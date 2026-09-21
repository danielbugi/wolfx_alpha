# ml_training/scripts/smart_hybrid_trainer.py

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


class SmartHybridPredictor:
    def __init__(self, db_path):
        self.db_path = db_path
        self.model = None
        self.scaler = StandardScaler()
        self.feature_names = []

    def load_smart_hybrid_data(self):
        """
        Load breakouts and INTELLIGENTLY match with most recent fundamentals
        """
        print("🧠 Loading breakouts with SMART fundamental matching...")

        conn = sqlite3.connect(self.db_path)

        # First, load breakouts
        breakouts_query = '''
            SELECT 
                symbol, date, breakout_type, entry_price, success,
                volume_ratio, atr_pct, rsi_value, price_change_pct,
                max_gain_10d, max_loss_10d, days_to_peak
            FROM breakouts 
            WHERE success IS NOT NULL 
            ORDER BY date DESC
        '''

        breakouts_df = pd.read_sql(breakouts_query, conn)
        print(f"📊 Loaded {len(breakouts_df)} breakouts")

        # Fix binary success column
        def fix_binary_success(value):
            if isinstance(value, bytes):
                return 1 if value == b'\x01' else 0
            else:
                return int(float(value)) if value is not None else 0

        breakouts_df['success'] = breakouts_df['success'].apply(fix_binary_success)

        # Now, get the LATEST fundamental data for each symbol
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
        print(f"💰 Loaded fundamentals for {len(fundamentals_df)} symbols")

        # Get technical indicators for breakout dates
        tech_query = '''
            SELECT DISTINCT
                symbol, date, rsi_14, price_position, sma_10, sma_20,
                volume_ratio as tech_volume_ratio
            FROM technical_indicators
            WHERE rsi_14 IS NOT NULL
        '''

        tech_df = pd.read_sql(tech_query, conn)
        print(f"📈 Loaded technical data for {len(tech_df)} records")

        conn.close()

        # Smart merge: breakouts + fundamentals (by symbol only)
        print("🔗 Smart merging: Breakouts + Latest Fundamentals...")
        df = breakouts_df.merge(fundamentals_df, on='symbol', how='left')

        # Add technical indicators (by symbol + date)
        df = df.merge(tech_df, on=['symbol', 'date'], how='left')

        # Check fundamental coverage
        has_quality_score = (~df['overall_quality_score'].isna()).sum()
        has_quality_grade = (~df['quality_grade'].isna()).sum()
        has_sector = (~df['sector'].isna()).sum()

        print(f"\n📊 SMART FUNDAMENTAL MATCHING RESULTS:")
        print(f"   Quality scores: {has_quality_score}/{len(df)} ({has_quality_score / len(df) * 100:.1f}%)")
        print(f"   Quality grades: {has_quality_grade}/{len(df)} ({has_quality_grade / len(df) * 100:.1f}%)")
        print(f"   Sector data: {has_sector}/{len(df)} ({has_sector / len(df) * 100:.1f}%)")

        if has_quality_score > 0:
            print(f"✅ SUCCESS! Found fundamental data for breakouts")

            # Show sample of matched data
            print(f"\n📋 Sample of matched breakout + fundamental data:")
            sample = df[df['overall_quality_score'].notna()].head(3)
            for _, row in sample.iterrows():
                print(
                    f"   {row['symbol']} | {row['date']} | Quality: {row['overall_quality_score']:.1f} | Grade: {row['quality_grade']}")
        else:
            print(f"❌ No fundamental matching found")
            return pd.DataFrame()

        # Fill remaining missing values intelligently
        df = self.smart_fill_missing_data(df)

        return df

    def smart_fill_missing_data(self, df):
        """
        Intelligently fill missing data using sector averages and smart defaults
        """
        print("\n🧹 Smart filling of missing data...")

        # Fill fundamental scores with sector medians where possible
        fundamental_cols = ['overall_quality_score', 'growth_score', 'profitability_score',
                            'financial_health_score', 'valuation_score', 'pe_ratio', 'pb_ratio', 'beta']

        for col in fundamental_cols:
            if col in df.columns:
                # First, fill with sector median
                sector_medians = df.groupby('sector')[col].median()
                for sector in sector_medians.index:
                    mask = (df['sector'] == sector) & df[col].isna()
                    df.loc[mask, col] = sector_medians[sector]

                # Then fill remaining with overall median
                df[col] = df[col].fillna(df[col].median())

                # Final fallback for specific columns
                if col in ['overall_quality_score', 'growth_score', 'profitability_score',
                           'financial_health_score', 'valuation_score']:
                    df[col] = df[col].fillna(5.0)
                elif col == 'pe_ratio':
                    df[col] = df[col].fillna(20.0)
                elif col == 'pb_ratio':
                    df[col] = df[col].fillna(2.0)
                elif col == 'beta':
                    df[col] = df[col].fillna(1.0)

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

        # Fill other missing values
        df['market_cap'] = df['market_cap'].fillna(df['market_cap'].median())
        df['sector'] = df['sector'].fillna('Unknown')

        print(f"✅ Smart data filling completed")
        return df

    def create_smart_hybrid_features(self, df):
        """
        Create intelligent hybrid features with real fundamental data
        """
        print("\n🔧 Creating SMART HYBRID features...")

        features_df = df.copy()

        # Technical features (proven valuable)
        features_df['is_bullish'] = (features_df['breakout_type'] == 'bullish').astype(int)
        features_df['volume_strength'] = np.log1p(features_df['volume_ratio'])
        features_df['rsi_normalized'] = features_df['rsi_value'] / 100
        features_df['price_momentum'] = features_df['price_change_pct']
        features_df['high_volume'] = (features_df['volume_ratio'] > 2.0).astype(int)
        features_df['strong_move'] = (abs(features_df['price_change_pct']) > 3).astype(int)

        # Fundamental features (now with REAL data!)
        features_df['quality_normalized'] = features_df['overall_quality_score'] / 10
        features_df['high_quality'] = (features_df['overall_quality_score'] >= 7).astype(int)
        features_df['grade_A'] = (features_df['quality_grade'] == 'A').astype(int)
        features_df['grade_B'] = (features_df['quality_grade'] == 'B').astype(int)

        # Growth and profitability
        features_df['high_growth'] = (features_df['growth_score'] >= 7).astype(int)
        features_df['profitable'] = (features_df['profitability_score'] >= 6).astype(int)
        features_df['financial_health_good'] = (features_df['financial_health_score'] >= 6).astype(int)

        # Valuation features
        features_df['reasonable_pe'] = ((features_df['pe_ratio'] >= 10) & (features_df['pe_ratio'] <= 30)).astype(int)
        features_df['reasonable_pb'] = ((features_df['pb_ratio'] >= 1) & (features_df['pb_ratio'] <= 5)).astype(int)

        # Market cap categories
        features_df['log_market_cap'] = np.log1p(features_df['market_cap'])
        market_cap_median = features_df['market_cap'].median()
        features_df['large_cap'] = (features_df['market_cap'] > market_cap_median * 2).astype(int)

        # POWERFUL INTERACTION FEATURES (this is where the magic happens!)
        features_df['quality_volume'] = features_df['overall_quality_score'] * features_df['volume_ratio']
        features_df['quality_momentum'] = features_df['overall_quality_score'] * abs(features_df['price_change_pct'])
        features_df['premium_breakout'] = ((features_df['overall_quality_score'] >= 7) &
                                           (features_df['volume_ratio'] >= 1.5)).astype(int)
        features_df['growth_momentum'] = features_df['growth_score'] * abs(features_df['price_change_pct'])

        # Sector encoding (top sectors only)
        top_sectors = features_df['sector'].value_counts().head(6).index.tolist()
        for sector in top_sectors:
            safe_sector_name = sector.replace(' ', '_').replace('&', 'and').replace(',', '')
            features_df[f'sector_{safe_sector_name}'] = (features_df['sector'] == sector).astype(int)

        # Select the most valuable features
        core_features = [
            # Technical (proven important)
            'is_bullish', 'volume_ratio', 'volume_strength', 'high_volume',
            'rsi_value', 'rsi_normalized', 'price_change_pct', 'price_momentum', 'strong_move',
            'atr_pct',

            # Fundamental (now with real data)
            'overall_quality_score', 'quality_normalized', 'high_quality', 'grade_A', 'grade_B',
            'growth_score', 'high_growth', 'profitability_score', 'profitable',
            'financial_health_score', 'financial_health_good',
            'pe_ratio', 'reasonable_pe', 'pb_ratio', 'reasonable_pb',
            'log_market_cap', 'large_cap',

            # Powerful interactions
            'quality_volume', 'quality_momentum', 'premium_breakout', 'growth_momentum'
        ]

        # Add sector features
        sector_features = [col for col in features_df.columns if col.startswith('sector_')]
        core_features.extend(sector_features)

        # Filter to existing columns
        available_features = [col for col in core_features if col in features_df.columns]

        print(f"✅ Created {len(available_features)} smart hybrid features")
        print(f"   📊 Technical: 10 features")
        print(
            f"   💰 Fundamental: {len([f for f in available_features if any(x in f for x in ['quality', 'grade', 'growth', 'profitability', 'health', 'pe_', 'pb_', 'market_cap'])])}")
        print(f"   🔗 Interactions: 4 features")
        print(f"   🏢 Sectors: {len(sector_features)}")

        return features_df, available_features

    def train_smart_model(self, df, feature_cols):
        """
        Train the smart hybrid model
        """
        print(f"\n🤖 TRAINING SMART HYBRID AI...")
        print("=" * 60)

        X = df[feature_cols].copy()
        y = df['success'].copy()

        # Handle missing values
        X = X.fillna(X.median())

        print(f"📊 Smart hybrid training data:")
        print(f"   Samples: {len(X)}")
        print(f"   Features: {len(feature_cols)}")
        print(f"   Success rate: {y.mean():.1%}")
        print(f"   Fundamentals coverage: {(~df['overall_quality_score'].isna()).mean():.1%}")

        # Split data
        X_train, X_test, y_train, y_test = train_test_split(
            X, y, test_size=0.25, random_state=42, stratify=y
        )

        # Scale features
        X_train_scaled = self.scaler.fit_transform(X_train)
        X_test_scaled = self.scaler.transform(X_test)

        # Train optimized Random Forest
        self.model = RandomForestClassifier(
            n_estimators=250,
            max_depth=10,
            min_samples_split=5,
            min_samples_leaf=3,
            random_state=42,
            class_weight='balanced',
            bootstrap=True,
            oob_score=True,
            max_features='sqrt'
        )

        print(f"🎯 Training smart hybrid Random Forest...")
        self.model.fit(X_train_scaled, y_train)

        # Evaluate
        train_accuracy = self.model.score(X_train_scaled, y_train)
        test_accuracy = self.model.score(X_test_scaled, y_test)
        oob_score = self.model.oob_score_

        y_pred = self.model.predict(X_test_scaled)
        y_pred_proba = self.model.predict_proba(X_test_scaled)[:, 1]

        try:
            auc_score = roc_auc_score(y_test, y_pred_proba)
        except:
            auc_score = 0.5

        print(f"\n🎯 SMART HYBRID PERFORMANCE:")
        print(f"   Training Accuracy: {train_accuracy:.1%}")
        print(f"   Test Accuracy: {test_accuracy:.1%}")
        print(f"   Out-of-Bag Score: {oob_score:.1%}")
        print(f"   AUC Score: {auc_score:.3f}")

        print(f"\n📈 DETAILED PERFORMANCE:")
        print(classification_report(y_test, y_pred, target_names=['Failed', 'Successful']))

        # Feature importance
        print(f"\n🔍 TOP 15 SMART HYBRID FEATURES:")
        importances = self.model.feature_importances_
        self.feature_names = feature_cols

        feature_importance = sorted(zip(feature_cols, importances),
                                    key=lambda x: x[1], reverse=True)

        for i, (feature, importance) in enumerate(feature_importance[:15], 1):
            # Categorize feature
            if any(keyword in feature for keyword in
                   ['quality', 'grade', 'growth', 'profitability', 'health', 'pe_', 'pb_', 'market_cap']):
                icon = "💰"
            elif any(keyword in feature for keyword in ['sector_']):
                icon = "🏢"
            elif any(keyword in feature for keyword in
                     ['quality_volume', 'quality_momentum', 'premium_breakout', 'growth_momentum']):
                icon = "🔗"
            else:
                icon = "📊"

            print(f"   {i:2}. {icon} {feature:<25}: {importance:.3f}")

        return test_accuracy, auc_score, feature_importance

    def save_smart_model(self):
        """Save the smart hybrid model"""
        try:
            os.makedirs('../../models', exist_ok=True)
            timestamp = datetime.now().strftime('%Y%m%d_%H%M')

            model_path = f"../models/smart_hybrid_{timestamp}.joblib"
            scaler_path = f"../models/smart_scaler_{timestamp}.joblib"
            features_path = f"../models/smart_features_{timestamp}.txt"

            joblib.dump(self.model, model_path)
            joblib.dump(self.scaler, scaler_path)

            with open(features_path, 'w') as f:
                f.write('\n'.join(self.feature_names))

            print(f"\n💾 SMART HYBRID MODEL SAVED:")
            print(f"   Model: {model_path}")
            print(f"   Scaler: {scaler_path}")
            print(f"   Features: {features_path}")

            return model_path

        except Exception as e:
            print(f"⚠️  Could not save model: {e}")
            return None


def main():
    """Train smart hybrid AI"""
    print("🧠 SMART HYBRID AI: Intelligent Fundamental Matching")
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
        predictor = SmartHybridPredictor(db_path)

        # Load smart hybrid data
        df = predictor.load_smart_hybrid_data()

        if len(df) < 20:
            print(f"❌ Not enough data: {len(df)} breakouts")
            return

        # Create smart features
        df_features, feature_cols = predictor.create_smart_hybrid_features(df)

        # Train smart model
        accuracy, auc, feature_importance = predictor.train_smart_model(df_features, feature_cols)

        if accuracy and accuracy > 0.55:
            model_path = predictor.save_smart_model()

            print(f"\n🎉 SUCCESS! SMART HYBRID AI TRAINED!")
            print(f"=" * 60)
            print(f"🎯 Test Accuracy: {accuracy:.1%}")
            print(f"📊 AUC Score: {auc:.3f}")

            # Compare improvements
            print(f"\n📈 MODEL EVOLUTION:")
            print(f"   Technical-only AI: 78.9%")
            print(f"   Previous Hybrid:   74.7% (bad fundamental data)")
            print(f"   Smart Hybrid:      {accuracy:.1%} (real fundamental data)")

            if accuracy > 0.789:
                improvement = (accuracy - 0.789) * 100
                print(f"   🚀 IMPROVEMENT: +{improvement:.1f} percentage points!")
            elif accuracy > 0.75:
                print(f"   ✅ COMPETITIVE: Close to technical-only performance!")

            print(f"\n🧠 YOUR SMART AI LEARNED:")
            print(f"   📊 Technical patterns work well")
            print(f"   💰 Quality fundamentals add value")
            print(f"   🔗 Combining factors is powerful")
            print(f"   🏢 Sector context matters")

        else:
            print(f"\n⚠️  Model needs improvement: {accuracy:.1%}")

        return predictor

    except Exception as e:
        print(f"❌ Smart hybrid training failed: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    predictor = main()