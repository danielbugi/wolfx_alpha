# ml_training/utils/logging_config.py

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
