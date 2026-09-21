# ml_training/scripts/debug_ml_model.py

import pandas as pd
import numpy as np
import sqlite3
import os
from sklearn.ensemble import RandomForestClassifier
from sklearn.model_selection import train_test_split
from sklearn.metrics import classification_report, roc_auc_score
from sklearn.preprocessing import StandardScaler
import joblib
from datetime import datetime
import warnings

warnings.filterwarnings('ignore')


def find_database():
    """Find the correct database path"""
    possible_paths = [
        'data/trading_data.db',
        '../data/trading_data.db',
        '../../data/trading_data.db'
    ]

    for path in possible_paths:
        if os.path.exists(path):
            print(f"✅ Found database: {path}")
            return path
    return None


def debug_data_types(df):
    """Debug and fix data type issues"""
    print("\n🔍 DEBUGGING DATA TYPES:")
    print("=" * 50)

    # Show data types
    print("📊 Column Data Types:")
    for col in df.columns:
        dtype = df[col].dtype
        sample_val = df[col].iloc[0] if len(df) > 0 else "N/A"
        print(f"   {col:<20}: {dtype} | Sample: {sample_val}")

    # Check for problematic columns
    print(f"\n🔍 Checking for problematic data...")

    # Fix bytes columns
    for col in df.columns:
        if df[col].dtype == 'object':
            # Check if any values are bytes
            sample_values = df[col].dropna().head(5).tolist()
            if any(isinstance(val, bytes) for val in sample_values):
                print(f"⚠️  Found bytes in column '{col}', converting...")
                df[col] = df[col].apply(lambda x: x.decode('utf-8') if isinstance(x, bytes) else x)

    # Show sample of key columns
    print(f"\n📋 Sample of Key Columns:")
    key_cols = ['breakout_type', 'success', 'symbol', 'volume_ratio']
    available_cols = [col for col in key_cols if col in df.columns]

    if available_cols:
        print(df[available_cols].head())

    return df


def safe_data_conversion(df):
    """Safely convert data types"""
    print("\n🔧 Converting data types safely...")

    # Convert success to numeric (handle various formats)
    if 'success' in df.columns:
        print("   Converting 'success' column...")
        df['success'] = pd.to_numeric(df['success'], errors='coerce')
        df['success'] = df['success'].fillna(0).astype(int)

    # Convert numeric columns
    numeric_cols = [
        'volume_ratio', 'atr_pct', 'rsi_value', 'price_change_pct',
        'max_gain_10d', 'max_loss_10d', 'days_to_peak', 'entry_price',
        'overall_quality_score', 'growth_score', 'profitability_score',
        'financial_health_score', 'rsi_14'
    ]

    for col in numeric_cols:
        if col in df.columns:
            print(f"   Converting '{col}' to numeric...")
            df[col] = pd.to_numeric(df[col], errors='coerce')
            df[col] = df[col].fillna(df[col].median() if not df[col].isna().all() else 0)

    # Convert string columns
    string_cols = ['breakout_type', 'symbol', 'quality_grade', 'sector']
    for col in string_cols:
        if col in df.columns:
            print(f"   Converting '{col}' to string...")
            df[col] = df[col].astype(str)
            df[col] = df[col].replace('nan', 'Unknown')

    print("✅ Data type conversion completed")
    return df


class BreakoutPredictor:
    def __init__(self, db_path=None):
        if db_path is None:
            self.db_path = find_database()
        else:
            self.db_path = db_path

        if self.db_path is None:
            raise FileNotFoundError("Could not find trading_data.db")

        self.model = None
        self.scaler = StandardScaler()
        self.feature_names = []

    def load_training_data(self):
        """Load breakout data with enhanced debugging"""
        print("🔍 Loading breakout data from database...")

        conn = sqlite3.connect(self.db_path)

        # Load breakouts with debugging
        print("   Loading breakouts table...")
        query = '''
            SELECT 
                symbol,
                date,
                breakout_type,
                entry_price,
                success,
                volume_ratio,
                atr_pct,
                rsi_value,
                price_change_pct,
                max_gain_10d,
                max_loss_10d,
                days_to_peak
            FROM breakouts
            WHERE success IS NOT NULL
            ORDER BY date DESC
        '''

        df = pd.read_sql(query, conn)
        print(f"   Loaded {len(df)} breakout records")

        # Debug data types immediately
        df = debug_data_types(df)
        df = safe_data_conversion(df)

        # Add technical indicators safely
        try:
            print("   Loading technical indicators...")
            tech_query = '''
                SELECT 
                    symbol,
                    date,
                    CAST(rsi_14 as REAL) as rsi_14,
                    CAST(volume_ratio as REAL) as tech_volume_ratio,
                    CAST(price_position as REAL) as price_position
                FROM technical_indicators
                WHERE rsi_14 IS NOT NULL
            '''
            tech_df = pd.read_sql(tech_query, conn)

            # Convert to proper types
            tech_df = safe_data_conversion(tech_df)

            # Merge
            df = df.merge(tech_df, on=['symbol', 'date'], how='left', suffixes=('', '_tech'))
            print(f"   Added technical data for {len(tech_df)} records")

        except Exception as e:
            print(f"   ⚠️  Could not load technical indicators: {e}")

        # Add fundamentals safely
        try:
            print("   Loading fundamentals...")
            fund_query = '''
                SELECT 
                    symbol,
                    date,
                    CAST(overall_quality_score as REAL) as overall_quality_score,
                    CAST(growth_score as REAL) as growth_score,
                    quality_grade,
                    sector
                FROM daily_fundamentals
                WHERE quality_grade IS NOT NULL
            '''
            fund_df = pd.read_sql(fund_query, conn)

            # Convert to proper types
            fund_df = safe_data_conversion(fund_df)

            # Merge
            df = df.merge(fund_df, on=['symbol', 'date'], how='left', suffixes=('', '_fund'))
            print(f"   Added fundamental data for {len(fund_df)} records")

        except Exception as e:
            print(f"   ⚠️  Could not load fundamentals: {e}")

        conn.close()

        # Final data cleaning
        df = safe_data_conversion(df)

        # Fill missing values with safe defaults
        df = df.fillna({
            'rsi_14': 50,
            'tech_volume_ratio': 1.0,
            'price_position': 50,
            'overall_quality_score': 5,
            'growth_score': 5,
            'quality_grade': 'C',
            'sector': 'Unknown'
        })

        print(f"✅ Final dataset: {len(df)} breakouts")

        # Safe breakout distribution calculation
        try:
            print(f"\n📊 Breakout Distribution:")

            # Use .value_counts() which is safer than filtering
            breakout_counts = df['breakout_type'].value_counts()
            print(f"   Breakout types: {dict(breakout_counts)}")

            success_counts = df['success'].value_counts()
            print(f"   Success distribution: {dict(success_counts)}")

            if len(df) > 0:
                success_rate = df['success'].mean() * 100
                print(f"   Success rate: {success_rate:.1f}%")

        except Exception as e:
            print(f"   ⚠️  Error calculating distribution: {e}")
            print(f"   Proceeding with available data...")

        return df

    def engineer_features(self, df):
        """Create ML features with safe calculations"""
        print("🔧 Engineering features...")

        features_df = df.copy()

        try:
            # Safe feature engineering
            features_df['is_bullish'] = (features_df['breakout_type'] == 'bullish').astype(int)

            # Numeric features with safe calculations
            features_df['volume_strength'] = np.log1p(
                pd.to_numeric(features_df['volume_ratio'], errors='coerce').fillna(1))
            features_df['rsi_normalized'] = pd.to_numeric(features_df['rsi_14'], errors='coerce').fillna(50) / 100
            features_df['quality_normalized'] = pd.to_numeric(features_df['overall_quality_score'],
                                                              errors='coerce').fillna(5) / 10

            # Interaction features
            features_df['volume_rsi'] = features_df['volume_strength'] * features_df['rsi_normalized']

            # Grade encoding
            grade_mapping = {'A': 4, 'B': 3, 'C': 2, 'D': 1}
            features_df['quality_grade_numeric'] = features_df['quality_grade'].map(grade_mapping).fillna(2)

            print(f"✅ Feature engineering completed")

        except Exception as e:
            print(f"⚠️  Error in feature engineering: {e}")
            print(f"   Using basic features only...")

        return features_df

    def prepare_features_and_target(self, df):
        """Prepare features for ML with extensive error handling"""
        print("📊 Preparing ML features...")

        # Core features that should always exist
        core_features = [
            'volume_ratio',
            'atr_pct',
            'rsi_value',
            'price_change_pct'
        ]

        # Additional features if available
        additional_features = [
            'is_bullish',
            'volume_strength',
            'rsi_normalized',
            'quality_normalized',
            'volume_rsi',
            'quality_grade_numeric',
            'overall_quality_score',
            'growth_score'
        ]

        # Select available features
        available_features = []
        for feature in core_features + additional_features:
            if feature in df.columns:
                available_features.append(feature)

        if len(available_features) < 3:
            raise ValueError(f"Not enough features available. Found: {available_features}")

        print(f"   Using {len(available_features)} features: {available_features}")

        # Prepare feature matrix
        X = df[available_features].copy()

        # Convert all to numeric and handle NaN
        for col in X.columns:
            X[col] = pd.to_numeric(X[col], errors='coerce')

        X = X.fillna(X.median())

        # Prepare target
        y = pd.to_numeric(df['success'], errors='coerce').fillna(0).astype(int)

        # Remove any rows where target is still NaN
        valid_mask = ~y.isna()
        X = X[valid_mask]
        y = y[valid_mask]

        self.feature_names = X.columns.tolist()

        print(f"✅ Prepared {len(X)} samples with {len(self.feature_names)} features")
        print(f"📈 Target: {y.mean() * 100:.1f}% successful breakouts")

        return X, y

    def train_model(self, X, y):
        """Train model with robust error handling"""
        print("🤖 Training AI model...")

        if len(X) < 20:
            print("❌ Not enough data for training")
            return 0, 0

        try:
            # Split data
            X_train, X_test, y_train, y_test = train_test_split(
                X, y, test_size=0.25, random_state=42, stratify=y
            )

            # Scale features
            X_train_scaled = self.scaler.fit_transform(X_train)
            X_test_scaled = self.scaler.transform(X_test)

            # Train model
            self.model = RandomForestClassifier(
                n_estimators=100,
                max_depth=8,
                random_state=42,
                class_weight='balanced'
            )

            self.model.fit(X_train_scaled, y_train)

            # Evaluate
            accuracy = self.model.score(X_test_scaled, y_test)

            try:
                y_prob = self.model.predict_proba(X_test_scaled)[:, 1]
                auc_score = roc_auc_score(y_test, y_prob)
            except:
                auc_score = 0.5

            print(f"\n🎯 MODEL PERFORMANCE:")
            print(f"   Accuracy: {accuracy:.1%}")
            print(f"   AUC Score: {auc_score:.3f}")

            # Feature importance
            print(f"\n🔍 TOP FEATURES:")
            importances = self.model.feature_importances_
            for i, (feature, importance) in enumerate(
                    sorted(zip(self.feature_names, importances), key=lambda x: x[1], reverse=True)[:5]
            ):
                print(f"   {i + 1}. {feature:<20}: {importance:.3f}")

            return accuracy, auc_score

        except Exception as e:
            print(f"❌ Training error: {e}")
            return 0, 0

    def save_model(self):
        """Save the model"""
        try:
            os.makedirs('../../models', exist_ok=True)
            timestamp = datetime.now().strftime('%Y%m%d_%H%M')
            model_path = f"../models/breakout_predictor_{timestamp}.joblib"

            joblib.dump(self.model, model_path)
            print(f"✅ Model saved: {model_path}")
            return model_path
        except Exception as e:
            print(f"⚠️  Could not save model: {e}")
            return None


def main():
    """Main function with comprehensive error handling"""
    print("🚀 DEBUG AI BREAKOUT PREDICTOR")
    print("=" * 50)

    try:
        predictor = BreakoutPredictor()

        # Load data with debugging
        df = predictor.load_training_data()

        if len(df) < 10:
            print(f"❌ Insufficient data: {len(df)} breakouts (need 10+)")
            return

        # Engineer features
        df_features = predictor.engineer_features(df)

        # Prepare for ML
        X, y = predictor.prepare_features_and_target(df_features)

        # Train model
        accuracy, auc_score = predictor.train_model(X, y)

        if accuracy > 0.5:
            predictor.save_model()

            print(f"\n🎉 SUCCESS!")
            print(f"🎯 Accuracy: {accuracy:.1%}")
            print(f"📊 AUC: {auc_score:.3f}")
            print(f"📈 Ready for integration!")
        else:
            print(f"\n⚠️  Low accuracy: {accuracy:.1%}")
            print(f"Model trained but needs improvement")

        return predictor

    except Exception as e:
        print(f"❌ Error: {e}")
        import traceback
        print("🔍 Full traceback:")
        traceback.print_exc()


if __name__ == "__main__":
    predictor = main()