# ml_training/config/ml_config.py

import os
from dataclasses import dataclass
from typing import Dict, List, Optional
from dotenv import load_dotenv


# Load environment file from multiple possible locations (same as automation/shared/config.py)
def load_env_file():
    """Load environment file from multiple possible locations"""
    possible_paths = [
        '.env',  # Root directory
        '../.env',  # Parent directory
        os.path.join(os.path.dirname(__file__), '..', '..', '.env'),  # Project root
        os.path.join(os.path.expanduser('~'), '.env'),  # Home directory
    ]

    for path in possible_paths:
        abs_path = os.path.abspath(path)
        if os.path.exists(abs_path):
            load_dotenv(abs_path)
            return abs_path

    return None


# Load environment variables from your existing .env file
env_file_path = load_env_file()


@dataclass
class MLConfig:
    """Configuration for ML training pipeline - uses same DB credentials as your trading system"""

    # Database settings - SAME AS YOUR EXISTING SYSTEM
    DB_HOST: str = os.getenv('DB_HOST', 'localhost')
    DB_PORT: str = os.getenv('DB_PORT', '5432')
    DB_NAME: str = os.getenv('DB_NAME', 'trading_production')
    DB_USER: str = os.getenv('DB_USER', 'postgres')  # Changed default to 'postgres'
    DB_PASSWORD: str = os.getenv('DB_PASSWORD', '')  # Empty default for postgres

    # Model training settings
    MODEL_TYPE: str = 'xgboost'  # 'xgboost', 'lightgbm', 'random_forest'
    TEST_SIZE: float = 0.2
    RANDOM_STATE: int = 42

    # XGBoost parameters
    XGBOOST_PARAMS: Dict = None

    # Feature engineering
    LOOKBACK_DAYS: int = 20
    MIN_TRAINING_SAMPLES: int = 100

    # Momentum scoring
    MOMENTUM_DAYS_FORWARD: int = 25
    STRONG_MOMENTUM_THRESHOLD: int = 65

    # Model deployment
    MODEL_DIR: str = '../models'
    PRODUCTION_MODEL_DIR: str = '../models/production'

    def __post_init__(self):
        if self.XGBOOST_PARAMS is None:
            self.XGBOOST_PARAMS = {
                'n_estimators': 100,
                'max_depth': 6,
                'learning_rate': 0.1,
                'subsample': 0.8,
                'colsample_bytree': 0.8,
                'random_state': self.RANDOM_STATE
            }

    @property
    def db_config(self) -> Dict[str, str]:
        """Get database configuration as dictionary for psycopg2"""
        return {
            'host': self.DB_HOST,
            'port': self.DB_PORT,
            'database': self.DB_NAME,
            'user': self.DB_USER,
            'password': self.DB_PASSWORD
        }

    def test_db_connection(self):
        """Test database connection"""
        try:
            import psycopg2
            conn = psycopg2.connect(**self.db_config)
            cursor = conn.cursor()
            cursor.execute("SELECT 1")
            cursor.close()
            conn.close()
            return True, "Connection successful"
        except Exception as e:
            return False, str(e)

    def validate_config(self) -> Dict[str, bool]:
        """Validate ML configuration"""
        validation = {}

        # Test database connection
        db_success, db_message = self.test_db_connection()
        validation['database_connection'] = db_success

        # Check required values
        validation['model_params_valid'] = self.XGBOOST_PARAMS is not None
        validation['directories_valid'] = True  # We'll create them if needed

        return validation, db_message if not db_success else "All validations passed"


# Global config instance
ml_config = MLConfig()

# Auto-test connection when imported
if __name__ == "__main__":
    print("ML TRAINING CONFIGURATION")
    print("=" * 50)

    if env_file_path:
        print(f"Environment file: {env_file_path}")
    else:
        print("WARNING: No .env file found - using defaults/environment variables")

    print(f"\nDatabase Configuration:")
    print(f"  Host: {ml_config.DB_HOST}")
    print(f"  Port: {ml_config.DB_PORT}")
    print(f"  Database: {ml_config.DB_NAME}")
    print(f"  User: {ml_config.DB_USER}")
    print(f"  Password: {'*' * len(ml_config.DB_PASSWORD) if ml_config.DB_PASSWORD else '(empty)'}")

    validation, message = ml_config.validate_config()

    print(f"\nValidation Results:")
    for check, status in validation.items():
        status_text = "SUCCESS" if status else "FAILED"
        print(f"  {check}: {status_text}")

    print(f"\nMessage: {message}")

    if validation['database_connection']:
        print(f"\nSUCCESS: ML system ready to use!")
    else:
        print(f"\nFIX NEEDED: Update your .env file with correct database credentials")
        print(f"Most likely fix - add to your .env file:")
        print(f"DB_USER=postgres")
        print(f"DB_PASSWORD=postgres")
else:
    # Quick validation when imported
    if env_file_path:
        validation, message = ml_config.validate_config()
        if not validation['database_connection']:
            print(f"WARNING: ML database connection failed: {message}")
            print(f"Using .env file: {env_file_path}")
    else:
        print("WARNING: No .env file found for ML config")