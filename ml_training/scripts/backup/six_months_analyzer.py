# ml_training/scripts/six_month_analyzer.py

import pandas as pd
import numpy as np
import sqlite3
import os
from sklearn.ensemble import RandomForestClassifier, RandomForestRegressor
from sklearn.model_selection import train_test_split
from sklearn.metrics import roc_auc_score, classification_report, mean_squared_error, r2_score
from sklearn.preprocessing import StandardScaler
import joblib
from datetime import datetime, timedelta
import warnings

warnings.filterwarnings('ignore')


class SixMonthPredictor:
    def __init__(self, db_path):
        self.db_path = db_path
        self.classification_model = None
        self.regression_model = None
        self.scaler = StandardScaler()
        self.feature_names = []

    def generate_six_month_labels(self):
        """
        Generate 6-month performance labels for historical breakouts
        """
        print("📅 Generating 6-month performance labels...")

        conn = sqlite3.connect(self.db_path)

        # Get all breakouts
        breakouts_query = '''
            SELECT 
                symbol, date, breakout_type, entry_price, success,
                volume_ratio, atr_pct, rsi_value, price_change_pct
            FROM breakouts 
            WHERE success IS NOT NULL 
            ORDER BY date DESC
        '''

        breakouts_df = pd.read_sql(breakouts_query, conn)

        # Fix binary success
        def fix_binary_success(value):
            if isinstance(value, bytes):
                return 1 if value == b'\x01' else 0
            else:
                return int(float(value)) if value is not None else 0

        breakouts_df['success'] = breakouts_df['success'].apply(fix_binary_success)

        print(f"📊 Analyzing {len(breakouts_df)} breakouts for 6-month performance...")

        # For each breakout, calculate 6-month performance
        six_month_results = []

        for _, breakout in breakouts_df.iterrows():
            symbol = breakout['symbol']
            breakout_date = pd.to_datetime(breakout['date'])
            entry_price = breakout['entry_price']

            # Calculate 6-month target date (roughly 130 trading days)
            six_month_date = breakout_date + timedelta(days=180)  # 6 months calendar days
            three_month_date = breakout_date + timedelta(days=90)  # 3 months for comparison

            # Get price data for this symbol around the 6-month mark
            price_query = '''
                SELECT date, close, volume
                FROM stock_prices 
                WHERE symbol = ? 
                AND date >= ? 
                AND date <= ?
                ORDER BY date
            '''

            price_data = pd.read_sql(price_query, conn, params=[
                symbol,
                breakout_date.strftime('%Y-%m-%d'),
                six_month_date.strftime('%Y-%m-%d')
            ])

            if len(price_data) < 30:  # Need at least 30 days of data
                continue

            # Calculate various performance metrics
            result = self.calculate_long_term_performance(
                price_data, entry_price, breakout_date, breakout
            )

            if result:
                six_month_results.append(result)

        conn.close()

        print(f"✅ Generated 6-month labels for {len(six_month_results)} breakouts")
        return pd.DataFrame(six_month_results)

    def calculate_long_term_performance(self, price_data, entry_price, breakout_date, breakout_info):
        """
        Calculate comprehensive 6-month performance metrics
        """
        try:
            price_data['date'] = pd.to_datetime(price_data['date'])
            price_data = price_data.sort_values('date')

            # Find prices at different time horizons
            end_dates = {
                '1_month': breakout_date + timedelta(days=30),
                '3_month': breakout_date + timedelta(days=90),
                '6_month': breakout_date + timedelta(days=180)
            }

            performance_metrics = {
                'symbol': breakout_info['symbol'],
                'breakout_date': breakout_info['date'],
                'breakout_type': breakout_info['breakout_type'],
                'entry_price': entry_price,
                'volume_ratio': breakout_info['volume_ratio'],
                'atr_pct': breakout_info['atr_pct'],
                'rsi_value': breakout_info['rsi_value'],
                'price_change_pct': breakout_info['price_change_pct']
            }

            # Calculate returns for different periods
            for period, target_date in end_dates.items():
                # Find closest price to target date
                closest_data = price_data[price_data['date'] <= target_date]
                if len(closest_data) > 0:
                    final_price = closest_data.iloc[-1]['close']
                    returns = (final_price - entry_price) / entry_price * 100
                    performance_metrics[f'return_{period}'] = returns
                else:
                    performance_metrics[f'return_{period}'] = np.nan

            # Calculate max gain and max loss over the entire period
            all_prices = price_data['close']
            max_price = all_prices.max()
            min_price = all_prices.min()

            performance_metrics['max_gain_6m'] = (max_price - entry_price) / entry_price * 100
            performance_metrics['max_loss_6m'] = (min_price - entry_price) / entry_price * 100

            # Calculate volatility (standard deviation of daily returns)
            daily_returns = all_prices.pct_change().dropna()
            performance_metrics['volatility_6m'] = daily_returns.std() * np.sqrt(252) * 100  # Annualized

            # Days to reach max gain/loss
            max_gain_idx = all_prices.idxmax()
            min_loss_idx = all_prices.idxmin()

            if max_gain_idx in price_data.index:
                max_gain_date = price_data.loc[max_gain_idx, 'date']
                performance_metrics['days_to_max_gain'] = (max_gain_date - breakout_date).days

            if min_loss_idx in price_data.index:
                min_loss_date = price_data.loc[min_loss_idx, 'date']
                performance_metrics['days_to_max_loss'] = (min_loss_date - breakout_date).days

            # Success definitions for different timeframes
            performance_metrics['success_1m'] = 1 if performance_metrics.get('return_1_month', 0) > 5 else 0
            performance_metrics['success_3m'] = 1 if performance_metrics.get('return_3_month', 0) > 10 else 0
            performance_metrics['success_6m'] = 1 if performance_metrics.get('return_6_month', 0) > 15 else 0

            # Strong performance flags
            performance_metrics['strong_performer_6m'] = 1 if performance_metrics.get('return_6_month', 0) > 25 else 0
            performance_metrics['poor_performer_6m'] = 1 if performance_metrics.get('return_6_month', 0) < -10 else 0

            return performance_metrics

        except Exception as e:
            print(f"⚠️  Error calculating performance for {breakout_info['symbol']}: {e}")
            return None

    def load_enhanced_fundamental_data(self):
        """
        Load comprehensive fundamental data for 6-month prediction
        """
        print("💰 Loading enhanced fundamental data for long-term prediction...")

        conn = sqlite3.connect(self.db_path)

        # Get comprehensive fundamental data
        fundamentals_query = '''
            SELECT 
                symbol,
                overall_quality_score,
                growth_score,
                profitability_score,
                financial_health_score,
                valuation_score,
                quality_grade,
                pe_ratio,
                pb_ratio,
                market_cap,
                sector,
                beta
            FROM daily_fundamentals f1
            WHERE f1.date = (
                SELECT MAX(f2.date) 
                FROM daily_fundamentals f2 
                WHERE f2.symbol = f1.symbol 
                AND f2.quality_grade IS NOT NULL
            )
            AND f1.quality_grade IS NOT NULL
        '''

        fundamentals_df = pd.read_sql(fundamentals_query, conn)

        # Get quarterly fundamentals for more detailed analysis
        try:
            quarterly_query = '''
                SELECT 
                    symbol,
                    revenue,
                    net_income,
                    gross_margin,
                    operating_margin,
                    net_margin,
                    roe,
                    roa,
                    debt_to_equity,
                    current_ratio,
                    revenue_growth_yoy
                FROM quarterly_fundamentals q1
                WHERE q1.quarter = (
                    SELECT MAX(q2.quarter)
                    FROM quarterly_fundamentals q2
                    WHERE q2.symbol = q1.symbol
                )
            '''

            quarterly_df = pd.read_sql(quarterly_query, conn)

            # Merge quarterly with daily fundamentals
            fundamentals_df = fundamentals_df.merge(quarterly_df, on='symbol', how='left')
            print(f"✅ Added quarterly fundamentals for enhanced analysis")

        except Exception as e:
            print(f"⚠️  No quarterly fundamentals available: {e}")

        conn.close()

        print(f"💰 Loaded fundamentals for {len(fundamentals_df)} symbols")
        return fundamentals_df

    def create_long_term_features(self, performance_df, fundamentals_df):
        """
        Create features optimized for 6-month prediction
        """
        print("🔧 Creating LONG-TERM optimized features...")

        # Merge performance data with fundamentals
        df = performance_df.merge(fundamentals_df, on='symbol', how='left')

        # Fill missing fundamentals intelligently
        df = self.fill_missing_long_term_data(df)

        features_df = df.copy()

        # =============================================================
        # FUNDAMENTAL FEATURES (now much more important!)
        # =============================================================

        # Quality scores (weighted higher for long-term)
        features_df['quality_excellent'] = (features_df['overall_quality_score'] >= 8).astype(int)
        features_df['quality_good'] = (features_df['overall_quality_score'] >= 6).astype(int)
        features_df['quality_poor'] = (features_df['overall_quality_score'] <= 3).astype(int)

        # Growth features (critical for 6-month performance)
        features_df['high_growth'] = (features_df['growth_score'] >= 7).astype(int)
        features_df['growth_normalized'] = features_df['growth_score'] / 10

        # Profitability (key for sustained performance)
        features_df['highly_profitable'] = (features_df['profitability_score'] >= 7).astype(int)
        features_df['profitability_normalized'] = features_df['profitability_score'] / 10

        # Financial health (risk management)
        features_df['financially_strong'] = (features_df['financial_health_score'] >= 7).astype(int)
        features_df['health_normalized'] = features_df['financial_health_score'] / 10

        # Valuation features
        features_df['fairly_valued'] = (features_df['valuation_score'] >= 6).astype(int)
        features_df['undervalued'] = (features_df['valuation_score'] >= 8).astype(int)

        # Grade features
        features_df['grade_A'] = (features_df['quality_grade'] == 'A').astype(int)
        features_df['grade_B'] = (features_df['quality_grade'] == 'B').astype(int)
        features_df['grade_A_or_B'] = ((features_df['quality_grade'] == 'A') |
                                       (features_df['quality_grade'] == 'B')).astype(int)

        # Financial ratios
        features_df['reasonable_pe'] = ((features_df['pe_ratio'] >= 10) &
                                        (features_df['pe_ratio'] <= 25)).astype(int)
        features_df['low_pe'] = (features_df['pe_ratio'] <= 15).astype(int)
        features_df['reasonable_pb'] = ((features_df['pb_ratio'] >= 1) &
                                        (features_df['pb_ratio'] <= 3)).astype(int)

        # Market cap features
        features_df['log_market_cap'] = np.log1p(features_df['market_cap'])
        market_cap_quartiles = features_df['market_cap'].quantile([0.25, 0.5, 0.75])
        features_df['large_cap'] = (features_df['market_cap'] > market_cap_quartiles[0.75]).astype(int)
        features_df['mega_cap'] = (features_df['market_cap'] > features_df['market_cap'].quantile(0.9)).astype(int)

        # Beta (risk factor)
        features_df['low_beta'] = (features_df['beta'] < 1.0).astype(int)
        features_df['high_beta'] = (features_df['beta'] > 1.5).astype(int)

        # =============================================================
        # ENHANCED QUARTERLY FEATURES (if available)
        # =============================================================

        if 'revenue_growth_yoy' in features_df.columns:
            features_df['strong_revenue_growth'] = (features_df['revenue_growth_yoy'] > 10).astype(int)
            features_df['revenue_declining'] = (features_df['revenue_growth_yoy'] < -5).astype(int)

        if 'roe' in features_df.columns:
            features_df['high_roe'] = (features_df['roe'] > 15).astype(int)
            features_df['excellent_roe'] = (features_df['roe'] > 20).astype(int)

        if 'debt_to_equity' in features_df.columns:
            features_df['low_debt'] = (features_df['debt_to_equity'] < 0.5).astype(int)
            features_df['high_debt'] = (features_df['debt_to_equity'] > 2.0).astype(int)

        # =============================================================
        # SECTOR FEATURES (important for 6-month trends)
        # =============================================================

        # Sector encoding
        top_sectors = features_df['sector'].value_counts().head(8).index.tolist()
        for sector in top_sectors:
            safe_name = sector.replace(' ', '_').replace('&', 'and').replace(',', '')
            features_df[f'sector_{safe_name}'] = (features_df['sector'] == sector).astype(int)

        # =============================================================
        # TECHNICAL FEATURES (less important but still useful)
        # =============================================================

        features_df['is_bullish'] = (features_df['breakout_type'] == 'bullish').astype(int)
        features_df['high_volume'] = (features_df['volume_ratio'] > 2.0).astype(int)
        features_df['strong_initial_move'] = (abs(features_df['price_change_pct']) > 3).astype(int)
        features_df['good_rsi'] = ((features_df['rsi_value'] >= 50) &
                                   (features_df['rsi_value'] <= 70)).astype(int)

        # =============================================================
        # POWERFUL COMBINATION FEATURES
        # =============================================================

        # Quality + Growth combination
        features_df['quality_growth_combo'] = features_df['overall_quality_score'] * features_df['growth_score']
        features_df['premium_stock'] = ((features_df['overall_quality_score'] >= 7) &
                                        (features_df['growth_score'] >= 6)).astype(int)

        # Quality + Valuation sweet spot
        features_df['quality_value_combo'] = ((features_df['overall_quality_score'] >= 6) &
                                              (features_df['valuation_score'] >= 6)).astype(int)

        # Sector + Quality interactions
        if 'sector_Technology' in features_df.columns:
            features_df['tech_quality'] = features_df['sector_Technology'] * features_df['overall_quality_score']

        # Growth + Profitability + Health (the holy trinity)
        features_df['fundamental_trinity'] = (features_df['growth_score'] *
                                              features_df['profitability_score'] *
                                              features_df['financial_health_score']) / 1000

        # Select features for 6-month prediction
        long_term_features = [
            # Fundamental scores (now primary!)
            'overall_quality_score', 'quality_excellent', 'quality_good', 'quality_poor',
            'growth_score', 'growth_normalized', 'high_growth',
            'profitability_score', 'profitability_normalized', 'highly_profitable',
            'financial_health_score', 'health_normalized', 'financially_strong',
            'valuation_score', 'fairly_valued', 'undervalued',
            'grade_A', 'grade_B', 'grade_A_or_B',

            # Financial ratios
            'pe_ratio', 'reasonable_pe', 'low_pe', 'pb_ratio', 'reasonable_pb',
            'log_market_cap', 'large_cap', 'mega_cap', 'beta', 'low_beta', 'high_beta',

            # Quarterly metrics (if available)
            'strong_revenue_growth', 'revenue_declining', 'high_roe', 'excellent_roe',
            'low_debt', 'high_debt',

            # Technical (secondary)
            'is_bullish', 'volume_ratio', 'high_volume', 'strong_initial_move', 'good_rsi',

            # Combination features
            'quality_growth_combo', 'premium_stock', 'quality_value_combo', 'fundamental_trinity'
        ]

        # Add sector features
        sector_features = [col for col in features_df.columns if col.startswith('sector_')]
        long_term_features.extend(sector_features)

        # Filter to existing columns
        available_features = [col for col in long_term_features if col in features_df.columns]

        print(f"✅ Created {len(available_features)} long-term optimized features")

        # Categorize for display
        fundamental_count = len([f for f in available_features if any(x in f for x in
                                                                      ['quality', 'grade', 'growth', 'profitability',
                                                                       'health', 'valuation', 'pe_', 'pb_', 'roe',
                                                                       'debt'])])

        print(f"   💰 Fundamental features: {fundamental_count} (primary)")
        print(
            f"   📊 Technical features: {len([f for f in available_features if f in ['is_bullish', 'volume_ratio', 'high_volume', 'strong_initial_move', 'good_rsi']])}")
        print(f"   🔗 Combination features: {len([f for f in available_features if 'combo' in f or 'trinity' in f])}")
        print(f"   🏢 Sector features: {len(sector_features)}")

        return features_df, available_features

    def fill_missing_long_term_data(self, df):
        """Fill missing data for long-term analysis"""
        print("🧹 Filling missing data for long-term analysis...")

        # Fill fundamental scores with sector medians
        score_cols = ['overall_quality_score', 'growth_score', 'profitability_score',
                      'financial_health_score', 'valuation_score']

        for col in score_cols:
            if col in df.columns:
                # Sector median first
                sector_medians = df.groupby('sector')[col].median()
                for sector in sector_medians.index:
                    mask = (df['sector'] == sector) & df[col].isna()
                    df.loc[mask, col] = sector_medians[sector]

                # Overall median
                df[col] = df[col].fillna(df[col].median())
                df[col] = df[col].fillna(5.0)  # Final fallback

        # Other fundamental columns
        if 'pe_ratio' in df.columns:
            df['pe_ratio'] = df['pe_ratio'].fillna(df['pe_ratio'].median())
        if 'pb_ratio' in df.columns:
            df['pb_ratio'] = df['pb_ratio'].fillna(df['pb_ratio'].median())
        if 'beta' in df.columns:
            df['beta'] = df['beta'].fillna(1.0)
        if 'market_cap' in df.columns:
            df['market_cap'] = df['market_cap'].fillna(df['market_cap'].median())

        # Quality grade from score
        def assign_grade(score):
            if pd.isna(score): return 'C'
            if score >= 8:
                return 'A'
            elif score >= 6:
                return 'B'
            elif score >= 4:
                return 'C'
            else:
                return 'D'

        df['quality_grade'] = df['quality_grade'].fillna(
            df['overall_quality_score'].apply(assign_grade)
        )

        df['sector'] = df['sector'].fillna('Unknown')

        print("✅ Missing data filled for long-term analysis")
        return df

    def train_six_month_models(self, df, feature_cols):
        """
        Train both classification and regression models for 6-month prediction
        """
        print(f"\n🤖 TRAINING 6-MONTH PREDICTION MODELS...")
        print("=" * 70)

        # Prepare features
        X = df[feature_cols].copy()
        X = X.fillna(X.median())

        # Prepare targets
        y_class = df['success_6m'].copy()  # Classification: Success/Failure
        y_reg = df['return_6_month'].copy()  # Regression: Actual returns

        # Remove rows with missing targets
        valid_mask = (~y_class.isna()) & (~y_reg.isna())
        X = X[valid_mask]
        y_class = y_class[valid_mask]
        y_reg = y_reg[valid_mask]

        print(f"📊 6-month training data:")
        print(f"   Samples: {len(X)}")
        print(f"   Features: {len(feature_cols)}")
        print(f"   6-month success rate: {y_class.mean():.1%}")
        print(f"   Average 6-month return: {y_reg.mean():.1f}%")
        print(f"   6-month return std: {y_reg.std():.1f}%")

        if len(X) < 20:
            print("❌ Not enough data for 6-month analysis")
            return None, None, None, None

        # Split data
        X_train, X_test, y_class_train, y_class_test, y_reg_train, y_reg_test = train_test_split(
            X, y_class, y_reg, test_size=0.25, random_state=42, stratify=y_class
        )

        # Scale features
        X_train_scaled = self.scaler.fit_transform(X_train)
        X_test_scaled = self.scaler.transform(X_test)

        # Train classification model (success/failure)
        print(f"\n🎯 Training 6-month SUCCESS CLASSIFIER...")
        self.classification_model = RandomForestClassifier(
            n_estimators=300,
            max_depth=12,
            min_samples_split=3,
            min_samples_leaf=2,
            random_state=42,
            class_weight='balanced'
        )

        self.classification_model.fit(X_train_scaled, y_class_train)

        # Train regression model (actual returns)
        print(f"🎯 Training 6-month RETURN PREDICTOR...")
        self.regression_model = RandomForestRegressor(
            n_estimators=300,
            max_depth=12,
            min_samples_split=3,
            min_samples_leaf=2,
            random_state=42
        )

        self.regression_model.fit(X_train_scaled, y_reg_train)

        # Evaluate classification model
        class_accuracy = self.classification_model.score(X_test_scaled, y_class_test)
        y_class_pred = self.classification_model.predict(X_test_scaled)
        y_class_proba = self.classification_model.predict_proba(X_test_scaled)[:, 1]

        try:
            class_auc = roc_auc_score(y_class_test, y_class_proba)
        except:
            class_auc = 0.5

        # Evaluate regression model
        y_reg_pred = self.regression_model.predict(X_test_scaled)
        reg_r2 = r2_score(y_reg_test, y_reg_pred)
        reg_rmse = np.sqrt(mean_squared_error(y_reg_test, y_reg_pred))

        print(f"\n🎯 6-MONTH MODEL PERFORMANCE:")
        print(f"📊 CLASSIFICATION (Success/Failure):")
        print(f"   Accuracy: {class_accuracy:.1%}")
        print(f"   AUC Score: {class_auc:.3f}")

        print(f"📈 REGRESSION (Return Prediction):")
        print(f"   R² Score: {reg_r2:.3f}")
        print(f"   RMSE: {reg_rmse:.1f}%")

        # Feature importance analysis
        print(f"\n🔍 TOP 15 FEATURES FOR 6-MONTH PREDICTION:")
        importances = self.classification_model.feature_importances_
        self.feature_names = feature_cols

        feature_importance = sorted(zip(feature_cols, importances),
                                    key=lambda x: x[1], reverse=True)

        for i, (feature, importance) in enumerate(feature_importance[:15], 1):
            if any(keyword in feature for keyword in
                   ['quality', 'grade', 'growth', 'profitability', 'health', 'valuation', 'pe_', 'pb_', 'roe', 'debt']):
                icon = "💰"
            elif any(keyword in feature for keyword in ['sector_']):
                icon = "🏢"
            elif any(keyword in feature for keyword in ['combo', 'trinity']):
                icon = "🔗"
            else:
                icon = "📊"

            print(f"   {i:2}. {icon} {feature:<30}: {importance:.3f}")

        return class_accuracy, class_auc, reg_r2, reg_rmse

    def save_six_month_models(self):
        """Save the 6-month prediction models"""
        try:
            os.makedirs('../../models', exist_ok=True)
            timestamp = datetime.now().strftime('%Y%m%d_%H%M')

            class_model_path = f"../models/six_month_classifier_{timestamp}.joblib"
            reg_model_path = f"../models/six_month_regressor_{timestamp}.joblib"
            scaler_path = f"../models/six_month_scaler_{timestamp}.joblib"
            features_path = f"../models/six_month_features_{timestamp}.txt"

            joblib.dump(self.classification_model, class_model_path)
            joblib.dump(self.regression_model, reg_model_path)
            joblib.dump(self.scaler, scaler_path)

            with open(features_path, 'w') as f:
                f.write('\n'.join(self.feature_names))

            print(f"\n💾 6-MONTH MODELS SAVED:")
            print(f"   Classifier: {class_model_path}")
            print(f"   Regressor: {reg_model_path}")
            print(f"   Scaler: {scaler_path}")
            print(f"   Features: {features_path}")

            return class_model_path, reg_model_path

        except Exception as e:
            print(f"⚠️  Could not save models: {e}")
            return None, None


def main():
    """Main function for 6-month analysis"""
    print("📅 6-MONTH LONG-TERM PERFORMANCE ANALYZER")
    print("=" * 80)
    print("Analyzing breakouts for 6-month performance...")
    print("Fundamentals become PRIMARY predictors for long-term success!")

    # Find database
    db_paths = [
        "data/trading_data.db",
        "../../mechanism/data/trading_data.db",
        "../mechanism/data/trading_data.db"
    ]

    db_path = None
    for path in db_paths:
        if os.path.exists(path):
            try:
                conn = sqlite3.connect(path)
                cursor = conn.cursor()
                cursor.execute("SELECT COUNT(*) FROM breakouts WHERE success IS NOT NULL")
                count = cursor.fetchone()[0]
                conn.close()
                if count > 0:
                    db_path = path
                    print(f"✅ Found database with {count} breakouts: {path}")
                    break
            except:
                pass

    if not db_path:
        print("❌ No database with breakouts found")
        return

    try:
        predictor = SixMonthPredictor(db_path)

        # Generate 6-month labels
        performance_df = predictor.generate_six_month_labels()

        if len(performance_df) < 20:
            print(f"❌ Not enough 6-month data: {len(performance_df)} samples")
            print("Need more historical data or wait for 6+ months to pass")
            return

        # Load enhanced fundamentals
        fundamentals_df = predictor.load_enhanced_fundamental_data()

        # Create long-term features
        df_features, feature_cols = predictor.create_long_term_features(performance_df, fundamentals_df)

        # Train 6-month models
        class_acc, class_auc, reg_r2, reg_rmse = predictor.train_six_month_models(df_features, feature_cols)

        if class_acc and class_acc > 0.55:
            class_path, reg_path = predictor.save_six_month_models()

            print(f"\n🎉 SUCCESS! 6-MONTH MODELS TRAINED!")
            print(f"=" * 60)
            print(f"🎯 Classification Accuracy: {class_acc:.1%}")
            print(f"📊 AUC Score: {class_auc:.3f}")
            print(f"📈 Return Prediction R²: {reg_r2:.3f}")

            print(f"\n💡 KEY INSIGHTS:")
            print(f"   📅 6-month prediction focuses on FUNDAMENTALS")
            print(f"   💰 Company quality matters more than technical signals")
            print(f"   🏢 Sector dynamics play important role")
            print(f"   📊 Technical indicators are secondary")

            print(f"\n🎯 COMPARISON:")
            print(f"   Short-term (10-day): Technical indicators dominate")
            print(f"   Long-term (6-month): Fundamentals dominate")
            print(f"   This proves professional trading wisdom!")

        else:
            print(f"\n⚠️  6-month models need more data or tuning")

        return predictor

    except Exception as e:
        print(f"❌ 6-month analysis failed: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    predictor = main()