# ml_training/setup_ml_environment.py
# -*- coding: utf-8 -*-
import os
import sys
from datetime import datetime

class MLEnvironmentSetup:
    """
    Setup script to create ML training environment and validate dependencies
    """
    
    def __init__(self, base_dir="."):
        self.base_dir = base_dir
        self.ml_dir = os.path.join(base_dir, "ml_training")
        
    def create_directory_structure(self):
        """Create the complete ML training directory structure"""
        print("� Creating ML training directory structure...")
        
        directories = [
            "ml_training",
            "ml_training/data_preparation",
            "ml_training/models", 
            "ml_training/training",
            "ml_training/evaluation",
            "ml_training/deployment",
            "ml_training/scripts",
            "ml_training/notebooks",
            "ml_training/logs",
            "models",  # Root level models directory
            "models/production",
            "models/archive",
            "models/experiments"
        ]
        
        for directory in directories:
            dir_path = os.path.join(self.base_dir, directory)
            os.makedirs(dir_path, exist_ok=True)
            print(f"   ✅ {directory}")
        
        # Create __init__.py files for Python packages
        package_dirs = [
            "ml_training",
            "ml_training/data_preparation", 
            "ml_training/models",
            "ml_training/training",
            "ml_training/evaluation",
            "ml_training/deployment"
        ]
        
        for package_dir in package_dirs:
            init_file = os.path.join(self.base_dir, package_dir, "__init__.py")
            if not os.path.exists(init_file):
                with open(init_file, 'w') as f:
                    f.write(f'# {package_dir} package\n')
        
        print("✅ Directory structure created successfully")
    
    def create_config_template(self):
        """Create configuration template for ML training"""
        print("⚙️ Creating ML configuration template...")
        
        config_content = '''# ml_training/config/ml_config.py

import os
from dataclasses import dataclass
from typing import Dict, List, Optional

@dataclass
class MLConfig:
    """Configuration for ML training pipeline"""
    
    # Database settings
    DB_HOST: str = os.getenv('DB_HOST', 'localhost')
    DB_PORT: str = os.getenv('DB_PORT', '5432') 
    DB_NAME: str = os.getenv('DB_NAME', 'trading_production')
    DB_USER: str = os.getenv('DB_USER', 'trading_user')
    DB_PASSWORD: str = os.getenv('DB_PASSWORD', 'your_password')
    
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

# Global config instance
ml_config = MLConfig()
'''
        
        config_dir = os.path.join(self.ml_dir, "config")
        os.makedirs(config_dir, exist_ok=True)
        
        config_file = os.path.join(config_dir, "ml_config.py")
        with open(config_file, 'w') as f:
            f.write(config_content)
        
        # Create __init__.py for config package
        init_file = os.path.join(config_dir, "__init__.py")
        with open(init_file, 'w') as f:
            f.write("from .ml_config import ml_config\n")
        
        print("✅ ML configuration template created")
    
    def create_logging_config(self):
        """Create logging configuration for ML training"""
        print("� Creating logging configuration...")
        
        logging_content = '''# ml_training/utils/logging_config.py

import logging
import os
from datetime import datetime

def setup_ml_logging(log_level=logging.INFO):
    """Setup logging for ML training pipeline"""
    
    # Create logs directory
    log_dir = os.path.join(os.path.dirname(__file__), '../logs')
    os.makedirs(log_dir, exist_ok=True)
    
    # Create log filename with timestamp
    timestamp = datetime.now().strftime('%Y%m%d_%H%M')
    log_file = os.path.join(log_dir, f'ml_training_{timestamp}.log')
    
    # Configure logging
    logging.basicConfig(
        level=log_level,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        handlers=[
            logging.FileHandler(log_file),
            logging.StreamHandler()
        ]
    )
    
    logger = logging.getLogger('ml_training')
    logger.info(f"ML training logging initialized: {log_file}")
    
    return logger

# Default logger
ml_logger = setup_ml_logging()
'''
        
        utils_dir = os.path.join(self.ml_dir, "utils")
        os.makedirs(utils_dir, exist_ok=True)
        
        logging_file = os.path.join(utils_dir, "logging_config.py")
        with open(logging_file, 'w') as f:
            f.write(logging_content)
        
        # Create __init__.py for utils package
        init_file = os.path.join(utils_dir, "__init__.py")
        with open(init_file, 'w') as f:
            f.write("from .logging_config import ml_logger\n")
        
        print("✅ Logging configuration created")
    
    def create_model_registry(self):
        """Create model registry for version control"""
        print("� Creating model registry...")
        
        registry_content = '''# ml_training/deployment/model_registry.py

import os
import json
import shutil
from datetime import datetime
from typing import Dict, List, Optional
import joblib

class ModelRegistry:
    """
    Model registry for version control and deployment management
    """
    
    def __init__(self, models_dir="../models"):
        self.models_dir = models_dir
        self.production_dir = os.path.join(models_dir, "production")
        self.archive_dir = os.path.join(models_dir, "archive")
        self.experiments_dir = os.path.join(models_dir, "experiments")
        
        # Create directories
        for dir_path in [self.models_dir, self.production_dir, 
                        self.archive_dir, self.experiments_dir]:
            os.makedirs(dir_path, exist_ok=True)
        
        self.registry_file = os.path.join(models_dir, "model_registry.json")
        self.registry = self.load_registry()
    
    def load_registry(self) -> Dict:
        """Load model registry from file"""
        if os.path.exists(self.registry_file):
            with open(self.registry_file, 'r') as f:
                return json.load(f)
        return {"models": [], "production_model": None}
    
    def save_registry(self):
        """Save model registry to file"""
        with open(self.registry_file, 'w') as f:
            json.dump(self.registry, f, indent=2, default=str)
    
    def register_model(self, model_path: str, metadata: Dict) -> str:
        """Register a new model version"""
        timestamp = datetime.now().isoformat()
        model_id = f"momentum_predictor_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        
        # Move model to experiments directory
        experiment_path = os.path.join(self.experiments_dir, f"{model_id}.joblib")
        shutil.copy2(model_path, experiment_path)
        
        # Register in registry
        model_record = {
            "model_id": model_id,
            "path": experiment_path,
            "created_at": timestamp,
            "metadata": metadata,
            "status": "experiment"
        }
        
        self.registry["models"].append(model_record)
        self.save_registry()
        
        print(f"✅ Model registered: {model_id}")
        return model_id
    
    def promote_to_production(self, model_id: str) -> bool:
        """Promote model to production"""
        model_record = self.get_model(model_id)
        if not model_record:
            print(f"❌ Model {model_id} not found")
            return False
        
        # Archive current production model if exists
        if self.registry["production_model"]:
            self.archive_production_model()
        
        # Copy to production directory
        production_path = os.path.join(self.production_dir, "momentum_predictor_production.joblib")
        shutil.copy2(model_record["path"], production_path)
        
        # Update registry
        model_record["status"] = "production"
        model_record["promoted_at"] = datetime.now().isoformat()
        self.registry["production_model"] = model_id
        
        self.save_registry()
        
        print(f"✅ Model {model_id} promoted to production")
        return True
    
    def archive_production_model(self):
        """Archive current production model"""
        if not self.registry["production_model"]:
            return
        
        current_model = self.get_model(self.registry["production_model"])
        if current_model:
            archive_path = os.path.join(
                self.archive_dir, 
                f"{current_model['model_id']}_archived.joblib"
            )
            production_path = os.path.join(self.production_dir, "momentum_predictor_production.joblib")
            
            if os.path.exists(production_path):
                shutil.move(production_path, archive_path)
            
            current_model["status"] = "archived"
            current_model["archived_at"] = datetime.now().isoformat()
    
    def get_model(self, model_id: str) -> Optional[Dict]:
        """Get model record by ID"""
        for model in self.registry["models"]:
            if model["model_id"] == model_id:
                return model
        return None
    
    def list_models(self, status: Optional[str] = None) -> List[Dict]:
        """List models, optionally filtered by status"""
        if status:
            return [m for m in self.registry["models"] if m["status"] == status]
        return self.registry["models"]
    
    def get_production_model_path(self) -> Optional[str]:
        """Get path to current production model"""
        production_path = os.path.join(self.production_dir, "momentum_predictor_production.joblib")
        if os.path.exists(production_path):
            return production_path
        return None

# Global registry instance
model_registry = ModelRegistry()
'''
        
        deployment_dir = os.path.join(self.ml_dir, "deployment")
        os.makedirs(deployment_dir, exist_ok=True)
        
        registry_file = os.path.join(deployment_dir, "model_registry.py")
        with open(registry_file, 'w') as f:
            f.write(registry_content)
        
        print("✅ Model registry created")
    
    def check_dependencies(self):
        """Check if required dependencies are installed"""
        print("� Checking ML dependencies...")
        
        required_packages = [
            'pandas',
            'numpy', 
            'scikit-learn',
            'xgboost',
            'psycopg2',
            'joblib'
        ]
        
        missing_packages = []
        
        for package in required_packages:
            try:
                __import__(package.replace('-', '_'))
                print(f"   ✅ {package}")
            except ImportError:
                print(f"   ❌ {package}")
                missing_packages.append(package)
        
        if missing_packages:
            print(f"\n⚠️ Missing packages: {', '.join(missing_packages)}")
            print(f"Install with: pip install {' '.join(missing_packages)}")
            return False
        else:
            print("✅ All dependencies satisfied")
            return True
    
    def create_example_notebook(self):
        """Create an example Jupyter notebook for ML exploration"""
        print("� Creating example notebook...")
        
        notebook_content = '''{
 "cells": [
  {
   "cell_type": "markdown",
   "metadata": {},
   "source": [
    "# Momentum Breakout ML Analysis\\n",
    "\\n",
    "This notebook demonstrates the ML training pipeline for momentum breakout prediction."
   ]
  },
  {
   "cell_type": "code",
   "execution_count": null,
   "metadata": {},
   "outputs": [],
   "source": [
    "# Import libraries\\n",
    "import sys\\n",
    "sys.path.append('../')\\n",
    "\\n",
    "from data_preparation.momentum_labeler import MomentumLabeler\\n",
    "from data_preparation.feature_builder import SwingTradingFeatureBuilder\\n",
    "from models.momentum_predictor import MomentumBreakoutPredictor\\n",
    "\\n",
    "import pandas as pd\\n",
    "import numpy as np\\n",
    "import matplotlib.pyplot as plt\\n",
    "import seaborn as sns"
   ]
  },
  {
   "cell_type": "code",
   "execution_count": null,
   "metadata": {},
   "outputs": [],
   "source": [
    "# Load and explore momentum data\\n",
    "labeler = MomentumLabeler()\\n",
    "breakouts_df = labeler.load_breakout_data(limit=100)\\n",
    "breakouts_df.head()"
   ]
  },
  {
   "cell_type": "code",
   "execution_count": null,
   "metadata": {},
   "outputs": [],
   "source": [
    "# Train a model\\n",
    "predictor = MomentumBreakoutPredictor()\\n",
    "features_df, metadata_df = predictor.load_training_data(limit=100)\\n",
    "\\n",
    "if features_df is not None:\\n",
    "    X = predictor.prepare_features(features_df)\\n",
    "    y = metadata_df['target_binary']\\n",
    "    \\n",
    "    results = predictor.train_model(X, y)\\n",
    "    print(f\\"Model AUC: {results['auc_score']:.3f}\\")\\n",
    "else:\\n",
    "    print(\\"No training data available\\")"
   ]
  }
 ],
 "metadata": {
  "kernelspec": {
   "display_name": "Python 3",
   "language": "python",
   "name": "python3"
  },
  "language_info": {
   "codemirror_mode": {
    "name": "ipython",
    "version": 3
   },
   "file_extension": ".py",
   "name": "python",
   "nbconvert_exporter": "python",
   "pygments_lexer": "ipython3",
   "version": "3.8.5"
  }
 },
 "nbformat": 4,
 "nbformat_minor": 4
}'''
        
        notebooks_dir = os.path.join(self.ml_dir, "notebooks")
        os.makedirs(notebooks_dir, exist_ok=True)
        
        notebook_file = os.path.join(notebooks_dir, "momentum_analysis_example.ipynb")
        with open(notebook_file, 'w') as f:
            f.write(notebook_content)
        
        print("✅ Example notebook created")
    
    def run_setup(self):
        """Run complete setup process"""
        print("� SETTING UP ML TRAINING ENVIRONMENT")
        print("=" * 50)
        
        self.create_directory_structure()
        self.create_config_template()
        self.create_logging_config()
        self.create_model_registry()
        self.create_example_notebook()
        
        dependencies_ok = self.check_dependencies()
        
        print(f"\n� ML ENVIRONMENT SETUP COMPLETE!")
        print(f"� Directory structure created")
        print(f"⚙️ Configuration templates ready")
        print(f"� Model registry initialized")
        print(f"� Example notebook created")
        
        if dependencies_ok:
            print(f"✅ All dependencies satisfied")
        else:
            print(f"⚠️ Install missing dependencies from requirements.txt")
        
        print(f"\n� NEXT STEPS:")
        print(f"   1. Install dependencies: pip install -r ml_training/requirements.txt")
        print(f"   2. Configure database credentials in .env file")
        print(f"   3. Run pipeline: python ml_training/scripts/ml_pipeline_runner.py test")
        
        return True

def main():
    """Main setup function"""
    setup = MLEnvironmentSetup()
    setup.run_setup()

if __name__ == "__main__":
    main()
