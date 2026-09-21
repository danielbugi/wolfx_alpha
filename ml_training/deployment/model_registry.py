# ml_training/deployment/model_registry.py

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
