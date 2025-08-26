# ml_training/breakout_predictor.py
# ML training system for predicting high-quality Donchian breakouts

import pandas as pd
import numpy as np
import psycopg2
from psycopg2.extras import RealDictCursor
import joblib
import os
from datetime import datetime, timedelta
import logging
from typing import Dict, List, Optional, Tuple, Any
from sklearn.model_selection import train_test_split, GridSearchCV, cross_val_score
from sklearn.ensemble import RandomForestClassifier, GradientBoostingClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import StandardScaler, LabelEncoder
from sklearn.metrics import classification_report, confusion_matrix, roc_auc_score
from sklearn.feature_selection import SelectKBest, f_classif
import warnings
from dotenv import load_dotenv

warnings.filterwarnings('ignore')
load_dotenv()

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('logs/ml_training.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)


class BreakoutPredictor:
    """ML system for predicting successful Donchian breakouts"""

    def __init__(self):
        """Initialize ML predictor with database connection"""
        self.db_config = {
            'host': os.getenv('POSTGRES_HOST', 'localhost'),
            'port': os.getenv('POSTGRES_PORT', '5432'),
            'database': os.getenv('POSTGRES_DB', 'trading_production'),
            'user': os.getenv('POSTGRES_USER', 'trading_user'),
            'password': os.getenv('POSTGRES_PASSWORD', '')
        }

        # Create directories
        os.makedirs('models', exist_ok=True)
        os.makedirs('logs', exist_ok=True)

        # Model storage
        self.models = {}
        self.scalers = {}
        self.feature_selectors = {}
        self.label_encoders = {}

        # Feature configurations
        self.technical_features = [
            'volume_ratio', 'atr_pct', 'rsi_14', 'tech_volume_ratio',
            'entry_price', 'price_change_pct'
        ]

        self.fundamental_features = [
            'overall_quality_score', 'pe_ratio', 'pb_ratio', 'market_cap_log',
            'beta', 'dividend_yield'
        ]

        logger.info("Breakout Predictor initialized")

    def get_connection(self):
        """Get PostgreSQL connection"""
        try:
            conn = psycopg2.connect(**self.db_config)
            return conn
        except Exception as e:
            logger.error(f"Database connection failed: {e}")
            raise

    def load_training_data(self) -> pd.DataFrame:
        """Load ML training data from database"""
        conn = self.get_connection()
        try:
            cursor = conn.cursor(cursor_factory=RealDictCursor)

            # Load comprehensive training data
            query = """
            SELECT 
                ml.*,
                df.pe_ratio,
                df.pb_ratio,
                df.market_cap,
                df.beta,
                df.dividend_yield,
                df.industry
            FROM ml_training_data ml
            LEFT JOIN daily_fundamentals df ON ml.symbol = df.symbol
                AND df.date = (SELECT MAX(date) FROM daily_fundamentals WHERE symbol = ml.symbol)
            WHERE ml.success IS NOT NULL
                AND ml.max_gain_10d IS NOT NULL
                AND ml.overall_quality_score IS NOT NULL
            ORDER BY ml.date DESC
            """

            cursor.execute(query)
            data = cursor.fetchall()

            if not data:
                logger.error("No training data found")
                return pd.DataFrame()

            # Convert to DataFrame
            df = pd.DataFrame(data)
            logger.info(f"Loaded {len(df)} training samples")

            return df

        except Exception as e:
            logger.error(f"Error loading training data: {e}")
            return pd.DataFrame()
        finally:
            conn.close()

    def engineer_features(self, df: pd.DataFrame) -> pd.DataFrame:
        """Engineer features for ML training"""
        try:
            logger.info("Engineering features...")

            # Create copy to avoid modifying original
            features_df = df.copy()

            # Market cap transformation (log scale)
            features_df['market_cap_log'] = np.log1p(features_df['market_cap'].fillna(0))

            # Quality score bins
            features_df['quality_tier'] = pd.cut(
                features_df['overall_quality_score'].fillna(50),
                bins=[0, 40, 60, 80, 100],
                labels=['low', 'medium', 'high', 'excellent']
            )

            # Volume strength indicator
            features_df['volume_strength'] = np.where(
                features_df['volume_ratio'] > 2.0, 'high',
                np.where(features_df['volume_ratio'] > 1.5, 'medium', 'low')
            )

            # RSI momentum
            features_df['rsi_momentum'] = np.where(
                features_df['rsi_14'] > 70, 'overbought',
                np.where(features_df['rsi_14'] < 30, 'oversold', 'neutral')
            )

            # Breakout strength score
            features_df['breakout_strength'] = (
                    features_df['volume_ratio'] * 0.4 +
                    (features_df['overall_quality_score'] / 100) * 0.3 +
                    (features_df['atr_pct'] / 10) * 0.3
            )

            # Success probability based on historical patterns
            features_df['sector_success_rate'] = features_df.groupby('sector')['success'].transform('mean')

            # Handle missing values
            numeric_columns = features_df.select_dtypes(include=[np.number]).columns
            features_df[numeric_columns] = features_df[numeric_columns].fillna(0)

            logger.info(f"Feature engineering completed. Shape: {features_df.shape}")
            return features_df

        except Exception as e:
            logger.error(f"Error in feature engineering: {e}")
            return df

    def prepare_ml_dataset(self, df: pd.DataFrame) -> Tuple[pd.DataFrame, pd.Series]:
        """Prepare dataset for ML training"""
        try:
            # Engineer features
            df_features = self.engineer_features(df)

            # Select features for training
            feature_columns = []

            # Add technical features (that exist)
            for feat in self.technical_features:
                if feat in df_features.columns:
                    feature_columns.append(feat)

            # Add fundamental features (that exist)
            for feat in self.fundamental_features:
                if feat in df_features.columns:
                    feature_columns.append(feat)

            # Add engineered features
            additional_features = ['breakout_strength', 'sector_success_rate']
            for feat in additional_features:
                if feat in df_features.columns:
                    feature_columns.append(feat)

            # Encode categorical features
            categorical_features = ['breakout_type', 'sector', 'quality_tier', 'volume_strength', 'rsi_momentum']

            for cat_feat in categorical_features:
                if cat_feat in df_features.columns:
                    # Create label encoder for this feature
                    le = LabelEncoder()
                    df_features[f'{cat_feat}_encoded'] = le.fit_transform(df_features[cat_feat].fillna('unknown'))
                    feature_columns.append(f'{cat_feat}_encoded')

                    # Store encoder for later use
                    self.label_encoders[cat_feat] = le

            # Create feature matrix
            X = df_features[feature_columns].copy()

            # Create target variable (success)
            y = df_features['success'].astype(int)

            # Handle any remaining NaN values
            X = X.fillna(0)

            logger.info(f"ML dataset prepared: {X.shape[0]} samples, {X.shape[1]} features")
            logger.info(f"Feature columns: {feature_columns}")
            logger.info(f"Success rate: {y.mean():.2%}")

            return X, y

        except Exception as e:
            logger.error(f"Error preparing ML dataset: {e}")
            return pd.DataFrame(), pd.Series()

    def train_models(self, X: pd.DataFrame, y: pd.Series) -> Dict[str, Any]:
        """Train multiple ML models for breakout prediction"""
        try:
            logger.info("Starting model training...")

            # Split data
            X_train, X_test, y_train, y_test = train_test_split(
                X, y, test_size=0.2, random_state=42, stratify=y
            )

            # Scale features
            scaler = StandardScaler()
            X_train_scaled = scaler.fit_transform(X_train)
            X_test_scaled = scaler.transform(X_test)

            # Store scaler
            self.scalers['breakout_predictor'] = scaler

            # Feature selection
            selector = SelectKBest(score_func=f_classif, k=min(15, X_train.shape[1]))
            X_train_selected = selector.fit_transform(X_train_scaled, y_train)
            X_test_selected = selector.transform(X_test_scaled)

            # Store feature selector
            self.feature_selectors['breakout_predictor'] = selector

            # Define models to train
            models_config = {
                'random_forest': {
                    'model': RandomForestClassifier(random_state=42),
                    'params': {
                        'n_estimators': [100, 200],
                        'max_depth': [10, 15, None],
                        'min_samples_split': [5, 10],
                        'min_samples_leaf': [2, 5]
                    }
                },
                'gradient_boosting': {
                    'model': GradientBoostingClassifier(random_state=42),
                    'params': {
                        'n_estimators': [100, 200],
                        'learning_rate': [0.05, 0.1],
                        'max_depth': [5, 7],
                        'min_samples_split': [5, 10]
                    }
                },
                'logistic_regression': {
                    'model': LogisticRegression(random_state=42, max_iter=1000),
                    'params': {
                        'C': [0.1, 1.0, 10.0],
                        'penalty': ['l1', 'l2'],
                        'solver': ['liblinear']
                    }
                }
            }

            training_results = {}

            # Train each model
            for model_name, config in models_config.items():
                logger.info(f"Training {model_name}...")

                # Grid search for best parameters
                grid_search = GridSearchCV(
                    config['model'],
                    config['params'],
                    cv=5,
                    scoring='roc_auc',
                    n_jobs=-1
                )

                # Use scaled and selected features for training
                grid_search.fit(X_train_selected, y_train)

                # Best model
                best_model = grid_search.best_estimator_

                # Make predictions
                y_pred = best_model.predict(X_test_selected)
                y_pred_proba = best_model.predict_proba(X_test_selected)[:, 1]

                # Calculate metrics
                auc_score = roc_auc_score(y_test, y_pred_proba)

                # Cross-validation score
                cv_scores = cross_val_score(best_model, X_train_selected, y_train, cv=5, scoring='roc_auc')

                # Store model and results
                self.models[model_name] = best_model

                training_results[model_name] = {
                    'best_params': grid_search.best_params_,
                    'best_cv_score': grid_search.best_score_,
                    'test_auc': auc_score,
                    'cv_mean': cv_scores.mean(),
                    'cv_std': cv_scores.std(),
                    'classification_report': classification_report(y_test, y_pred, output_dict=True)
                }

                logger.info(f"{model_name} - Test AUC: {auc_score:.3f}, CV Mean: {cv_scores.mean():.3f}")

            # Select best model based on CV score
            best_model_name = max(training_results.keys(), key=lambda x: training_results[x]['best_cv_score'])
            logger.info(f"Best model: {best_model_name}")

            return {
                'training_results': training_results,
                'best_model': best_model_name,
                'feature_names': X.columns.tolist(),
                'training_samples': len(X_train),
                'test_samples': len(X_test)
            }

        except Exception as e:
            logger.error(f"Error in model training: {e}")
            return {}

    def save_models(self) -> bool:
        """Save trained models and preprocessing objects"""
        try:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

            # Save models
            for model_name, model in self.models.items():
                model_path = f'models/{model_name}_breakout_predictor_{timestamp}.joblib'
                joblib.dump(model, model_path)
                logger.info(f"Saved {model_name} to {model_path}")

            # Save preprocessing objects
            preprocessing_objects = {
                'scalers': self.scalers,
                'feature_selectors': self.feature_selectors,
                'label_encoders': self.label_encoders
            }

            preprocessing_path = f'models/preprocessing_objects_{timestamp}.joblib'
            joblib.dump(preprocessing_objects, preprocessing_path)

            # Save latest model references
            latest_models_info = {
                'timestamp': timestamp,
                'models': list(self.models.keys()),
                'preprocessing_path': preprocessing_path
            }

            info_path = 'models/latest_models_info.joblib'
            joblib.dump(latest_models_info, info_path)

            logger.info(f"All models and preprocessing objects saved with timestamp {timestamp}")
            return True

        except Exception as e:
            logger.error(f"Error saving models: {e}")
            return False

    def predict_breakout_success(self, features: Dict[str, Any], model_name: str = 'random_forest') -> Dict[str, Any]:
        """Predict breakout success probability for new data"""
        try:
            if model_name not in self.models:
                logger.error(f"Model {model_name} not found")
                return {'success': False, 'error': 'Model not found'}

            # Convert features to DataFrame
            feature_df = pd.DataFrame([features])

            # Apply preprocessing
            if 'breakout_predictor' in self.scalers:
                feature_scaled = self.scalers['breakout_predictor'].transform(feature_df)

                if 'breakout_predictor' in self.feature_selectors:
                    feature_selected = self.feature_selectors['breakout_predictor'].transform(feature_scaled)

                    # Make prediction
                    model = self.models[model_name]
                    prediction = model.predict(feature_selected)[0]
                    probability = model.predict_proba(feature_selected)[0, 1]

                    return {
                        'success': True,
                        'prediction': bool(prediction),
                        'probability': float(probability),
                        'confidence': 'high' if probability > 0.7 or probability < 0.3 else 'medium'
                    }

            return {'success': False, 'error': 'Preprocessing objects not found'}

        except Exception as e:
            logger.error(f"Error in prediction: {e}")
            return {'success': False, 'error': str(e)}

    def run_training_pipeline(self) -> bool:
        """Run complete ML training pipeline"""
        start_time = datetime.now()
        logger.info("🚀 Starting ML training pipeline...")

        try:
            # Load training data
            df = self.load_training_data()
            if df.empty:
                logger.error("No training data available")
                return False

            # Prepare dataset
            X, y = self.prepare_ml_dataset(df)
            if X.empty:
                logger.error("Failed to prepare ML dataset")
                return False

            # Train models
            training_results = self.train_models(X, y)
            if not training_results:
                logger.error("Model training failed")
                return False

            # Save models
            if not self.save_models():
                logger.error("Failed to save models")
                return False

            duration = datetime.now() - start_time

            logger.info(f"""
            🎉 ML Training Pipeline Completed in {duration}:
            ✅ Training samples: {training_results.get('training_samples', 0)}
            ✅ Test samples: {training_results.get('test_samples', 0)}
            ✅ Best model: {training_results.get('best_model', 'unknown')}
            ✅ Models trained: {len(self.models)}
            ✅ Models saved successfully
            """)

            return True

        except Exception as e:
            logger.error(f"Training pipeline failed: {e}")
            return False


def main():
    """Main function for ML training"""
    import argparse

    parser = argparse.ArgumentParser(description='Breakout Predictor ML Training')
    parser.add_argument('--train', action='store_true', help='Run training pipeline')
    parser.add_argument('--predict', help='Predict for specific symbol')

    args = parser.parse_args()

    predictor = BreakoutPredictor()

    if args.train:
        logger.info("🧠 Running ML training pipeline...")
        success = predictor.run_training_pipeline()

        if success:
            logger.info("✅ Training completed successfully!")
        else:
            logger.error("❌ Training failed!")

        return success

    elif args.predict:
        # Example prediction (you'd get real features from database)
        example_features = {
            'volume_ratio': 2.5,
            'atr_pct': 3.2,
            'rsi_14': 65,
            'overall_quality_score': 75,
            'breakout_strength': 1.8
        }

        result = predictor.predict_breakout_success(example_features)
        print(f"Prediction result: {result}")

    else:
        print("Use --train to run training or --predict SYMBOL to make prediction")


if __name__ == "__main__":
    main()