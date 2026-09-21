# ml_training/scripts/fix_binary_and_train.py

import pandas as pd
import numpy as np
import sqlite3
import os
from sklearn.ensemble import RandomForestClassifier
from sklearn.model_selection import train_test_split
from sklearn.metrics import roc_auc_score, classification_report
from sklearn.preprocessing import StandardScaler
import joblib
from datetime import datetime
import warnings

warnings.filterwarnings('ignore')


class BinaryBytesPredictor:
    def __init__(self, db_path):
        self.db_path = db_path
        self.model = None
        self.scaler = StandardScaler()
        self.feature_names = []

    def fix_binary_bytes_data(self, df):
        """
        Fix binary bytes data like b'\x00' and b'\x01'
        """
        print(f"\n🔧 FIXING BINARY BYTES DATA...")
        print("=" * 50)

        # Show the problem first
        if 'success' in df.columns:
            print(f"📊 Original success column sample:")
            success_sample = df['success'].head(10).tolist()
            for i, val in enumerate(success_sample):
                print(f"   {i + 1}. {val} (type: {type(val)})")

        # Fix binary bytes in success column
        def fix_binary_success(value):
            if isinstance(value, bytes):
                if value == b'\x01':
                    return 1
                elif value == b'\x00':
                    return 0
                else:
                    # Handle other binary values
                    try:
                        return int.from_bytes(value, byteorder='little')
                    except:
                        return 0
            elif isinstance(value, str):
                # Handle string representations
                if value in ['1', 'True', 'true']:
                    return 1
                elif value in ['0', 'False', 'false']:
                    return 0
                else:
                    return 0
            else:
                # Handle numeric
                try:
                    return int(float(value))
                except:
                    return 0

        # Apply the fix
        if 'success' in df.columns:
            print(f"   Fixing success column...")
            df['success'] = df['success'].apply(fix_binary_success)

            # Verify the fix
            success_rate = df['success'].mean()
            success_counts = df['success'].value_counts().sort_index()

            print(f"   ✅ Fixed success column!")
            print(f"   📊 Success distribution: {dict(success_counts)}")
            print(f"   📈 Success rate: {success_rate:.1%}")

        # Fix other potential binary columns
        binary_columns = ['breakout_type']
        for col in binary_columns:
            if col in df.columns:
                print(f"   Checking '{col}' for binary data...")
                # Check if any values are bytes
                sample_values = df[col].head(5).tolist()
                has_bytes = any(isinstance(val, bytes) for val in sample_values)

                if has_bytes:
                    print(f"      Found binary data in '{col}', fixing...")
                    df[col] = df[col].apply(lambda x: x.decode('utf-8') if isinstance(x, bytes) else str(x))

        print(f"✅ Binary bytes fixing completed!")
        return df

    def load_and_fix_data(self):
        """
        Load data and fix all corruption issues
        """
        print(f"🔍 Loading data from database...")

        conn = sqlite3.connect(self.db_path)

        query = '''
            SELECT 
                symbol, date, breakout_type, entry_price, success,
                volume_ratio, atr_pct, rsi_value, price_change_pct,
                max_gain_10d, max_loss_10d, days_to_peak
            FROM breakouts 
            WHERE success IS NOT NULL 
            ORDER BY date DESC
        '''

        df = pd.read_sql(query, conn)
        conn.close()

        print(f"📊 Loaded {len(df)} breakout records")

        # Fix binary bytes data
        df = self.fix_binary_bytes_data(df)

        # Additional data cleaning
        print(f"\n🧹 Additional data cleaning...")

        # Ensure numeric columns are properly numeric
        numeric_cols = ['volume_ratio', 'atr_pct', 'rsi_value', 'price_change_pct',
                        'max_gain_10d', 'max_loss_10d', 'entry_price']

        for col in numeric_cols:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors='coerce')
                df[col] = df[col].fillna(df[col].median())

        # Clean string columns
        if 'breakout_type' in df.columns:
            df['breakout_type'] = df['breakout_type'].astype(str)
            df['breakout_type'] = df['breakout_type'].str.strip()

        print(f"✅ Data cleaning completed!")

        # Final verification
        print(f"\n📊 FINAL DATA SUMMARY:")
        print(f"   Total breakouts: {len(df)}")

        if 'success' in df.columns:
            success_counts = df['success'].value_counts().sort_index()
            success_rate = df['success'].mean()
            print(f"   Success distribution: {dict(success_counts)}")
            print(f"   Success rate: {success_rate:.1%}")

        if 'breakout_type' in df.columns:
            type_counts = df['breakout_type'].value_counts()
            print(f"   Breakout types: {dict(type_counts)}")

        return df

    def create_features(self, df):
        """
        Create comprehensive ML features
        """
        print(f"\n🔧 Creating ML features...")

        features_df = df.copy()

        # Basic features
        features_df['is_bullish'] = (features_df['breakout_type'] == 'bullish').astype(int)

        # Volume features
        if 'volume_ratio' in features_df.columns:
            features_df['volume_strength'] = np.log1p(features_df['volume_ratio'])
            features_df['high_volume'] = (features_df['volume_ratio'] > 2.0).astype(int)

        # RSI features
        if 'rsi_value' in features_df.columns:
            features_df['rsi_normalized'] = features_df['rsi_value'] / 100
            features_df['rsi_overbought'] = (features_df['rsi_value'] > 70).astype(int)
            features_df['rsi_oversold'] = (features_df['rsi_value'] < 30).astype(int)
            features_df['rsi_neutral'] = ((features_df['rsi_value'] >= 40) & (features_df['rsi_value'] <= 60)).astype(
                int)

        # Price change features
        if 'price_change_pct' in features_df.columns:
            features_df['strong_move'] = (abs(features_df['price_change_pct']) > 3).astype(int)
            features_df['price_momentum'] = features_df['price_change_pct']

        # Risk features
        if 'atr_pct' in features_df.columns:
            features_df['high_volatility'] = (features_df['atr_pct'] > 3).astype(int)
            features_df['low_volatility'] = (features_df['atr_pct'] < 1.5).astype(int)

        # Interaction features
        if 'volume_ratio' in features_df.columns and 'rsi_value' in features_df.columns:
            features_df['volume_rsi_score'] = features_df['volume_ratio'] * (features_df['rsi_value'] / 100)

        if 'volume_ratio' in features_df.columns and 'price_change_pct' in features_df.columns:
            features_df['volume_momentum'] = features_df['volume_ratio'] * abs(features_df['price_change_pct'])

        # Select features for training
        feature_columns = [
            'is_bullish',
            'volume_ratio', 'volume_strength', 'high_volume',
            'rsi_value', 'rsi_normalized', 'rsi_overbought', 'rsi_oversold', 'rsi_neutral',
            'price_change_pct', 'price_momentum', 'strong_move',
            'atr_pct', 'high_volatility', 'low_volatility'
        ]

        # Add interaction features if they exist
        interaction_features = ['volume_rsi_score', 'volume_momentum']
        for feature in interaction_features:
            if feature in features_df.columns:
                feature_columns.append(feature)

        # Filter to columns that actually exist
        available_features = [col for col in feature_columns if col in features_df.columns]

        print(f"✅ Created {len(available_features)} features:")
        for i, feature in enumerate(available_features, 1):
            print(f"   {i:2}. {feature}")

        return features_df, available_features

    def train_model(self, df, feature_cols):
        """
        Train Random Forest model with proper validation
        """
        print(f"\n🤖 TRAINING AI MODEL...")
        print("=" * 50)

        # Prepare data
        X = df[feature_cols].copy()
        y = df['success'].copy()

        # Final data validation
        print(f"📊 Training data validation:")
        print(f"   Samples: {len(X)}")
        print(f"   Features: {len(feature_cols)}")
        print(f"   Target distribution: {y.value_counts().sort_index().to_dict()}")
        print(f"   Success rate: {y.mean():.1%}")

        if len(X) < 20:
            print(f"❌ Not enough data for reliable training")
            return None, None, None

        if y.nunique() < 2:
            print(f"❌ Target has only one class - cannot train classifier")
            return None, None, None

        # Handle missing values
        X = X.fillna(X.median())

        # Split data
        test_size = max(0.2, min(0.3, 30 / len(X)))
        X_train, X_test, y_train, y_test = train_test_split(
            X, y, test_size=test_size, random_state=42, stratify=y
        )

        print(f"\n📊 Data split:")
        print(f"   Training: {len(X_train)} samples")
        print(f"   Testing: {len(X_test)} samples")
        print(f"   Train success rate: {y_train.mean():.1%}")
        print(f"   Test success rate: {y_test.mean():.1%}")

        # Scale features
        X_train_scaled = self.scaler.fit_transform(X_train)
        X_test_scaled = self.scaler.transform(X_test)

        # Train Random Forest
        self.model = RandomForestClassifier(
            n_estimators=200,
            max_depth=8,
            min_samples_split=5,
            min_samples_leaf=3,
            random_state=42,
            class_weight='balanced',
            bootstrap=True,
            oob_score=True
        )

        print(f"\n🎯 Training Random Forest...")
        self.model.fit(X_train_scaled, y_train)

        # Evaluate model
        train_accuracy = self.model.score(X_train_scaled, y_train)
        test_accuracy = self.model.score(X_test_scaled, y_test)
        oob_score = self.model.oob_score_

        # Predictions and probabilities
        y_pred = self.model.predict(X_test_scaled)
        y_pred_proba = self.model.predict_proba(X_test_scaled)[:, 1]

        # Calculate AUC
        try:
            auc_score = roc_auc_score(y_test, y_pred_proba)
        except:
            auc_score = 0.5

        print(f"\n🎯 MODEL PERFORMANCE:")
        print(f"   Training Accuracy: {train_accuracy:.1%}")
        print(f"   Test Accuracy: {test_accuracy:.1%}")
        print(f"   Out-of-Bag Score: {oob_score:.1%}")
        print(f"   AUC Score: {auc_score:.3f}")

        # Detailed classification report
        print(f"\n📈 DETAILED PERFORMANCE:")
        print(classification_report(y_test, y_pred, target_names=['Failed', 'Successful']))

        # Feature importance
        print(f"\n🔍 TOP 10 FEATURE IMPORTANCE:")
        importances = self.model.feature_importances_
        self.feature_names = feature_cols

        feature_importance = sorted(zip(feature_cols, importances),
                                    key=lambda x: x[1], reverse=True)

        for i, (feature, importance) in enumerate(feature_importance[:10], 1):
            print(f"   {i:2}. {feature:<25}: {importance:.3f}")

        return test_accuracy, auc_score, feature_importance

    def save_model(self):
        """Save the trained model and metadata"""
        try:
            os.makedirs('../../models', exist_ok=True)
            timestamp = datetime.now().strftime('%Y%m%d_%H%M')

            # Save model and scaler
            model_path = f"../models/breakout_ai_{timestamp}.joblib"
            scaler_path = f"../models/breakout_scaler_{timestamp}.joblib"
            features_path = f"../models/breakout_features_{timestamp}.txt"

            joblib.dump(self.model, model_path)
            joblib.dump(self.scaler, scaler_path)

            # Save feature names
            with open(features_path, 'w') as f:
                f.write('\n'.join(self.feature_names))

            print(f"\n💾 MODEL SAVED:")
            print(f"   Model: {model_path}")
            print(f"   Scaler: {scaler_path}")
            print(f"   Features: {features_path}")

            return model_path

        except Exception as e:
            print(f"⚠️  Could not save model: {e}")
            return None


def main():
    """
    Main training function with binary bytes fixing
    """
    print("🚀 BINARY BYTES FIXER & AI TRAINER")
    print("=" * 80)

    # Auto-find the correct database path WITH breakouts table
    possible_paths = [
        "data/trading_data.db",  # If in automation directory
        "../mechanism/data/trading_data.db",  # If in ml_training/scripts
        "mechanism/data/trading_data.db",  # If in project root
        "../../mechanism/data/trading_data.db"  # If nested deeper
    ]

    db_path = None
    for path in possible_paths:
        if os.path.exists(path):
            # Check if this database actually has breakouts table
            try:
                test_conn = sqlite3.connect(path)
                cursor = test_conn.cursor()
                cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='breakouts';")
                has_breakouts = cursor.fetchone() is not None

                if has_breakouts:
                    cursor.execute("SELECT COUNT(*) FROM breakouts WHERE success IS NOT NULL")
                    breakout_count = cursor.fetchone()[0]
                    test_conn.close()

                    if breakout_count > 0:
                        db_path = path
                        print(f"✅ Found database with {breakout_count} breakouts: {path}")
                        break
                    else:
                        print(f"⚠️  Database {path} has breakouts table but no labeled data")
                else:
                    print(f"⚠️  Database {path} exists but no breakouts table")
                    test_conn.close()
            except Exception as e:
                print(f"⚠️  Error checking {path}: {e}")

    if not db_path:
        print(f"❌ No database with breakouts table found!")
        print(f"\n🔍 Searched these locations:")
        for path in possible_paths:
            exists = "✅" if os.path.exists(path) else "❌"
            print(f"   {exists} {path}")

        print(f"\n🔧 SOLUTION:")
        print(f"   1. Make sure you're in the mechanism directory")
        print(f"   2. Run: python generate_historical_breakouts_fixed.py")
        print(f"   3. Then try this script again")
        return

    try:
        predictor = BinaryBytesPredictor(db_path)

        # Load and fix corrupted data
        df = predictor.load_and_fix_data()

        if len(df) < 20:
            print(f"❌ Not enough data: {len(df)} breakouts")
            return

        # Create features
        df_features, feature_cols = predictor.create_features(df)

        # Train model
        accuracy, auc, feature_importance = predictor.train_model(df_features, feature_cols)

        if accuracy and accuracy > 0.55:
            model_path = predictor.save_model()

            print(f"\n🎉 SUCCESS! AI BREAKOUT PREDICTOR TRAINED!")
            print(f"=" * 60)
            print(f"🎯 Test Accuracy: {accuracy:.1%}")
            print(f"📊 AUC Score: {auc:.3f}")

            if accuracy > 0.75:
                print(f"🏆 EXCELLENT! Professional-grade performance!")
            elif accuracy > 0.65:
                print(f"✅ VERY GOOD! Ready for assisted trading!")
            elif accuracy > 0.55:
                print(f"👍 GOOD! Better than random, suitable for testing!")

            print(f"\n🚀 YOUR AI MODEL CAN NOW:")
            print(f"   • Predict breakout success with {accuracy:.1%} accuracy")
            print(f"   • Score breakouts from 0-100% probability")
            print(f"   • Identify key success factors")
            print(f"   • Filter trades by confidence level")

            print(f"\n🎯 NEXT STEPS:")
            print(f"   1. Test predictions on recent breakouts")
            print(f"   2. Integrate into your screener")
            print(f"   3. Build confidence-based filtering")
            print(f"   4. Start paper trading with AI signals")

        else:
            print(f"\n⚠️  Model accuracy too low: {accuracy:.1%}")
            print(f"   The model learned something but needs improvement")

        return predictor

    except Exception as e:
        print(f"❌ Training failed: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    predictor = main()