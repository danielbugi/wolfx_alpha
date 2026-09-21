🤖 ML Training System for Momentum Breakout Prediction
A comprehensive machine learning system for predicting high-quality momentum breakouts in swing trading, built on top of your existing PostgreSQL trading infrastructure.

📋 System Overview
The ML training system enhances your existing breakout detection with momentum prediction capabilities:

Momentum Labeling: Creates sophisticated momentum scores for historical breakouts
Feature Engineering: Builds comprehensive features from technical, fundamental, and market data
XGBoost Training: Trains optimized models for momentum prediction
Backend Integration: Seamlessly integrates with your existing screening pipeline
Performance Monitoring: Tracks model performance and suggests improvements
🏗️ System Architecture
ml_training/
├── data_preparation/           # Data processing pipeline
│   ├── momentum_labeler.py     # Create momentum scores (0-100)
│   ├── feature_builder.py      # Engineer ML features
│   └── data_cleaner.py         # Validate PostgreSQL data
├── models/                     # ML model implementations
│   └── momentum_predictor.py   # XGBoost momentum predictor
├── training/                   # Training workflows
├── evaluation/                 # Performance tracking
│   └── performance_tracker.py  # Monitor model performance
├── deployment/                 # Production deployment
│   ├── ml_integration.py       # Backend integration
│   └── model_registry.py       # Model version control
├── scripts/                    # Automation scripts
│   └── ml_pipeline_runner.py   # Complete pipeline orchestration
├── config/                     # Configuration management
├── utils/                      # Logging and utilities
└── notebooks/                  # Jupyter notebooks for analysis
🚀 Quick Start
1. Setup Environment
bash
# Create ML directory structure
python ml_training/setup_ml_environment.py

# Install dependencies
pip install -r ml_training/requirements.txt

# Configure environment variables (same as your existing .env)
# DB_HOST, DB_PORT, DB_NAME, DB_USER, DB_PASSWORD
2. Run Complete Pipeline
bash
# Quick test (100 samples)
python ml_training/scripts/ml_pipeline_runner.py test

# Full pipeline (production)
python ml_training/scripts/ml_pipeline_runner.py full
3. Integrate with Daily Screening
bash
# Enhance existing breakout JSON with ML predictions
python -c "
from ml_training.deployment.ml_integration import MLBreakoutIntegration
integration = MLBreakoutIntegration()
integration.integrate_with_daily_screening('frontend_data/latest_breakouts.json')
"
📊 Pipeline Steps Explained
Step 1: Momentum Labeling
Creates momentum scores (0-100) for historical breakouts based on:

Trend Persistence: Days staying above/below breakout level
New Extremes: Creation of new highs/lows
Daily Consistency: Consistent daily moves in breakout direction
Volume Confirmation: Volume support during the move
Speed of Move: Rate of price appreciation
python
# File: ml_training/data_preparation/momentum_labeler.py
labeler = MomentumLabeler()
breakouts_df = labeler.load_breakout_data(limit=1000)
momentum_df = labeler.process_breakouts_batch(breakouts_df)
labeler.save_momentum_scores(momentum_df)
Step 2: Feature Engineering
Builds comprehensive ML features:

Technical Features: RSI, MACD, moving averages, Donchian position
Fundamental Features: Quality scores, valuation ratios, sector
Price Context: Momentum, volatility, volume patterns
Market Context: Market-wide conditions and sentiment
Interaction Features: Quality × Technical combinations
python
# File: ml_training/data_preparation/feature_builder.py
builder = SwingTradingFeatureBuilder()
training_df = builder.build_training_dataset(momentum_df)
Step 3: Model Training
Trains XGBoost model optimized for momentum prediction:

Time-Series Validation: Proper temporal data splits
Feature Selection: Automated feature importance ranking
Hyperparameter Tuning: Optimized for momentum detection
Performance Metrics: AUC, precision, recall for trading metrics
python
# File: ml_training/models/momentum_predictor.py
predictor = MomentumBreakoutPredictor()
features_df, metadata_df = predictor.load_training_data()
X = predictor.prepare_features(features_df)
y = metadata_df['target_binary']
results = predictor.train_model(X, y)
model_path = predictor.save_model()
Step 4: Integration & Deployment
Seamlessly integrates with your existing system:

Enhanced JSON Output: Adds ML predictions to your current breakout screening
Model Registry: Version control and production deployment
Performance Tracking: Monitors real-world performance
🎯 Enhanced Output Format
The ML system enhances your existing JSON output:

json
{
  "metadata": {
    "ml_enhanced": true,
    "model_version": "momentum_predictor_v20250713_1400"
  },
  "summary": {
    "high_confidence_signals": 23,
    "ml_avg_probability": 67.3
  },
  "signals": {
    "bullish_breakouts": [
      {
        "symbol": "AAPL",
        "type": "bullish_breakout",
        "current_price": 175.50,
        "ml_momentum_probability": 85.3,
        "ml_confidence_level": "high",
        "ml_predicted_category": "strong",
        "ml_trade_recommendation": "strong_buy",
        "ml_risk_score": 25
      }
    ]
  },
  "ml_insights": {
    "top_ml_picks": [...],
    "high_confidence": [...],
    "recommendations": {
      "strong_buy": [...],
      "buy": [...]
    }
  }
}
🔧 Configuration
ML Configuration
python
# ml_training/config/ml_config.py
@dataclass
class MLConfig:
    MODEL_TYPE: str = 'xgboost'
    STRONG_MOMENTUM_THRESHOLD: int = 65
    MOMENTUM_DAYS_FORWARD: int = 25
    LOOKBACK_DAYS: int = 20
Model Parameters
python
XGBOOST_PARAMS = {
    'n_estimators': 100,
    'max_depth': 6,
    'learning_rate': 0.1,
    'subsample': 0.8,
    'colsample_bytree': 0.8
}
📈 Performance Monitoring
Track Model Performance
bash
# Evaluate recent predictions
python -c "
from ml_training.evaluation.performance_tracker import MLPerformanceTracker
tracker = MLPerformanceTracker()
tracker.evaluate_predictions(days_back=30)
tracker.generate_performance_report()
"
Performance Metrics
Accuracy: Overall prediction correctness
Precision: Success rate of high-probability predictions
Recall: Capturing actual momentum opportunities
Sharpe Ratio: Risk-adjusted returns
Max Drawdown: Worst-case performance
🔄 Daily Workflow Integration
Manual Integration
bash
# 1. Run your existing daily updates
python automation/data_updaters/daily_data_updater.py
python automation/screeners/donchian_screener.py

# 2. Enhance with ML predictions
python ml_training/deployment/ml_integration.py

# 3. Track performance (weekly)
python ml_training/evaluation/performance_tracker.py
Automated Integration (Recommended)
Add to your existing master_automation_runner.py:

python
# After donchian_screener.py
if os.path.exists('frontend_data/latest_breakouts.json'):
    from ml_training.deployment.ml_integration import MLBreakoutIntegration
    integration = MLBreakoutIntegration()
    integration.integrate_with_daily_screening('frontend_data/latest_breakouts.json')
🧪 Testing & Validation
Quick Tests
bash
# Test individual components
python ml_training/data_preparation/momentum_labeler.py
python ml_training/data_preparation/feature_builder.py
python ml_training/models/momentum_predictor.py

# Test integration
python ml_training/deployment/ml_integration.py
Validation Pipeline
bash
# Validate data quality
python ml_training/data_preparation/data_cleaner.py

# Run pipeline test
python ml_training/scripts/ml_pipeline_runner.py test
🔧 Troubleshooting
Common Issues
1. No Training Data

bash
# Check if momentum scores exist
python -c "
from ml_training.data_preparation.data_cleaner import PostgreSQLDataCleaner
cleaner = PostgreSQLDataCleaner()
cleaner.check_database_health()
"

# If missing, run:
python ml_training/scripts/ml_pipeline_runner.py momentum 1000
2. Low Model Performance

Increase training data: ml_pipeline_runner.py full
Check feature quality: Review feature importance in model output
Adjust momentum threshold: Modify STRONG_MOMENTUM_THRESHOLD in config
3. Integration Errors

Verify model exists: Check models/production/ directory
Update model path: Use model registry to promote latest model
Debug Mode
bash
# Run with detailed logging
python ml_training/scripts/ml_pipeline_runner.py full --debug

# Check specific symbol
python -c "
from ml_training.models.momentum_predictor import MomentumBreakoutPredictor
predictor = MomentumBreakoutPredictor()
# Load model and test specific symbol
"
📋 Maintenance
Weekly Tasks
Evaluate Predictions: Run performance tracker
Review Model Performance: Check accuracy trends
Update Training Data: Add new breakout outcomes
Monthly Tasks
Retrain Model: With expanded dataset
Feature Analysis: Review feature importance
Parameter Tuning: Optimize model parameters
Model Retraining
bash
# Full retraining with latest data
python ml_training/scripts/ml_pipeline_runner.py full

# Promote best model to production
python -c "
from ml_training.deployment.model_registry import model_registry
# Review models and promote best performer
model_registry.promote_to_production('momentum_predictor_v20250713_1400')
"
🎯 Success Metrics
Model Quality
AUC Score: >0.65 (good), >0.75 (excellent)
Precision: >70% for high-confidence predictions
Accuracy: >60% overall prediction accuracy
Business Impact
Signal Quality: Higher success rate in daily breakouts
Time Efficiency: Focus on top ML-ranked signals
Risk Management: Better risk-adjusted returns
🚀 Advanced Features
Custom Model Training
python
from ml_training.models.momentum_predictor import MomentumBreakoutPredictor

predictor = MomentumBreakoutPredictor()
# Custom training with your parameters
features_df, metadata_df = predictor.load_training_data()
X = predictor.prepare_features(features_df)
y = metadata_df['target_binary']

# Train with custom parameters
predictor.model = xgb.XGBClassifier(
    n_estimators=200,  # More trees
    max_depth=8,       # Deeper trees
    learning_rate=0.05 # Slower learning
)
results = predictor.train_model(X, y)
Feature Importance Analysis
python
# Analyze feature importance
predictor.show_feature_importance(top_n=20)

# Custom feature engineering
from ml_training.data_preparation.feature_builder import SwingTradingFeatureBuilder
builder = SwingTradingFeatureBuilder()
# Add your custom features
Backtesting
python
# Backtest model performance
from ml_training.evaluation.performance_tracker import MLPerformanceTracker
tracker = MLPerformanceTracker()
tracker.evaluate_predictions(days_back=90, evaluation_period=20)
📞 Support
File Locations
Configuration: ml_training/config/ml_config.py
Models: models/production/momentum_predictor_production.joblib
Logs: ml_training/logs/ml_training_*.log
Performance: Database tables ml_predictions, ml_prediction_outcomes
Key Commands
bash
# Check system status
python ml_training/data_preparation/data_cleaner.py

# Full pipeline
python ml_training/scripts/ml_pipeline_runner.py full

# Integration test
python ml_training/deployment/ml_integration.py

# Performance report
python ml_training/evaluation/performance_tracker.py
🎉 Your ML-enhanced trading system is ready for momentum breakout prediction!

The system builds on your existing infrastructure and provides sophisticated momentum predictions to improve your swing trading signals. Start with the quick test, then integrate with your daily workflow for enhanced breakout detection.

