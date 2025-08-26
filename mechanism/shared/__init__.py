# automation/shared/__init__.py
"""
Shared infrastructure for the trading system mechanism
Provides centralized configuration, database management, and utilities
"""

from .config import config, TradingSystemConfig
from .database import db, DatabaseManager
from .utils import (
    setup_logging,
    retry_on_failure,
    timing_decorator,
    date_utils,
    data_validation,
    performance_monitor,
    file_utils,
    format_number,
    calculate_percentage_change,
    safe_divide
)

__version__ = "1.0.0"
__author__ = "Trading System Team"

# Export main components for easy importing
__all__ = [
    # Configuration
    'config',
    'TradingSystemConfig',

    # Database
    'db',
    'DatabaseManager',

    # Utilities
    'setup_logging',
    'retry_on_failure',
    'timing_decorator',
    'date_utils',
    'data_validation',
    'performance_utils',
    'file_utils',
    'format_number',
    'calculate_percentage_change',
    'safe_divide'
]


def initialize_system():
    """Initialize the trading system components"""
    print("🚀 Initializing Trading System Shared Infrastructure...")

    # Test configuration
    validation = config.validate_config()
    if all(validation.values()):
        print("✅ Configuration: Valid")
    else:
        print(f"⚠️ Configuration issues: {validation}")

    # Test database connection
    db_test = db.test_connection()
    if db_test.get('connected'):
        print(f"✅ Database: Connected to {db_test.get('database')}")
    else:
        print(f"❌ Database: Connection failed - {db_test.get('error')}")

    print("🎯 Shared infrastructure ready!")
    return validation, db_test


if __name__ == "__main__":
    initialize_system()