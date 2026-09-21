# ml_training/scripts/hybrid_ai_trainer.py

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


class HybridBreakoutPredictor:
    def __init__(self, db_path):
        self.db_path = db_path
        self.model = None
        self.scaler = StandardScaler()
        self.feature_names = []

    def load_comprehensive_data(self):
        """
        Load breakouts with BOTH technical AND fundamental data
        """
        print("🔍 Loading comprehensive breakout data...")
        print("   Technical indicators + Fundamental analysis")

        conn = sqlite3.connect(self.db_path)

        # Enhanced query that includes fundamentals
        query = '''
            SELECT 
                -- Breakout info
                b.symbol, b.date, b.breakout_type, b.entry_price, b.success,
                b.volume_ratio, b.atr_pct, b.rsi_value, b.price_change_pct,
                b.max_gain_10d, b.max_loss_10d, b.days_to_peak,

                -- Fundamental scores (the gold mine!)
                f.overall_quality_score,
                f.growth_score,
                f.profitability_score, 
                f.financial_health_score,
                f.valuation_score,
                f.quality_grade,
                f.pe_ratio,
                f.pb_ratio,
                f.market_cap,
                f.sector,
                f.beta,

                -- Additional technical indicators
                t.rsi_14,
                t.price_position,
                t.sma_10,
                t.sma_20,
                t.volume_ratio as tech_volume_ratio

            FROM breakouts b
            LEFT JOIN daily_fundamentals f ON b.symbol = f.symbol AND b.date = f.date
            LEFT JOIN technical_indicators t ON b.symbol = t.symbol AND b.date = t.date
            WHERE b.success IS NOT NULL
            ORDER BY b.date DESC
        '''

        df = pd.read_sql(query, conn)
        conn.close()

        print(f"📊 Loaded {len(df)} breakouts with fundamental data")

        # Fix binary bytes in success column
        def fix_binary_success(value):
            if isinstance(value, bytes):
                return 1 if value == b'\x01' else 0
            else:
                return int(float(value)) if value is not None else 0

        df['success'] = df['success'].apply(fix_binary_success)

        # Check fundamental data coverage
        has_quality_score = (~df['overall_quality_score'].isna()).sum()
        has_quality_grade = (~df['quality_grade'].isna()).sum()
        has_sector = (~df['sector'].isna()).sum()

        print(f"📈 Fundamental data coverage:")
        print(f"   Quality scores: {has_quality_score}/{len(df)} ({has_quality_score / len(df) * 100:.1f}%)")
        print(f"   Quality grades: {has_quality_grade}/{len(df)} ({has_quality_grade / len(df) * 100:.1f}%)")
        print(f"   Sector data: {has_sector}/{len(df)} ({has_sector / len(df) * 100:.1f}%)")

        # Fill missing fundamental data with intelligent defaults
        df = self.fill_missing_fundamentals(df)

        return df

    def fill_missing_fundamentals(self, df):
        """
        Intelligently fill missing fundamental data
        """
        print("🧹 Filling missing fundamental data...")

        # For quality scores, use sector averages where possible
        for score_col in ['overall_quality_score', 'growth_score', 'profitability_score',
                          'financial_health_score', 'valuation_score']:
            if score_col in df.columns:
                # Fill with sector median first
                df[score_col] = df.groupby('sector')[score_col].transform(
                    lambda x: x.fillna(x.median())
                )
                # Fill remaining with overall median
                df[score_col] = df[score_col].fillna(df[score_col].median())
                # Final fallback
                df[score_col] = df[score_col].fillna(5.0)

        # Quality grade based on overall quality score
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

        # Fill other fundamentals
        df['pe_ratio'] = df['pe_ratio'].fillna(df['pe_ratio'].median())
        df['pb_ratio'] = df['pb_ratio'].fillna(df['pb_ratio'].median())
        df['market_cap'] = df['market_cap'].fillna(df['market_cap'].median())
        df['beta'] = df['beta'].fillna(1.0)
        df['sector'] = df['sector'].fillna('Unknown')

        print("✅ Fundamental data preparation completed!")
        return df

    def create_hybrid_features(self, df):
        """
        Create comprehensive features combining technical + fundamental analysis
        """
        print("🔧 Creating HYBRID features (Technical + Fundamental)...")

        features_df = df.copy()

        # =============================================================
        # TECHNICAL FEATURES (same as before)
        # =============================================================
        features_df['is_bullish'] = (features_df['breakout_type'] == 'bullish').astype(int)

        # Volume features
        features_df['volume_strength'] = np.log1p(features_df['volume_ratio'])
        features_df['high_volume'] = (features_df['volume_ratio'] > 2.0).astype(int)

        # RSI features
        features_df['rsi_normalized'] = features_df['rsi_value'] / 100
        features_df['rsi_overbought'] = (features_df['rsi_value'] > 70).astype(int)
        features_df['rsi_oversold'] = (features_df['rsi_value'] < 30).astype(int)
        features_df['rsi_neutral'] = ((features_df['rsi_value'] >= 40) & (features_df['rsi_value'] <= 60)).astype(int)

        # Price momentum
        features_df['price_momentum'] = features_df['price_change_pct']
        features_df['strong_move'] = (abs(features_df['price_change_pct']) > 3).astype(int)

        # Risk features
        features_df['high_volatility'] = (features_df['atr_pct'] > 3).astype(int)
        features_df['low_volatility'] = (features_df['atr_pct'] < 1.5).astype(int)

        # =============================================================
        # FUNDAMENTAL FEATURES (the new power!)
        # =============================================================

        # Quality score features
        features_df['quality_normalized'] = features_df['overall_quality_score'] / 10
        features_df['high_quality'] = (features_df['overall_quality_score'] >= 7).astype(int)
        features_df['low_quality'] = (features_df['overall_quality_score'] <= 3).astype(int)

        # Individual fundamental scores
        features_df['growth_normalized'] = features_df['growth_score'] / 10
        features_df['profitability_normalized'] = features_df['profitability_score'] / 10
        features_df['health_normalized'] = features_df['financial_health_score'] / 10
        features_df['valuation_normalized'] = features_df['valuation_score'] / 10

        # Quality grade encoding
        grade_mapping = {'A': 4, 'B': 3, 'C': 2, 'D': 1}
        features_df['quality_grade_numeric'] = features_df['quality_grade'].map(grade_mapping).fillna(2)

        # High-quality flags
        features_df['grade_A'] = (features_df['quality_grade'] == 'A').astype(int)
        features_df['grade_B'] = (features_df['quality_grade'] == 'B').astype(int)
        features_df['grade_C'] = (features_df['quality_grade'] == 'C').astype(int)
        features_df['grade_D'] = (features_df['quality_grade'] == 'D').astype(int)

        # Financial metrics
        features_df['pe_reasonable'] = ((features_df['pe_ratio'] >= 10) & (features_df['pe_ratio'] <= 30)).astype(int)
        features_df['pb_reasonable'] = ((features_df['pb_ratio'] >= 1) & (features_df['pb_ratio'] <= 5)).astype(int)
        features_df['log_market_cap'] = np.log1p(features_df['market_cap'])
        features_df['low_beta'] = (features_df['beta'] < 1.2).astype(int)
        features_df['high_beta'] = (features_df['beta'] > 1.5).astype(int)

        # =============================================================
        # SECTOR FEATURES
        # =============================================================

        # Get top sectors
        top_sectors = features_df['sector'].value_counts().head(8).index.tolist()
        for sector in top_sectors:
            features_df[f'sector_{sector.replace(" ", "_").replace("&", "and")}'] = (
                        features_df['sector'] == sector).astype(int)

        # =============================================================
        # INTERACTION FEATURES (where the magic happens!)
        # =============================================================

        # Technical + Fundamental interactions
        features_df['quality_volume'] = features_df['overall_quality_score'] * features_df['volume_ratio']
        features_df['quality_momentum'] = features_df['overall_quality_score'] * abs(features_df['price_change_pct'])
        features_df['quality_rsi'] = features_df['overall_quality_score'] * features_df['rsi_normalized']

        # Risk-adjusted quality
        features_df['risk_adjusted_quality'] = features_df['overall_quality_score'] * (1 / (1 + features_df['atr_pct']))

        # Growth momentum
        features_df['growth_momentum'] = features_df['growth_score'] * abs(features_df['price_change_pct'])

        # Volume + Quality combination
        features_df['volume_quality_score'] = features_df['volume_strength'] * features_df['quality_normalized']

        # High quality + high volume flag
        features_df['premium_breakout'] = ((features_df['overall_quality_score'] >= 7) &
                                           (features_df['volume_ratio'] >= 2.0)).astype(int)

        # Select features for training
        feature_columns = [
            # Technical features
            'is_bullish', 'volume_ratio', 'volume_strength', 'high_volume',
            'rsi_value', 'rsi_normalized', 'rsi_overbought', 'rsi_oversold', 'rsi_neutral',
            'price_change_pct', 'price_momentum', 'strong_move',
            'atr_pct', 'high_volatility', 'low_volatility',

            # Fundamental features
            'overall_quality_score', 'quality_normalized', 'high_quality', 'low_quality',
            'growth_score', 'growth_normalized', 'profitability_score', 'profitability_normalized',
            'financial_health_score', 'health_normalized', 'valuation_score', 'valuation_normalized',
            'quality_grade_numeric', 'grade_A', 'grade_B', 'grade_C', 'grade_D',
            'pe_reasonable', 'pb_reasonable', 'log_market_cap', 'low_beta', 'high_beta',

            # Interaction features
            'quality_volume', 'quality_momentum', 'quality_rsi', 'risk_adjusted_quality',
            'growth_momentum', 'volume_quality_score', 'premium_breakout'
        ]

        # Add sector features
        sector_features = [col for col in features_df.columns if col.startswith('sector_')]
        feature_columns.extend(sector_features)

        # Filter to existing columns
        available_features = [col for col in feature_columns if col in features_df.columns]

        print(f"✅ Created {len(available_features)} HYBRID features:")

        # Categorize features for display
        technical_features = [f for f in available_features if f in [
            'is_bullish', 'volume_ratio', 'volume_strength', 'high_volume',
            'rsi_value', 'rsi_normalized', 'rsi_overbought', 'rsi_oversold', 'rsi_neutral',
            'price_change_pct', 'price_momentum', 'strong_move',
            'atr_pct', 'high_volatility', 'low_volatility'
        ]]

        fundamental_features = [f for f in available_features if f in [
            'overall_quality_score', 'quality_normalized', 'high_quality', 'low_quality',
            'growth_score', 'growth_normalized', 'profitability_score', 'profitability_normalized',
            'financial_health_score', 'health_normalized', 'valuation_score', 'valuation_normalized',
            'quality_grade_numeric', 'grade_A', 'grade_B', 'grade_C', 'grade_D',
            'pe_reasonable', 'pb_reasonable', 'log_market_cap', 'low_beta', 'high_beta'
        ]]

        interaction_features = [f for f in available_features if f in [
            'quality_volume', 'quality_momentum', 'quality_rsi', 'risk_adjusted_quality',
            'growth_momentum', 'volume_quality_score', 'premium_breakout'
        ]]

        print(f"   📊 Technical features: {len(technical_features)}")
        print(f"   💰 Fundamental features: {len(fundamental_features)}")
        print(f"   🔗 Interaction features: {len(interaction_features)}")
        print(f"   🏢 Sector features: {len(sector_features)}")

        return features_df, available_features

    def train_hybrid_model(self, df, feature_cols):
        """
        Train the hybrid AI model
        """
        print(f"\n🤖 TRAINING HYBRID AI MODEL (Technical + Fundamental)...")
        print("=" * 70)

        # Prepare data
        X = df[feature_cols].copy()
        y = df['success'].copy()

        # Handle missing values
        X = X.fillna(X.median())

        print(f"📊 Hybrid training data:")
        print(f"   Samples: {len(X)}")
        print(f"   Features: {len(feature_cols)}")
        print(f"   Success rate: {y.mean():.1%}")

        # Split data
        X_train, X_test, y_train, y_test = train_test_split(
            X, y, test_size=0.25, random_state=42, stratify=y
        )

        # Scale features
        X_train_scaled = self.scaler.fit_transform(X_train)
        X_test_scaled = self.scaler.transform(X_test)

        # Train enhanced Random Forest
        self.model = RandomForestClassifier(
            n_estimators=300,  # More trees for complex patterns
            max_depth=12,  # Deeper for fundamental interactions
            min_samples_split=5,
            min_samples_leaf=2,
            random_state=42,
            class_weight='balanced',
            bootstrap=True,
            oob_score=True,
            max_features='sqrt'  # Good for many features
        )

        print(f"🎯 Training hybrid Random Forest...")
        self.model.fit(X_train_scaled, y_train)

        # Evaluate
        train_accuracy = self.model.score(X_train_scaled, y_train)
        test_accuracy = self.model.score(X_test_scaled, y_test)
        oob_score = self.model.oob_score_

        # Predictions
        y_pred = self.model.predict(X_test_scaled)
        y_pred_proba = self.model.predict_proba(X_test_scaled)[:, 1]

        try:
            auc_score = roc_auc_score(y_test, y_pred_proba)
        except:
            auc_score = 0.5

        print(f"\n🎯 HYBRID MODEL PERFORMANCE:")
        print(f"   Training Accuracy: {train_accuracy:.1%}")
        print(f"   Test Accuracy: {test_accuracy:.1%}")
        print(f"   Out-of-Bag Score: {oob_score:.1%}")
        print(f"   AUC Score: {auc_score:.3f}")

        # Detailed report
        print(f"\n📈 DETAILED PERFORMANCE:")
        print(classification_report(y_test, y_pred, target_names=['Failed', 'Successful']))

        # Feature importance analysis
        print(f"\n🔍 TOP 15 FEATURE IMPORTANCE (Technical + Fundamental):")
        importances = self.model.feature_importances_
        self.feature_names = feature_cols

        feature_importance = sorted(zip(feature_cols, importances),
                                    key=lambda x: x[1], reverse=True)

        for i, (feature, importance) in enumerate(feature_importance[:15], 1):
            # Categorize feature type
            if any(keyword in feature for keyword in
                   ['quality', 'grade', 'growth', 'profitability', 'health', 'valuation', 'pe_', 'pb_', 'beta',
                    'market_cap']):
                feature_type = "💰"  # Fundamental
            elif any(keyword in feature for keyword in ['sector_']):
                feature_type = "🏢"  # Sector
            elif any(keyword in feature for keyword in
                     ['quality_volume', 'quality_momentum', 'risk_adjusted', 'premium_breakout']):
                feature_type = "🔗"  # Interaction
            else:
                feature_type = "📊"  # Technical

            print(f"   {i:2}. {feature_type} {feature:<25}: {importance:.3f}")

        return test_accuracy, auc_score, feature_importance

    def save_hybrid_model(self):
        """Save the hybrid model"""
        try:
            os.makedirs('../../models', exist_ok=True)
            timestamp = datetime.now().strftime('%Y%m%d_%H%M')

            model_path = f"../models/hybrid_ai_{timestamp}.joblib"
            scaler_path = f"../models/hybrid_scaler_{timestamp}.joblib"
            features_path = f"../models/hybrid_features_{timestamp}.txt"

            joblib.dump(self.model, model_path)
            joblib.dump(self.scaler, scaler_path)

            with open(features_path, 'w') as f:
                f.write('\n'.join(self.feature_names))

            print(f"\n💾 HYBRID MODEL SAVED:")
            print(f"   Model: {model_path}")
            print(f"   Scaler: {scaler_path}")
            print(f"   Features: {features_path}")

            return model_path

        except Exception as e:
            print(f"⚠️  Could not save hybrid model: {e}")
            return None


def main():
    """Train hybrid AI model"""
    print("🚀 HYBRID AI TRAINER: Technical + Fundamental Analysis")
    print("=" * 80)

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
        # Initialize predictor
        predictor = HybridBreakoutPredictor(db_path)

        # Load comprehensive data
        df = predictor.load_comprehensive_data()

        if len(df) < 20:
            print(f"❌ Not enough data: {len(df)} breakouts")
            return

        # Create hybrid features
        df_features, feature_cols = predictor.create_hybrid_features(df)

        # Train hybrid model
        accuracy, auc, feature_importance = predictor.train_hybrid_model(df_features, feature_cols)

        if accuracy and accuracy > 0.55:
            model_path = predictor.save_hybrid_model()

            print(f"\n🎉 SUCCESS! HYBRID AI TRAINED!")
            print(f"=" * 60)
            print(f"🎯 Test Accuracy: {accuracy:.1%}")
            print(f"📊 AUC Score: {auc:.3f}")

            # Compare to previous model
            print(f"\n📈 IMPROVEMENT vs Technical-Only Model:")
            print(f"   Previous (Technical only): 78.9%")
            print(f"   New (Technical + Fundamental): {accuracy:.1%}")

            if accuracy > 0.789:
                improvement = (accuracy - 0.789) * 100
                print(f"   🚀 IMPROVEMENT: +{improvement:.1f} percentage points!")

            print(f"\n🧠 YOUR HYBRID AI NOW KNOWS:")
            print(f"   📊 Technical patterns (volume, RSI, momentum)")
            print(f"   💰 Company quality (growth, profitability, health)")
            print(f"   🏢 Sector dynamics (tech vs healthcare vs finance)")
            print(f"   🔗 Combined factors (quality + volume + momentum)")

            print(f"\n🎯 NEXT STEPS:")
            print(f"   1. Test hybrid predictions vs technical-only")
            print(f"   2. See which fundamentals matter most")
            print(f"   3. Build confidence-based screening")
            print(f"   4. Integrate into live trading system")

        else:
            print(f"\n⚠️  Hybrid model needs improvement: {accuracy:.1%}")

        return predictor

    except Exception as e:
        print(f"❌ Hybrid training failed: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    predictor = main()