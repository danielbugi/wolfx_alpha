# automation/shared/config.py
"""
Centralized configuration management for the trading system
Handles environment variables, validation, and default values
"""

import os
from typing import Dict, Any, Optional
from dataclasses import dataclass, field
from dotenv import load_dotenv
import logging


# Load environment variables from multiple possible locations
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


# Load environment variables
env_file_path = load_env_file()


@dataclass
class TradingSystemConfig:
    """Centralized configuration for the trading system"""

    # Database Configuration
    db_host: str = field(default_factory=lambda: os.getenv('DB_HOST', 'localhost'))
    db_port: int = field(default_factory=lambda: int(os.getenv('DB_PORT', '5432')))
    db_name: str = field(default_factory=lambda: os.getenv('DB_NAME', 'trading_production'))
    db_user: str = field(default_factory=lambda: os.getenv('DB_USER', 'trading_user'))
    db_password: str = field(default_factory=lambda: os.getenv('DB_PASSWORD', ''))
    db_pool_min_size: int = field(default_factory=lambda: int(os.getenv('DB_POOL_MIN_SIZE', '2')))
    db_pool_max_size: int = field(default_factory=lambda: int(os.getenv('DB_POOL_MAX_SIZE', '10')))
    db_timeout: int = field(default_factory=lambda: int(os.getenv('DB_TIMEOUT', '60')))

    # API Configuration
    api_host: str = field(default_factory=lambda: os.getenv('API_HOST', 'localhost'))
    api_port: int = field(default_factory=lambda: int(os.getenv('API_PORT', '8000')))
    api_timeout: int = field(default_factory=lambda: int(os.getenv('API_TIMEOUT', '30')))

    # Data Update Configuration
    data_batch_size: int = field(default_factory=lambda: int(os.getenv('DATA_BATCH_SIZE', '1000')))
    max_concurrent_updates: int = field(default_factory=lambda: int(os.getenv('MAX_CONCURRENT_UPDATES', '5')))
    rate_limit_delay: float = field(default_factory=lambda: float(os.getenv('RATE_LIMIT_DELAY', '1.0')))
    api_rate_limit: int = field(default_factory=lambda: int(os.getenv('API_RATE_LIMIT', '100')))

    # Screening Configuration
    min_volume: int = field(default_factory=lambda: int(os.getenv('MIN_VOLUME', '100000')))
    min_price: float = field(default_factory=lambda: float(os.getenv('MIN_PRICE', '5.0')))
    max_price: float = field(default_factory=lambda: float(os.getenv('MAX_PRICE', '500.0')))
    min_market_cap: int = field(default_factory=lambda: int(os.getenv('MIN_MARKET_CAP', '100000000')))
    min_quality_score: float = field(default_factory=lambda: float(os.getenv('MIN_QUALITY_SCORE', '60.0')))
    volume_spike_threshold: float = field(default_factory=lambda: float(os.getenv('VOLUME_SPIKE_THRESHOLD', '1.5')))

    # ML Configuration
    ml_lookforward_days: int = field(default_factory=lambda: int(os.getenv('ML_LOOKFORWARD_DAYS', '10')))
    ml_min_data_points: int = field(default_factory=lambda: int(os.getenv('ML_MIN_DATA_POINTS', '50')))

    # Logging Configuration
    log_level: str = field(default_factory=lambda: os.getenv('LOG_LEVEL', 'INFO'))
    log_file_max_size: int = field(default_factory=lambda: int(os.getenv('LOG_FILE_MAX_SIZE', '10485760')))  # 10MB
    log_backup_count: int = field(default_factory=lambda: int(os.getenv('LOG_BACKUP_COUNT', '5')))

    # File Paths
    data_dir: str = field(default_factory=lambda: os.getenv('DATA_DIR', 'data'))
    logs_dir: str = field(default_factory=lambda: os.getenv('LOGS_DIR', 'logs'))
    reports_dir: str = field(default_factory=lambda: os.getenv('REPORTS_DIR', 'reports'))
    frontend_data_dir: str = field(default_factory=lambda: os.getenv('FRONTEND_DATA_DIR', 'frontend_data'))

    # Performance Configuration
    performance_monitoring: bool = field(
        default_factory=lambda: os.getenv('PERFORMANCE_MONITORING', 'true').lower() == 'true')
    metric_collection_interval: int = field(default_factory=lambda: int(os.getenv('METRIC_COLLECTION_INTERVAL', '60')))

    def __post_init__(self):
        """Post-initialization validation and setup"""
        # Create necessary directories
        for directory in [self.data_dir, self.logs_dir, self.reports_dir, self.frontend_data_dir]:
            os.makedirs(directory, exist_ok=True)

    @property
    def db_url(self) -> str:
        """Get database URL for connection"""
        return f"postgresql://{self.db_user}:{self.db_password}@{self.db_host}:{self.db_port}/{self.db_name}"

    @property
    def db_config(self) -> Dict[str, Any]:
        """Get database configuration as dictionary"""
        return {
            'host': self.db_host,
            'port': self.db_port,
            'database': self.db_name,
            'user': self.db_user,
            'password': self.db_password
        }

    def validate_config(self) -> Dict[str, bool]:
        """Validate configuration and return status"""
        validation = {
            'database_config': bool(self.db_password),  # Must have password
            'directories_exist': all(os.path.exists(d) for d in [self.data_dir, self.logs_dir]),
            'numeric_values': all([
                self.db_port > 0,
                self.api_port > 0,
                self.data_batch_size > 0,
                self.max_concurrent_updates > 0,
                self.min_volume > 0,
                self.min_price > 0
            ]),
            'environment_file': env_file_path is not None
        }

        return validation

    def get_screening_criteria(self) -> Dict[str, Any]:
        """Get screening criteria as dictionary"""
        return {
            'min_volume': self.min_volume,
            'min_price': self.min_price,
            'max_price': self.max_price,
            'min_market_cap': self.min_market_cap,
            'min_quality_score': self.min_quality_score,
            'volume_spike_threshold': self.volume_spike_threshold
        }

    def get_ml_config(self) -> Dict[str, Any]:
        """Get ML configuration as dictionary"""
        return {
            'lookforward_days': self.ml_lookforward_days,
            'min_data_points': self.ml_min_data_points,
            'batch_size': self.data_batch_size
        }

    def update_from_dict(self, config_dict: Dict[str, Any]):
        """Update configuration from dictionary"""
        for key, value in config_dict.items():
            if hasattr(self, key):
                setattr(self, key, value)

    def to_dict(self) -> Dict[str, Any]:
        """Convert configuration to dictionary"""
        return {
            field.name: getattr(self, field.name)
            for field in self.__dataclass_fields__.values()
        }

    def __str__(self) -> str:
        """String representation hiding sensitive data"""
        config_str = "TradingSystemConfig:\n"
        for field_name, field_obj in self.__dataclass_fields__.items():
            value = getattr(self, field_name)
            if 'password' in field_name.lower():
                value = '*' * len(str(value)) if value else '(empty)'
            config_str += f"  {field_name}: {value}\n"
        return config_str


# Global configuration instance
config = TradingSystemConfig()

# Configuration validation on import
if __name__ == "__main__":
    print("🔧 Trading System Configuration")
    print("=" * 50)

    if env_file_path:
        print(f"✅ Environment file: {env_file_path}")
    else:
        print("⚠️ No .env file found - using defaults/environment variables")

    validation = config.validate_config()
    print(f"\n📋 Configuration validation:")
    for check, status in validation.items():
        status_icon = "✅" if status else "❌"
        print(f"  {status_icon} {check.replace('_', ' ').title()}")

    if not all(validation.values()):
        print(f"\n⚠️ Configuration issues detected!")
        if not validation['database_config']:
            print("  - Database password is missing")
        if not validation['environment_file']:
            print("  - No .env file found")
    else:
        print(f"\n🎉 Configuration is valid!")

    print(f"\n📊 Current configuration:")
    print(f"  Database: {config.db_host}:{config.db_port}/{config.db_name}")
    print(f"  API: {config.api_host}:{config.api_port}")
    print(f"  Data batch size: {config.data_batch_size}")
    print(f"  Max concurrent updates: {config.max_concurrent_updates}")
    print(f"  Log level: {config.log_level}")