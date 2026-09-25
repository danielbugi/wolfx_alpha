# ml_training/evaluation/performance_tracker.py

import pandas as pd
import numpy as np
import psycopg2
import os
import json
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
import warnings
from dotenv import load_dotenv

warnings.filterwarnings('ignore')

# This script reads DB_* directly via os.getenv() and, unlike
# mechanism/shared/config.py, never loaded .env itself — it silently fell
# back to the literal default password string when run standalone. Fixed
# 2026-09-14 (see MILESTONES.md).
load_dotenv()

class MLPerformanceTracker:
    """
    Track and monitor ML model performance over time
    Provides insights for model improvement and retraining decisions
    """
    
    def __init__(self):
        self.db_config = {
            'host': os.getenv('DB_HOST', 'localhost'),
            'port': os.getenv('DB_PORT', '5432'),
            'database': os.getenv('DB_NAME', 'trading_production'),
            'user': os.getenv('DB_USER', 'trading_user'),
            'password': os.getenv('DB_PASSWORD', 'your_password')
        }
        
    def get_db_connection(self):
        """Create PostgreSQL connection"""
        try:
            conn = psycopg2.connect(**self.db_config)
            return conn
        except Exception as e:
            print(f"❌ Database connection failed: {e}")
            return None
    
    def setup_performance_tables(self):
        """Verify ml_predictions/ml_prediction_outcomes/ml_performance_metrics exist.

        These tables are owned by mechanism/add_ml_prediction_tracking_tables.sql (migration #17)
        -- not by this method. Until 2026-09-25 this method carried its own CREATE TABLE IF NOT
        EXISTS DDL for the same three tables, which had silently diverged from the tracked
        migration (breakout_type VARCHAR(10) here vs VARCHAR(20) in the migration -- the migration's
        width is the one verified against real production data via a direct, documented inspection;
        this method's was stale and would have been too narrow for a real value like
        "bullish_breakout"). That DDL was never actually reachable outside a manual `python
        performance_tracker.py` run (nothing in the live pipeline calls this method), so removing it
        changes no current behavior -- it only removes a latent footgun and a second, driftable
        source of truth for these tables' schema. Per the Phase 3/4A audit: migrations define
        schema, application code consumes it.
        """
        conn = self.get_db_connection()
        if not conn:
            return False

        cursor = conn.cursor()
        try:
            cursor.execute("""
                SELECT count(*) FROM information_schema.tables
                WHERE table_schema = 'public'
                  AND table_name IN ('ml_predictions', 'ml_prediction_outcomes', 'ml_performance_metrics')
            """)
            found = cursor.fetchone()[0]
            if found < 3:
                print(f"❌ Only {found}/3 expected tables exist -- apply "
                      f"mechanism/add_ml_prediction_tracking_tables.sql before using this tracker")
                return False
            print("✅ ml_predictions / ml_prediction_outcomes / ml_performance_metrics all present")
            return True
        except Exception as e:
            print(f"❌ Error checking performance tables: {e}")
            return False
        finally:
            cursor.close()
            conn.close()
    
    def record_predictions(self, predictions: List[Dict], model_version: str = "unknown"):
        """Record ML predictions for future performance evaluation"""
        print(f"Recording {len(predictions)} ML predictions...")
        
        conn = self.get_db_connection()
        if not conn:
            return False
            
        cursor = conn.cursor()
        
        try:
            insert_query = """
                INSERT INTO ml_predictions 
                (symbol, prediction_date, breakout_type, entry_price, ml_probability,
                 ml_confidence, ml_recommendation, ml_risk_score, model_version)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (symbol, prediction_date) DO UPDATE SET
                    ml_probability = EXCLUDED.ml_probability,
                    ml_confidence = EXCLUDED.ml_confidence,
                    ml_recommendation = EXCLUDED.ml_recommendation,
                    ml_risk_score = EXCLUDED.ml_risk_score,
                    model_version = EXCLUDED.model_version
            """
            
            recorded_count = 0
            for pred in predictions:
                try:
                    cursor.execute(insert_query, (
                        pred.get('symbol'),
                        pred.get('date', datetime.now().date()),
                        pred.get('type', 'unknown'),
                        pred.get('current_price', 0),
                        pred.get('ml_momentum_probability', 0) / 100,  # Convert to 0-1
                        pred.get('ml_confidence_level', 'unknown'),
                        pred.get('ml_trade_recommendation', 'unknown'),
                        pred.get('ml_risk_score', 50),
                        model_version
                    ))
                    # Commit per row rather than once at the end. Postgres
                    # aborts the whole transaction on the first failed
                    # statement (e.g. a value too long for a column) --
                    # catching the exception per row without rolling back
                    # left every subsequent execute() in this same
                    # transaction failing with "current transaction is
                    # aborted, commands ignored until end of transaction
                    # block", silently discarding an entire batch's worth
                    # of predictions after just one bad row (confirmed
                    # 2026-09-14: 217 signals in, 0 recorded). A single
                    # rollback() at the end would also have undone every
                    # prior successful row in the same batch, so the fix is
                    # commit-as-you-go with a per-row rollback on failure.
                    conn.commit()
                    recorded_count += 1
                except Exception as e:
                    conn.rollback()
                    print(f"⚠️ Error recording prediction for {pred.get('symbol')}: {e}")

            print(f"✅ Recorded {recorded_count} ML predictions")
            return True
            
        except Exception as e:
            print(f"❌ Error recording predictions: {e}")
            conn.rollback()
            return False
        finally:
            cursor.close()
            conn.close()
    
    def evaluate_predictions(self, days_back: int = 30, evaluation_period: int = 10):
        """Evaluate ML predictions against actual outcomes"""
        print(f"Evaluating predictions from {days_back} days ago...")
        
        conn = self.get_db_connection()
        if not conn:
            return False
            
        try:
            # Get predictions that need evaluation
            predictions_query = """
                SELECT p.id, p.symbol, p.prediction_date, p.breakout_type, 
                       p.entry_price, p.ml_probability, p.ml_confidence,
                       p.model_version
                FROM ml_predictions p
                LEFT JOIN ml_prediction_outcomes o ON p.id = o.prediction_id
                WHERE p.prediction_date >= CURRENT_DATE - INTERVAL '%s days'
                    AND p.prediction_date <= CURRENT_DATE - INTERVAL '%s days'
                    AND o.id IS NULL
                ORDER BY p.prediction_date DESC
            """
            
            predictions_df = pd.read_sql(predictions_query % (days_back + evaluation_period, days_back), conn)
            
            if len(predictions_df) == 0:
                print("No predictions to evaluate")
                return True
            
            print(f"Evaluating {len(predictions_df)} predictions...")
            
            # Evaluate each prediction
            cursor = conn.cursor()
            evaluated_count = 0
            
            for _, pred in predictions_df.iterrows():
                outcome = self.calculate_prediction_outcome(
                    pred['symbol'], 
                    pred['prediction_date'], 
                    pred['entry_price'],
                    pred['breakout_type'],
                    evaluation_period
                )
                
                if outcome:
                    # Record outcome
                    insert_outcome_query = """
                        INSERT INTO ml_prediction_outcomes 
                        (prediction_id, symbol, prediction_date, evaluation_date, days_elapsed,
                         actual_return_pct, max_gain_pct, max_loss_pct, momentum_achieved, momentum_score)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """
                    
                    cursor.execute(insert_outcome_query, (
                        pred['id'],
                        pred['symbol'],
                        pred['prediction_date'],
                        datetime.now().date(),
                        evaluation_period,
                        outcome['actual_return_pct'],
                        outcome['max_gain_pct'],
                        outcome['max_loss_pct'],
                        outcome['momentum_achieved'],
                        outcome['momentum_score']
                    ))
                    evaluated_count += 1
            
            conn.commit()
            print(f"✅ Evaluated {evaluated_count} predictions")
            
            # Calculate and store performance metrics
            self.calculate_performance_metrics()
            
            return True
            
        except Exception as e:
            print(f"❌ Error evaluating predictions: {e}")
            conn.rollback()
            return False
        finally:
            conn.close()
    
    def calculate_prediction_outcome(self, symbol: str, prediction_date: str, 
                                   entry_price: float, breakout_type: str, 
                                   evaluation_period: int) -> Optional[Dict]:
        """Calculate actual outcome for a prediction"""
        conn = self.get_db_connection()
        if not conn:
            return None
            
        try:
            # Get price data for evaluation period
            price_query = """
                SELECT date, close, high, low
                FROM stock_prices
                WHERE symbol = %s 
                    AND date > %s
                    AND date <= %s + INTERVAL '%s days'
                ORDER BY date
            """
            
            price_df = pd.read_sql(price_query, conn, params=[
                symbol, prediction_date, prediction_date, evaluation_period
            ])
            
            if len(price_df) < 3:  # Need minimum data
                return None
            
            prices = price_df['close'].values
            highs = price_df['high'].values
            lows = price_df['low'].values
            
            # Calculate returns
            final_price = prices[-1]
            actual_return_pct = (final_price - entry_price) / entry_price * 100
            
            # Calculate max gain and loss
            max_price = np.max(highs)
            min_price = np.min(lows)
            max_gain_pct = (max_price - entry_price) / entry_price * 100
            max_loss_pct = (min_price - entry_price) / entry_price * 100
            
            # Determine if momentum was achieved
            is_bullish = breakout_type in ['bullish_breakout', 'near_bullish']
            
            if is_bullish:
                momentum_achieved = actual_return_pct > 2.0  # 2%+ gain
                momentum_score = min(100, max(0, actual_return_pct * 10))
            else:
                momentum_achieved = actual_return_pct < -2.0  # 2%+ loss
                momentum_score = min(100, max(0, abs(actual_return_pct) * 10))
            
            return {
                'actual_return_pct': round(actual_return_pct, 2),
                'max_gain_pct': round(max_gain_pct, 2),
                'max_loss_pct': round(max_loss_pct, 2),
                'momentum_achieved': momentum_achieved,
                'momentum_score': int(momentum_score)
            }
            
        except Exception as e:
            print(f"⚠️ Error calculating outcome for {symbol}: {e}")
            return None
        finally:
            conn.close()
    
    def calculate_performance_metrics(self):
        """Calculate and store overall performance metrics"""
        print("Calculating performance metrics...")
        
        conn = self.get_db_connection()
        if not conn:
            return False
            
        try:
            # Get recent predictions with outcomes
            metrics_query = """
                SELECT p.model_version, p.ml_probability, p.ml_confidence,
                       o.momentum_achieved, o.actual_return_pct
                FROM ml_predictions p
                JOIN ml_prediction_outcomes o ON p.id = o.prediction_id
                WHERE o.evaluation_date >= CURRENT_DATE - INTERVAL '30 days'
            """
            
            df = pd.read_sql(metrics_query, conn)
            
            if len(df) == 0:
                print("No data for performance calculation")
                return True
            
            # Group by model version
            for model_version, group in df.groupby('model_version'):
                metrics = self.compute_metrics(group)
                
                # Store metrics
                cursor = conn.cursor()
                insert_metrics_query = """
                    INSERT INTO ml_performance_metrics 
                    (metric_date, model_version, total_predictions, correct_predictions,
                     accuracy, precision_high_prob, recall_high_prob, avg_return_predicted_high,
                     avg_return_predicted_low, sharpe_ratio, max_drawdown)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """
                
                cursor.execute(insert_metrics_query, (
                    datetime.now().date(),
                    model_version,
                    metrics['total_predictions'],
                    metrics['correct_predictions'],
                    metrics['accuracy'],
                    metrics['precision_high_prob'],
                    metrics['recall_high_prob'],
                    metrics['avg_return_predicted_high'],
                    metrics['avg_return_predicted_low'],
                    metrics['sharpe_ratio'],
                    metrics['max_drawdown']
                ))
            
            conn.commit()
            print("✅ Performance metrics calculated and stored")
            return True
            
        except Exception as e:
            print(f"❌ Error calculating performance metrics: {e}")
            conn.rollback()
            return False
        finally:
            conn.close()
    
    def compute_metrics(self, df: pd.DataFrame) -> Dict:
        """Compute performance metrics from prediction results"""
        total_predictions = len(df)
        correct_predictions = df['momentum_achieved'].sum()
        accuracy = correct_predictions / total_predictions if total_predictions > 0 else 0
        
        # High probability predictions (>70%)
        high_prob_mask = df['ml_probability'] > 0.7
        high_prob_df = df[high_prob_mask]
        
        if len(high_prob_df) > 0:
            precision_high_prob = high_prob_df['momentum_achieved'].mean()
            avg_return_predicted_high = high_prob_df['actual_return_pct'].mean()
        else:
            precision_high_prob = 0
            avg_return_predicted_high = 0
        
        # Low probability predictions (<50%)
        low_prob_mask = df['ml_probability'] < 0.5
        low_prob_df = df[low_prob_mask]
        
        if len(low_prob_df) > 0:
            avg_return_predicted_low = low_prob_df['actual_return_pct'].mean()
        else:
            avg_return_predicted_low = 0
        
        # Recall for high probability
        actual_momentum = df['momentum_achieved'] == True
        if actual_momentum.sum() > 0:
            recall_high_prob = (high_prob_mask & actual_momentum).sum() / actual_momentum.sum()
        else:
            recall_high_prob = 0
        
        # Sharpe ratio (simplified)
        returns = df['actual_return_pct'].values
        sharpe_ratio = np.mean(returns) / np.std(returns) if np.std(returns) > 0 else 0
        
        # Max drawdown
        cumulative_returns = np.cumsum(returns)
        running_max = np.maximum.accumulate(cumulative_returns)
        drawdown = cumulative_returns - running_max
        max_drawdown = np.min(drawdown)
        
        return {
            'total_predictions': total_predictions,
            'correct_predictions': correct_predictions,
            'accuracy': round(accuracy, 3),
            'precision_high_prob': round(precision_high_prob, 3),
            'recall_high_prob': round(recall_high_prob, 3),
            'avg_return_predicted_high': round(avg_return_predicted_high, 2),
            'avg_return_predicted_low': round(avg_return_predicted_low, 2),
            'sharpe_ratio': round(sharpe_ratio, 3),
            'max_drawdown': round(max_drawdown, 2)
        }
    
    def generate_performance_report(self, days_back: int = 30):
        """Generate comprehensive performance report"""
        print(f"GENERATING PERFORMANCE REPORT ({days_back} days)")
        print("=" * 60)
        
        conn = self.get_db_connection()
        if not conn:
            return
            
        try:
            # Recent performance metrics
            metrics_query = """
                SELECT * FROM ml_performance_metrics
                WHERE metric_date >= CURRENT_DATE - INTERVAL '%s days'
                ORDER BY metric_date DESC
                LIMIT 1
            """
            
            metrics_df = pd.read_sql(metrics_query % days_back, conn)
            
            if len(metrics_df) > 0:
                metrics = metrics_df.iloc[0]
                
                print(f"OVERALL PERFORMANCE:")
                print(f"   Model Version: {metrics['model_version']}")
                print(f"   Total Predictions: {metrics['total_predictions']}")
                print(f"   Accuracy: {metrics['accuracy']:.1%}")
                print(f"   Precision (High Prob): {metrics['precision_high_prob']:.1%}")
                print(f"   Recall (High Prob): {metrics['recall_high_prob']:.1%}")
                print(f"   Sharpe Ratio: {metrics['sharpe_ratio']:.3f}")
                print(f"   Max Drawdown: {metrics['max_drawdown']:.2f}%")
                
                print(f"\nRETURN ANALYSIS:")
                print(f"   Avg Return (High Prob Predictions): {metrics['avg_return_predicted_high']:.2f}%")
                print(f"   Avg Return (Low Prob Predictions): {metrics['avg_return_predicted_low']:.2f}%")
            
            # Recent predictions breakdown
            breakdown_query = """
                SELECT p.ml_confidence, 
                       COUNT(*) as count,
                       AVG(o.actual_return_pct) as avg_return,
                       AVG(CASE WHEN o.momentum_achieved THEN 1.0 ELSE 0.0 END) as success_rate
                FROM ml_predictions p
                JOIN ml_prediction_outcomes o ON p.id = o.prediction_id
                WHERE o.evaluation_date >= CURRENT_DATE - INTERVAL '%s days'
                GROUP BY p.ml_confidence
                ORDER BY p.ml_confidence DESC
            """
            
            breakdown_df = pd.read_sql(breakdown_query % days_back, conn)
            
            if len(breakdown_df) > 0:
                print(f"\nCONFIDENCE LEVEL BREAKDOWN:")
                for _, row in breakdown_df.iterrows():
                    print(f"   {row['ml_confidence']}: {row['count']} predictions, "
                          f"{row['success_rate']:.1%} success, {row['avg_return']:.2f}% avg return")
            
            # Top performing predictions
            top_query = """
                SELECT p.symbol, p.prediction_date, p.ml_probability, 
                       o.actual_return_pct, o.momentum_achieved
                FROM ml_predictions p
                JOIN ml_prediction_outcomes o ON p.id = o.prediction_id
                WHERE o.evaluation_date >= CURRENT_DATE - INTERVAL '%s days'
                ORDER BY o.actual_return_pct DESC
                LIMIT 10
            """
            
            top_df = pd.read_sql(top_query % days_back, conn)
            
            if len(top_df) > 0:
                print(f"\nTOP 10 PERFORMING PREDICTIONS:")
                for _, row in top_df.iterrows():
                    print(f"   {row['symbol']} ({row['prediction_date']}): "
                          f"{row['ml_probability']:.1%} prob → {row['actual_return_pct']:.2f}% return")
            
            # Model improvement suggestions
            self.suggest_improvements(metrics_df, breakdown_df)
            
        except Exception as e:
            print(f"❌ Error generating performance report: {e}")
        finally:
            conn.close()
    
    def suggest_improvements(self, metrics_df: pd.DataFrame, breakdown_df: pd.DataFrame):
        """Suggest model improvements based on performance analysis"""
        print(f"\nMODEL IMPROVEMENT SUGGESTIONS:")
        
        if len(metrics_df) == 0:
            print("   No recent performance data available")
            return
        
        metrics = metrics_df.iloc[0]
        
        suggestions = []
        
        # Accuracy suggestions
        if metrics['accuracy'] < 0.6:
            suggestions.append("Low accuracy - consider more training data or feature engineering")
        elif metrics['accuracy'] > 0.8:
            suggestions.append("High accuracy - model performing well")
        
        # Precision/Recall suggestions
        if metrics['precision_high_prob'] < 0.7:
            suggestions.append("Low precision for high probability predictions - adjust threshold")
        
        if metrics['recall_high_prob'] < 0.5:
            suggestions.append("Low recall - missing many actual momentum opportunities")
        
        # Return suggestions
        if metrics['avg_return_predicted_high'] < 2.0:
            suggestions.append("Low returns on high probability predictions - review momentum definition")
        
        if metrics['sharpe_ratio'] < 0.5:
            suggestions.append("Low Sharpe ratio - high volatility relative to returns")
        
        # Print suggestions
        for i, suggestion in enumerate(suggestions, 1):
            print(f"   {i}. {suggestion}")
        
        if not suggestions:
            print("   Model performance is good - continue monitoring")

def main():
    """Main function for testing performance tracking"""
    print("ML PERFORMANCE TRACKER")
    print("=" * 50)
    
    tracker = MLPerformanceTracker()
    
    # Setup tables
    tracker.setup_performance_tables()
    
    # Evaluate recent predictions
    tracker.evaluate_predictions(days_back=30)
    
    # Generate performance report
    tracker.generate_performance_report()

if __name__ == "__main__":
    main()
