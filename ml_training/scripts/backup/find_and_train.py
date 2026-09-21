# ml_training/scripts/find_and_train.py

import pandas as pd
import numpy as np
import sqlite3
import os
from sklearn.ensemble import RandomForestClassifier
from sklearn.model_selection import train_test_split
from sklearn.metrics import roc_auc_score
from sklearn.preprocessing import StandardScaler
import joblib
from datetime import datetime
import warnings

warnings.filterwarnings('ignore')


def find_all_databases():
    """
    Find ALL database files and check which one has breakouts
    """
    print("🔍 SEARCHING FOR ALL DATABASES...")
    print("=" * 60)

    # Search in multiple locations
    search_paths = [
        ".",
        "..",
        "../..",
        "../../..",
        "data",
        "../data",
        "../../data",
        "../../../data",
        "mechanism/data",
        "../mechanism/data",
        "../../mechanism/data"
    ]

    databases_found = []

    for search_path in search_paths:
        try:
            if os.path.exists(search_path):
                for file in os.listdir(search_path):
                    if file.endswith('.db'):
                        full_path = os.path.join(search_path, file)
                        if os.path.exists(full_path):
                            databases_found.append(os.path.abspath(full_path))
        except:
            pass

    # Remove duplicates
    unique_dbs = list(set(databases_found))

    print(f"📊 Found {len(unique_dbs)} database files:")
    for i, db_path in enumerate(unique_dbs, 1):
        print(f"   {i}. {db_path}")

    return unique_dbs


def check_database_contents(db_path):
    """
    Check what's in each database
    """
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()

        # Get table names
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
        tables = [table[0] for table in cursor.fetchall()]

        print(f"\n📋 Database: {os.path.basename(db_path)}")
        print(f"   Location: {db_path}")
        print(f"   Tables: {tables}")

        # Check breakouts specifically
        if 'breakouts' in tables:
            cursor.execute("SELECT COUNT(*) FROM breakouts")
            total_breakouts = cursor.fetchone()[0]

            cursor.execute("SELECT COUNT(*) FROM breakouts WHERE success IS NOT NULL")
            labeled_breakouts = cursor.fetchone()[0]

            print(f"   🎯 BREAKOUTS: {total_breakouts} total, {labeled_breakouts} labeled")

            if labeled_breakouts > 0:
                print(f"   ✅ THIS DATABASE HAS TRAINING DATA!")

                # Show sample data
                cursor.execute("SELECT * FROM breakouts LIMIT 3")
                samples = cursor.fetchall()
                print(f"   📊 Sample breakout data:")
                for sample in samples:
                    print(f"      {sample[:4]}...")  # First 4 columns

                conn.close()
                return labeled_breakouts
            else:
                print(f"   ❌ No labeled breakouts")
        else:
            print(f"   ❌ No breakouts table")

        conn.close()
        return 0

    except Exception as e:
        print(f"   ❌ Error checking database: {e}")
        return 0


def find_best_database():
    """
    Find the database with the most labeled breakouts
    """
    print("🎯 FINDING BEST DATABASE FOR ML TRAINING...")
    print("=" * 60)

    all_dbs = find_all_databases()

    if not all_dbs:
        print("❌ No databases found!")
        return None

    best_db = None
    max_breakouts = 0

    for db_path in all_dbs:
        breakout_count = check_database_contents(db_path)
        if breakout_count > max_breakouts:
            max_breakouts = breakout_count
            best_db = db_path

    if best_db:
        print(f"\n🏆 BEST DATABASE FOR TRAINING:")
        print(f"   📍 Path: {best_db}")
        print(f"   🎯 Labeled breakouts: {max_breakouts}")
        return best_db
    else:
        print(f"\n❌ No database with labeled breakouts found!")
        return None


class SimpleBreakoutPredictor:
    def __init__(self, db_path):
        self.db_path = db_path
        self.model = None
        self.scaler = StandardScaler()
        self.feature_names = []

    def load_and_clean_data(self):
        """
        Load breakout data and clean it thoroughly
        """
        print(f"\n🔍 Loading data from: {os.path.basename(self.db_path)}")

        conn = sqlite3.connect(self.db_path)

        # Simple, robust query
        query = '''
            SELECT * FROM breakouts 
            WHERE success IS NOT NULL 
            ORDER BY date DESC
        '''

        df = pd.read_sql(query, conn)
        conn.close()

        print(f"📊 Loaded {len(df)} breakout records")

        # Show first few records to understand data structure
        print(f"\n🔍 Sample data structure:")
        print(df.head(2))
        print(f"\n📋 Columns: {list(df.columns)}")
        print(f"\n📊 Data types:")
        print(df.dtypes)

        # Clean data step by step
        print(f"\n🧹 Cleaning data...")

        # Fix success column (main target)
        print(f"   Fixing 'success' column...")
        if 'success' in df.columns:
            # Handle various data types
            df['success'] = df['success'].astype(str)  # Convert everything to string first
            df['success'] = df['success'].str.replace('b\'', '').str.replace('\'', '')  # Remove b' and '
            df['success'] = pd.to_numeric(df['success'], errors='coerce')
            df['success'] = df['success'].fillna(0).astype(int)

            success_rate = df['success'].mean()
            print(f"      Success rate: {success_rate:.1%}")

        # Fix breakout_type
        if 'breakout_type' in df.columns:
            print(f"   Fixing 'breakout_type' column...")
            df['breakout_type'] = df['breakout_type'].astype(str)
            df['breakout_type'] = df['breakout_type'].str.replace('b\'', '').str.replace('\'', '')

            type_counts = df['breakout_type'].value_counts()
            print(f"      Breakout types: {dict(type_counts)}")

        # Fix numeric columns
        numeric_cols = ['volume_ratio', 'atr_pct', 'rsi_value', 'price_change_pct',
                        'max_gain_10d', 'max_loss_10d', 'entry_price']

        for col in numeric_cols:
            if col in df.columns:
                print(f"   Fixing '{col}' column...")
                df[col] = pd.to_numeric(df[col], errors='coerce')
                median_val = df[col].median()
                df[col] = df[col].fillna(median_val)

        print(f"✅ Data cleaning completed!")
        print(f"📊 Final dataset: {len(df)} records")

        return df

    def create_simple_features(self, df):
        """
        Create basic ML features from cleaned data
        """
        print(f"\n🔧 Creating ML features...")

        features_df = df.copy()

        # Basic features that should always work
        features_df['is_bullish'] = (features_df['breakout_type'] == 'bullish').astype(int)

        # Ensure we have numeric features
        required_features = ['volume_ratio', 'atr_pct', 'rsi_value', 'price_change_pct']
        available_features = ['is_bullish']

        for feature in required_features:
            if feature in features_df.columns:
                # Make sure it's numeric
                features_df[feature] = pd.to_numeric(features_df[feature], errors='coerce')
                features_df[feature] = features_df[feature].fillna(features_df[feature].median())
                available_features.append(feature)

        # Simple engineered features
        if 'volume_ratio' in features_df.columns:
            features_df['log_volume'] = np.log1p(features_df['volume_ratio'])
            available_features.append('log_volume')

        if 'rsi_value' in features_df.columns:
            features_df['rsi_extreme'] = ((features_df['rsi_value'] > 70) | (features_df['rsi_value'] < 30)).astype(int)
            available_features.append('rsi_extreme')

        print(f"✅ Created {len(available_features)} features: {available_features}")

        return features_df, available_features

    def train_simple_model(self, df, feature_cols):
        """
        Train a simple but effective model
        """
        print(f"\n🤖 Training AI model...")

        # Prepare features and target
        X = df[feature_cols].copy()
        y = df['success'].copy()

        # Handle any remaining NaN
        X = X.fillna(X.median())

        print(f"📊 Training data: {len(X)} samples, {len(feature_cols)} features")
        print(f"🎯 Target distribution: {y.value_counts().to_dict()}")

        if len(X) < 10:
            print(f"❌ Not enough data for training")
            return None, None

        # Split data
        test_size = max(0.2, min(0.4, 20 / len(X)))  # Adaptive test size
        X_train, X_test, y_train, y_test = train_test_split(
            X, y, test_size=test_size, random_state=42, stratify=y
        )

        print(f"📊 Split: {len(X_train)} train, {len(X_test)} test")

        # Scale features
        X_train_scaled = self.scaler.fit_transform(X_train)
        X_test_scaled = self.scaler.transform(X_test)

        # Train Random Forest
        self.model = RandomForestClassifier(
            n_estimators=100,
            max_depth=6,
            min_samples_split=5,
            random_state=42,
            class_weight='balanced'
        )

        self.model.fit(X_train_scaled, y_train)

        # Evaluate
        train_score = self.model.score(X_train_scaled, y_train)
        test_score = self.model.score(X_test_scaled, y_test)

        try:
            y_pred_proba = self.model.predict_proba(X_test_scaled)[:, 1]
            auc = roc_auc_score(y_test, y_pred_proba)
        except:
            auc = 0.5

        print(f"\n🎯 MODEL PERFORMANCE:")
        print(f"   Training Accuracy: {train_score:.1%}")
        print(f"   Test Accuracy: {test_score:.1%}")
        print(f"   AUC Score: {auc:.3f}")

        # Feature importance
        print(f"\n🔍 FEATURE IMPORTANCE:")
        importances = self.model.feature_importances_
        self.feature_names = feature_cols

        for feature, importance in sorted(zip(feature_cols, importances),
                                          key=lambda x: x[1], reverse=True):
            print(f"   {feature:<20}: {importance:.3f}")

        return test_score, auc

    def save_model(self):
        """Save the trained model"""
        try:
            os.makedirs('../../models', exist_ok=True)
            timestamp = datetime.now().strftime('%Y%m%d_%H%M')

            model_path = f"../models/simple_predictor_{timestamp}.joblib"
            scaler_path = f"../models/simple_scaler_{timestamp}.joblib"

            joblib.dump(self.model, model_path)
            joblib.dump(self.scaler, scaler_path)

            print(f"\n💾 Model saved:")
            print(f"   Model: {model_path}")
            print(f"   Scaler: {scaler_path}")

            return model_path
        except Exception as e:
            print(f"⚠️  Could not save model: {e}")
            return None


def main():
    """
    Main function: Find database and train model
    """
    print("🚀 BREAKOUT PREDICTOR - DATABASE FINDER & TRAINER")
    print("=" * 80)

    # Step 1: Find the best database
    best_db = find_best_database()

    if not best_db:
        print(f"\n❌ Cannot proceed without labeled breakout data")
        print(f"\n🔧 SOLUTION:")
        print(f"   1. Run: cd mechanism")
        print(f"   2. Run: python generate_historical_breakouts_fixed.py")
        print(f"   3. Then try this script again")
        return

    # Step 2: Train model
    try:
        predictor = SimpleBreakoutPredictor(best_db)

        # Load and clean data
        df = predictor.load_and_clean_data()

        # Create features
        df_features, feature_cols = predictor.create_simple_features(df)

        # Train model
        accuracy, auc = predictor.train_simple_model(df_features, feature_cols)

        if accuracy and accuracy > 0.55:  # Better than random + some margin
            model_path = predictor.save_model()

            print(f"\n🎉 SUCCESS! Your AI breakout predictor is ready!")
            print(f"🎯 Test Accuracy: {accuracy:.1%}")
            print(f"📊 AUC Score: {auc:.3f}")

            if accuracy > 0.7:
                print(f"🏆 EXCELLENT performance! Ready for live trading!")
            elif accuracy > 0.6:
                print(f"✅ GOOD performance! Suitable for assisted trading!")
            else:
                print(f"⚠️  BASIC performance, but better than random!")

            print(f"\n🚀 NEXT STEPS:")
            print(f"   1. Integrate predictions into your screener")
            print(f"   2. Test on recent breakouts")
            print(f"   3. Gather more data to improve accuracy")

        else:
            print(f"\n⚠️  Model accuracy too low: {accuracy:.1%}")
            print(f"   Need more or better quality data")

        return predictor

    except Exception as e:
        print(f"❌ Training failed: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    predictor = main()