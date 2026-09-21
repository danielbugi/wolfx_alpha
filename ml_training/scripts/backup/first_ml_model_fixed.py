# ml_training/scripts/first_ml_model_fixed.py

import pandas as pd
import numpy as np
import sqlite3
import os
from sklearn.ensemble import RandomForestClassifier
from sklearn.model_selection import train_test_split, cross_val_score
from sklearn.metrics import classification_report, confusion_matrix, roc_auc_score
from sklearn.preprocessing import StandardScaler
import joblib
from datetime import datetime
import warnings

warnings.filterwarnings('ignore')


def find_database():
    """
    Find the correct database path regardless of where script is run
    """
    possible_paths = [
        'data/trading_data.db',  # If run from project root
        '../data/trading_data.db',  # If run from ml_training/scripts
        '../../data/trading_data.db',  # If nested deeper
        '../../mechanism/data/trading_data.db',  # Alternative location
        '../../../mechanism/data/trading_data.db'  # Another alternative
    ]

    for path in possible_paths:
        if os.path.exists(path):
            print(f"✅ Found database: {path}")
            return path

    print("❌ Database not found. Looking in current directory...")
    print(f"Current directory: {os.getcwd()}")
    print("Files in current directory:")
    for file in os.listdir('..'):
        print(f"  {file}")

    return None


def check_database_tables(db_path):
    """
    Check what tables exist in the database
    """
    print(f"\n🔍 Checking database: {db_path}")

    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()

        # Get all table names
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
        tables = cursor.fetchall()

        print(f"📊 Tables found in database:")
        for table in tables:
            table_name = table[0]
            cursor.execute(f"SELECT COUNT(*) FROM {table_name}")
            count = cursor.fetchone()[0]
            print(f"   {table_name}: {count} records")

        # Check specifically for breakouts
        if 'breakouts' in [t[0] for t in tables]:
            cursor.execute("SELECT COUNT(*) FROM breakouts WHERE success IS NOT NULL")
            labeled_breakouts = cursor.fetchone()[0]
            print(f"\n✅ Breakouts table found with {labeled_breakouts} labeled examples!")
            conn.close()
            return True
        else:
            print(f"\n❌ No 'breakouts' table found!")
            print(f"Available tables: {[t[0] for t in tables]}")
            conn.close()
            return False

    except Exception as e:
        print(f"❌ Error checking database: {e}")
        return False


class BreakoutPredictor:
    def __init__(self, db_path=None):
        """
        AI Breakout Predictor with automatic database detection
        """
        if db_path is None:
            self.db_path = find_database()
        else:
            self.db_path = db_path

        if self.db_path is None:
            raise FileNotFoundError("Could not find trading_data.db")

        # Check if breakouts table exists
        if not check_database_tables(self.db_path):
            raise ValueError("Breakouts table not found. Run generate_historical_breakouts.py first!")

        self.model = None
        self.scaler = StandardScaler()
        self.feature_names = []

    def load_training_data(self):
        """
        Load breakout data with simplified query to avoid missing columns
        """
        print("🔍 Loading breakout data from database...")

        conn = sqlite3.connect(self.db_path)

        # Simplified query - only use columns we know exist
        query = '''
            SELECT 
                b.symbol,
                b.date,
                b.breakout_type,
                b.entry_price,
                b.success,
                b.volume_ratio,
                b.atr_pct,
                b.rsi_value,
                b.price_change_pct,
                COALESCE(b.max_gain_10d, 0) as max_gain_10d,
                COALESCE(b.max_loss_10d, 0) as max_loss_10d,
                COALESCE(b.days_to_peak, 1) as days_to_peak
            FROM breakouts b
            WHERE b.success IS NOT NULL
            ORDER BY b.date DESC
        '''

        df = pd.read_sql(query, conn)

        # Add technical indicators if available
        try:
            tech_query = '''
                SELECT DISTINCT
                    t.symbol,
                    t.date,
                    t.rsi_14,
                    t.volume_ratio as tech_volume_ratio,
                    t.price_position,
                    COALESCE(t.sma_10, 0) as sma_10,
                    COALESCE(t.sma_20, 0) as sma_20
                FROM technical_indicators t
                WHERE t.rsi_14 IS NOT NULL
            '''
            tech_df = pd.read_sql(tech_query, conn)

            # Merge with breakouts
            df = df.merge(tech_df, on=['symbol', 'date'], how='left')
            print(f"✅ Added technical indicators for {len(tech_df)} records")

        except Exception as e:
            print(f"⚠️  Could not load technical indicators: {e}")

        # Add fundamentals if available
        try:
            fund_query = '''
                SELECT DISTINCT
                    f.symbol,
                    f.date,
                    COALESCE(f.overall_quality_score, 5) as overall_quality_score,
                    COALESCE(f.growth_score, 5) as growth_score,
                    COALESCE(f.profitability_score, 5) as profitability_score,
                    COALESCE(f.financial_health_score, 5) as financial_health_score,
                    f.quality_grade,
                    f.sector
                FROM daily_fundamentals f
                WHERE f.quality_grade IS NOT NULL
            '''
            fund_df = pd.read_sql(fund_query, conn)

            # Merge with breakouts
            df = df.merge(fund_df, on=['symbol', 'date'], how='left')
            print(f"✅ Added fundamentals for {len(fund_df)} records")

        except Exception as e:
            print(f"⚠️  Could not load fundamentals: {e}")

        conn.close()

        # Fill missing values with defaults
        df = df.fillna({
            'rsi_14': 50,
            'tech_volume_ratio': 1.0,
            'price_position': 50,
            'sma_10': 0,
            'sma_20': 0,
            'overall_quality_score': 5,
            'growth_score': 5,
            'profitability_score': 5,
            'financial_health_score': 5,
            'quality_grade': 'C',
            'sector': 'Unknown'
        })

        print(f"✅ Loaded {len(df)} breakouts with complete data")

        # Basic info
        print(f"📊 Breakout Distribution:")
        bullish_count = len(df[df['breakout_type'] == 'bullish'])
        bearish_count = len(df[df['breakout_type'] == 'bearish'])
        success_rate = df['success'].mean() * 100

        print(f"   Bullish: {bullish_count}")
        print(f"   Bearish: {bearish_count}")
        print(f"   Success rate: {success_rate:.1f}%")

        return df

    def engineer_features(self, df):
        """
        Create ML features from available data
        """
        print("🔧 Engineering features...")

        features_df = df.copy()

        # 1. BASIC FEATURES
        features_df['is_bullish'] = (features_df['breakout_type'] == 'bullish').astype(int)

        # 2. TECHNICAL FEATURES
        features_df['volume_strength'] = np.log1p(features_df['volume_ratio'])
        features_df['price_momentum'] = features_df['price_change_pct']
        features_df['rsi_deviation'] = abs(features_df['rsi_14'] - 50)
        features_df['rsi_normalized'] = features_df['rsi_14'] / 100

        # 3. FUNDAMENTAL FEATURES
        features_df['quality_score_norm'] = features_df['overall_quality_score'] / 10
        features_df['growth_norm'] = features_df['growth_score'] / 10
        features_df['profitability_norm'] = features_df['profitability_score'] / 10
        features_df['health_norm'] = features_df['financial_health_score'] / 10

        # 4. INTERACTION FEATURES
        features_df['volume_rsi'] = features_df['volume_ratio'] * features_df['rsi_normalized']
        features_df['quality_momentum'] = features_df['overall_quality_score'] * abs(features_df['price_change_pct'])
        features_df['risk_reward'] = features_df['overall_quality_score'] / (features_df['atr_pct'] + 1)

        # 5. GRADE ENCODING
        grade_mapping = {'A': 4, 'B': 3, 'C': 2, 'D': 1}
        features_df['quality_grade_numeric'] = features_df['quality_grade'].map(grade_mapping).fillna(2)

        # 6. SECTOR ENCODING (simplified)
        # Get top 5 sectors, others become 'Other'
        top_sectors = features_df['sector'].value_counts().head(5).index.tolist()
        features_df['sector_clean'] = features_df['sector'].apply(
            lambda x: x if x in top_sectors else 'Other'
        )

        # One-hot encode sectors
        sector_dummies = pd.get_dummies(features_df['sector_clean'], prefix='sector')
        features_df = pd.concat([features_df, sector_dummies], axis=1)

        print(f"✅ Created features for {len(features_df)} records")

        return features_df

    def prepare_features_and_target(self, df):
        """
        Select features for ML training
        """
        print("📊 Preparing ML features...")

        # Select numeric features only
        feature_columns = [
            'is_bullish',
            'volume_ratio',
            'atr_pct',
            'rsi_14',
            'price_change_pct',
            'volume_strength',
            'price_momentum',
            'rsi_deviation',
            'rsi_normalized',
            'overall_quality_score',
            'growth_score',
            'profitability_score',
            'financial_health_score',
            'quality_score_norm',
            'growth_norm',
            'profitability_norm',
            'health_norm',
            'volume_rsi',
            'quality_momentum',
            'risk_reward',
            'quality_grade_numeric'
        ]

        # Add sector dummies if they exist
        sector_cols = [col for col in df.columns if col.startswith('sector_')]
        feature_columns.extend(sector_cols)

        # Filter to columns that actually exist
        available_features = [col for col in feature_columns if col in df.columns]

        X = df[available_features].copy()
        y = df['success'].astype(int)

        # Handle any remaining NaN values
        X = X.fillna(X.median())

        self.feature_names = X.columns.tolist()

        print(f"✅ Using {len(self.feature_names)} features")
        print(f"📈 Target: {y.mean() * 100:.1f}% successful breakouts")

        return X, y

    def train_model(self, X, y):
        """
        Train Random Forest model
        """
        print("🤖 Training AI model...")

        if len(X) < 30:
            print("❌ Not enough data for reliable training (need 30+)")
            return 0, 0

        # Split data
        test_size = min(0.3, max(0.15, 20 / len(X)))  # Adaptive test size
        X_train, X_test, y_train, y_test = train_test_split(
            X, y, test_size=test_size, random_state=42, stratify=y
        )

        print(f"📊 Training: {len(X_train)}, Testing: {len(X_test)}")

        # Scale features
        X_train_scaled = self.scaler.fit_transform(X_train)
        X_test_scaled = self.scaler.transform(X_test)

        # Train model
        self.model = RandomForestClassifier(
            n_estimators=100,
            max_depth=8,
            min_samples_split=5,
            min_samples_leaf=3,
            random_state=42,
            class_weight='balanced'
        )

        self.model.fit(X_train_scaled, y_train)

        # Evaluate
        y_pred = self.model.predict(X_test_scaled)
        y_prob = self.model.predict_proba(X_test_scaled)[:, 1]

        accuracy = self.model.score(X_test_scaled, y_test)

        try:
            auc_score = roc_auc_score(y_test, y_prob)
        except:
            auc_score = 0.5

        print(f"\n🎯 MODEL PERFORMANCE:")
        print(f"   Accuracy: {accuracy:.1%}")
        print(f"   AUC Score: {auc_score:.3f}")

        # Feature importance
        if len(self.feature_names) > 0:
            print(f"\n🔍 TOP 10 IMPORTANT FEATURES:")
            importances = self.model.feature_importances_
            feature_imp = sorted(zip(self.feature_names, importances),
                                 key=lambda x: x[1], reverse=True)

            for i, (feature, importance) in enumerate(feature_imp[:10], 1):
                print(f"   {i:2}. {feature:<25}: {importance:.3f}")

        return accuracy, auc_score

    def save_model(self):
        """
        Save the trained model
        """
        print("\n💾 Saving model...")

        os.makedirs('../../models', exist_ok=True)

        timestamp = datetime.now().strftime('%Y%m%d_%H%M')
        model_path = f"../models/breakout_predictor_{timestamp}.joblib"
        scaler_path = f"../models/scaler_{timestamp}.joblib"

        joblib.dump(self.model, model_path)
        joblib.dump(self.scaler, scaler_path)

        print(f"✅ Model saved: {model_path}")
        return model_path


def main():
    """
    Main training function with error handling
    """
    print("🚀 AI BREAKOUT PREDICTOR")
    print("=" * 50)

    try:
        # Initialize predictor
        predictor = BreakoutPredictor()

        # Load data
        df = predictor.load_training_data()

        if len(df) < 20:
            print(f"❌ Not enough breakout data ({len(df)} found, need 20+)")
            print("Run generate_historical_breakouts.py first!")
            return

        # Engineer features
        df_features = predictor.engineer_features(df)

        # Prepare for ML
        X, y = predictor.prepare_features_and_target(df_features)

        # Train model
        accuracy, auc_score = predictor.train_model(X, y)

        if accuracy > 0.5:  # Only save if better than random
            model_path = predictor.save_model()

            print(f"\n🎉 SUCCESS!")
            print(f"🎯 Your AI achieved {accuracy:.1%} accuracy")
            print(f"📊 AUC Score: {auc_score:.3f}")
            print(f"💾 Model ready for integration")

        else:
            print(f"\n⚠️  Model accuracy too low ({accuracy:.1%})")
            print(f"Need more/better training data")

        return predictor

    except FileNotFoundError as e:
        print(f"❌ Database not found: {e}")
        print(f"\n🔧 SOLUTIONS:")
        print(f"   1. Make sure you're in the right directory")
        print(f"   2. Check if data/trading_data.db exists")
        print(f"   3. Run daily_data_updater.py first")

    except ValueError as e:
        print(f"❌ {e}")
        print(f"\n🔧 SOLUTION:")
        print(f"   Run: python generate_historical_breakouts.py")

    except Exception as e:
        print(f"❌ Unexpected error: {e}")
        print(f"\n🔧 DEBUG INFO:")
        print(f"   Current directory: {os.getcwd()}")
        print(f"   Python path: {os.sys.path[0]}")


if __name__ == "__main__":
    predictor = main()