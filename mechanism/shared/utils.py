# automation/shared/utils.py
"""
Shared utilities for the trading system
Includes logging, retry mechanisms, performance monitoring, and helper functions
"""

import logging
import logging.handlers
import time
import functools
import os
import json
import pandas as pd
import numpy as np
from datetime import datetime, timedelta, date
from typing import Any, Callable, Dict, List, Optional, Union
from dataclasses import dataclass, field
import psutil
import threading
from pathlib import Path
import asyncio

from .config import config


# ============================================================================
# LOGGING SETUP
# ============================================================================

def setup_logging(
        name: str = "trading_system",
        log_file: Optional[str] = None,
        level: str = None
) -> logging.Logger:
    """
    Setup consistent logging across all modules

    Args:
        name: Logger name
        log_file: Optional specific log file (defaults to logs/{name}.log)
        level: Log level override

    Returns:
        Configured logger
    """
    logger = logging.getLogger(name)

    # Avoid duplicate handlers
    if logger.handlers:
        return logger

    # Set level
    log_level = getattr(logging, (level or config.log_level).upper(), logging.INFO)
    logger.setLevel(log_level)

    # Create formatters
    detailed_formatter = logging.Formatter(
        '%(asctime)s - %(name)s - %(levelname)s - %(funcName)s:%(lineno)d - %(message)s'
    )
    simple_formatter = logging.Formatter(
        '%(asctime)s - %(levelname)s - %(message)s'
    )

    # Console handler
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(simple_formatter)
    logger.addHandler(console_handler)

    # File handler with rotation
    if log_file is None:
        log_file = os.path.join(config.logs_dir, f"{name}.log")

    # Ensure log directory exists
    os.makedirs(os.path.dirname(log_file), exist_ok=True)

    file_handler = logging.handlers.RotatingFileHandler(
        log_file,
        maxBytes=config.log_file_max_size,
        backupCount=config.log_backup_count
    )
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(detailed_formatter)
    logger.addHandler(file_handler)

    return logger


# ============================================================================
# RETRY MECHANISMS
# ============================================================================

def retry_on_failure(
        max_retries: int = 3,
        delay: float = 1.0,
        backoff_factor: float = 2.0,
        exceptions: tuple = (Exception,)
):
    """
    Decorator for retrying functions on failure with exponential backoff

    Args:
        max_retries: Maximum number of retry attempts
        delay: Initial delay between retries in seconds
        backoff_factor: Factor to multiply delay by after each retry
        exceptions: Tuple of exception types to catch and retry
    """

    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            current_delay = delay
            last_exception = None

            for attempt in range(max_retries + 1):
                try:
                    return func(*args, **kwargs)
                except exceptions as e:
                    last_exception = e

                    if attempt == max_retries:
                        logger = logging.getLogger(func.__module__)
                        logger.error(f"Function {func.__name__} failed after {max_retries} retries: {e}")
                        raise e

                    logger = logging.getLogger(func.__module__)
                    logger.warning(
                        f"Function {func.__name__} attempt {attempt + 1} failed: {e}. Retrying in {current_delay:.1f}s...")

                    time.sleep(current_delay)
                    current_delay *= backoff_factor

            raise last_exception

        return wrapper

    return decorator


def async_retry_on_failure(
        max_retries: int = 3,
        delay: float = 1.0,
        backoff_factor: float = 2.0,
        exceptions: tuple = (Exception,)
):
    """Async version of retry decorator"""

    def decorator(func):
        @functools.wraps(func)
        async def wrapper(*args, **kwargs):
            current_delay = delay
            last_exception = None

            for attempt in range(max_retries + 1):
                try:
                    return await func(*args, **kwargs)
                except exceptions as e:
                    last_exception = e

                    if attempt == max_retries:
                        logger = logging.getLogger(func.__module__)
                        logger.error(f"Async function {func.__name__} failed after {max_retries} retries: {e}")
                        raise e

                    logger = logging.getLogger(func.__module__)
                    logger.warning(
                        f"Async function {func.__name__} attempt {attempt + 1} failed: {e}. Retrying in {current_delay:.1f}s...")

                    await asyncio.sleep(current_delay)
                    current_delay *= backoff_factor

            raise last_exception

        return wrapper

    return decorator


# ============================================================================
# PERFORMANCE MONITORING
# ============================================================================

@dataclass
class PerformanceMetrics:
    """Performance metrics for monitoring system performance"""
    function_name: str
    execution_time: float
    memory_usage_mb: float
    cpu_percent: float
    timestamp: datetime = field(default_factory=datetime.now)
    success: bool = True
    error: Optional[str] = None


class PerformanceMonitor:
    """Performance monitoring and metrics collection"""

    def __init__(self):
        self.metrics: List[PerformanceMetrics] = []
        self.lock = threading.Lock()
        self.enabled = config.performance_monitoring

    def record_metric(self, metric: PerformanceMetrics):
        """Record a performance metric"""
        if not self.enabled:
            return

        with self.lock:
            self.metrics.append(metric)

            # Keep only recent metrics to prevent memory issues
            cutoff_time = datetime.now() - timedelta(hours=24)
            self.metrics = [m for m in self.metrics if m.timestamp > cutoff_time]

    def get_summary(self, hours: int = 1) -> Dict[str, Any]:
        """Get performance summary for the last N hours"""
        if not self.enabled:
            return {"monitoring": "disabled"}

        cutoff_time = datetime.now() - timedelta(hours=hours)
        recent_metrics = [m for m in self.metrics if m.timestamp > cutoff_time]

        if not recent_metrics:
            return {"message": "no_metrics_available"}

        execution_times = [m.execution_time for m in recent_metrics]
        memory_usage = [m.memory_usage_mb for m in recent_metrics]
        cpu_usage = [m.cpu_percent for m in recent_metrics]
        success_rate = sum(1 for m in recent_metrics if m.success) / len(recent_metrics) * 100

        return {
            "period_hours": hours,
            "total_operations": len(recent_metrics),
            "success_rate_percent": round(success_rate, 2),
            "execution_time": {
                "avg_seconds": round(np.mean(execution_times), 3),
                "min_seconds": round(np.min(execution_times), 3),
                "max_seconds": round(np.max(execution_times), 3),
                "p95_seconds": round(np.percentile(execution_times, 95), 3)
            },
            "memory_usage_mb": {
                "avg": round(np.mean(memory_usage), 2),
                "max": round(np.max(memory_usage), 2)
            },
            "cpu_usage_percent": {
                "avg": round(np.mean(cpu_usage), 2),
                "max": round(np.max(cpu_usage), 2)
            },
            "errors": [m.error for m in recent_metrics if not m.success]
        }

    def get_function_stats(self, function_name: str, hours: int = 24) -> Dict[str, Any]:
        """Get stats for a specific function"""
        cutoff_time = datetime.now() - timedelta(hours=hours)
        function_metrics = [
            m for m in self.metrics
            if m.function_name == function_name and m.timestamp > cutoff_time
        ]

        if not function_metrics:
            return {"message": "no_metrics_for_function"}

        execution_times = [m.execution_time for m in function_metrics]
        success_count = sum(1 for m in function_metrics if m.success)

        return {
            "function_name": function_name,
            "call_count": len(function_metrics),
            "success_count": success_count,
            "failure_count": len(function_metrics) - success_count,
            "avg_execution_time": round(np.mean(execution_times), 3),
            "max_execution_time": round(np.max(execution_times), 3)
        }


# Global performance monitor
performance_monitor = PerformanceMonitor()


def timing_decorator(include_system_metrics: bool = True):
    """
    Decorator to time function execution and record performance metrics

    Args:
        include_system_metrics: Whether to include CPU and memory usage
    """

    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            start_time = time.time()
            start_memory = psutil.Process().memory_info().rss / 1024 / 1024 if include_system_metrics else 0
            start_cpu = psutil.cpu_percent() if include_system_metrics else 0

            success = True
            error = None
            result = None

            try:
                result = func(*args, **kwargs)
                return result
            except Exception as e:
                success = False
                error = str(e)
                raise
            finally:
                execution_time = time.time() - start_time
                end_memory = psutil.Process().memory_info().rss / 1024 / 1024 if include_system_metrics else 0
                end_cpu = psutil.cpu_percent() if include_system_metrics else 0

                metric = PerformanceMetrics(
                    function_name=f"{func.__module__}.{func.__name__}",
                    execution_time=execution_time,
                    memory_usage_mb=max(end_memory - start_memory, 0),
                    cpu_percent=max(end_cpu - start_cpu, 0),
                    success=success,
                    error=error
                )

                performance_monitor.record_metric(metric)

                logger = logging.getLogger(func.__module__)
                if success:
                    logger.debug(f"{func.__name__} completed in {execution_time:.3f}s")
                else:
                    logger.error(f"{func.__name__} failed after {execution_time:.3f}s: {error}")

        return wrapper

    return decorator


# ============================================================================
# DATE AND TIME UTILITIES
# ============================================================================

class DateUtils:
    """Date and time utility functions"""

    @staticmethod
    def get_trading_days_ago(days: int) -> date:
        """Get trading day N days ago (excluding weekends)"""
        current = datetime.now().date()
        count = 0
        while count < days:
            current -= timedelta(days=1)
            if current.weekday() < 5:  # Monday = 0, Sunday = 6
                count += 1
        return current

    @staticmethod
    def is_trading_day(check_date: Union[date, datetime]) -> bool:
        """Check if a date is a trading day (weekday)"""
        if isinstance(check_date, datetime):
            check_date = check_date.date()
        return check_date.weekday() < 5

    @staticmethod
    def get_last_trading_day() -> date:
        """Get the last trading day"""
        today = datetime.now().date()
        if today.weekday() < 5:  # If today is a weekday
            return today
        else:
            # Go back to Friday
            days_back = today.weekday() - 4
            return today - timedelta(days=days_back)

    @staticmethod
    def format_date_for_api(date_obj: Union[date, datetime, str]) -> str:
        """Format date for API calls"""
        if isinstance(date_obj, str):
            return date_obj
        elif isinstance(date_obj, datetime):
            return date_obj.strftime('%Y-%m-%d')
        elif isinstance(date_obj, date):
            return date_obj.strftime('%Y-%m-%d')
        else:
            raise ValueError(f"Invalid date type: {type(date_obj)}")

    @staticmethod
    def parse_date(date_str: str) -> date:
        """Parse date string in various formats"""
        formats = ['%Y-%m-%d', '%m/%d/%Y', '%d/%m/%Y', '%Y%m%d']

        for fmt in formats:
            try:
                return datetime.strptime(date_str, fmt).date()
            except ValueError:
                continue

        raise ValueError(f"Unable to parse date: {date_str}")


date_utils = DateUtils()


# ============================================================================
# DATA VALIDATION
# ============================================================================

class DataValidation:
    """Data validation utilities"""

    @staticmethod
    def validate_symbol(symbol: str) -> bool:
        """Validate stock symbol format"""
        if not symbol or not isinstance(symbol, str):
            return False

        symbol = symbol.strip().upper()

        # Basic symbol validation (1-5 letters, optional dot)
        if len(symbol) < 1 or len(symbol) > 6:
            return False

        # Check for valid characters
        valid_chars = set('ABCDEFGHIJKLMNOPQRSTUVWXYZ.')
        return all(c in valid_chars for c in symbol)

    @staticmethod
    def validate_price_data(df: pd.DataFrame) -> Dict[str, Any]:
        """Validate price data DataFrame"""
        required_columns = ['open', 'high', 'low', 'close', 'volume']
        missing_columns = [col for col in required_columns if col not in df.columns]

        if missing_columns:
            return {
                'valid': False,
                'error': f"Missing columns: {missing_columns}"
            }

        # Check for negative prices
        price_columns = ['open', 'high', 'low', 'close']
        negative_prices = df[price_columns].lt(0).any().any()

        # Check for missing data
        missing_data = df[required_columns].isnull().sum()

        # Check for logical inconsistencies
        logical_errors = (
                (df['high'] < df['low']).sum() +
                (df['high'] < df['open']).sum() +
                (df['high'] < df['close']).sum() +
                (df['low'] > df['open']).sum() +
                (df['low'] > df['close']).sum()
        )

        return {
            'valid': not negative_prices and logical_errors == 0,
            'record_count': len(df),
            'negative_prices': negative_prices,
            'missing_data': missing_data.to_dict(),
            'logical_errors': logical_errors,
            'date_range': {
                'start': df.index.min() if not df.empty else None,
                'end': df.index.max() if not df.empty else None
            }
        }

    @staticmethod
    def clean_numeric_data(value: Any, default: float = 0.0) -> float:
        """Clean and convert numeric data"""
        if pd.isna(value) or value is None:
            return default

        try:
            return float(value)
        except (ValueError, TypeError):
            return default

    @staticmethod
    def validate_fundamentals(data: Dict[str, Any]) -> Dict[str, Any]:
        """Validate fundamental data"""
        numeric_fields = [
            'market_cap', 'pe_ratio', 'pb_ratio', 'dividend_yield',
            'beta', 'shares_outstanding'
        ]

        validation = {
            'valid': True,
            'errors': [],
            'warnings': []
        }

        for field in numeric_fields:
            if field in data and data[field] is not None:
                try:
                    value = float(data[field])
                    if value < 0 and field in ['market_cap', 'shares_outstanding']:
                        validation['errors'].append(f"{field} cannot be negative")
                        validation['valid'] = False
                except (ValueError, TypeError):
                    validation['warnings'].append(f"{field} is not numeric")

        return validation


data_validation = DataValidation()


# ============================================================================
# FILE UTILITIES
# ============================================================================

class FileUtils:
    """File and directory utilities"""

    @staticmethod
    def ensure_directory(path: str) -> bool:
        """Ensure directory exists, create if necessary"""
        try:
            os.makedirs(path, exist_ok=True)
            return True
        except Exception as e:
            logger = logging.getLogger(__name__)
            logger.error(f"Failed to create directory {path}: {e}")
            return False

    @staticmethod
    def save_json(data: Any, filepath: str, indent: int = 2) -> bool:
        """Save data to JSON file with error handling"""
        try:
            FileUtils.ensure_directory(os.path.dirname(filepath))

            with open(filepath, 'w') as f:
                json.dump(data, f, indent=indent, default=str)
            return True

        except Exception as e:
            logger = logging.getLogger(__name__)
            logger.error(f"Failed to save JSON to {filepath}: {e}")
            return False

    @staticmethod
    def load_json(filepath: str) -> Optional[Any]:
        """Load data from JSON file with error handling"""
        try:
            with open(filepath, 'r') as f:
                return json.load(f)
        except Exception as e:
            logger = logging.getLogger(__name__)
            logger.error(f"Failed to load JSON from {filepath}: {e}")
            return None

    @staticmethod
    def get_latest_file(directory: str, pattern: str = "*") -> Optional[str]:
        """Get the most recently modified file matching pattern"""
        try:
            path = Path(directory)
            if not path.exists():
                return None

            files = list(path.glob(pattern))
            if not files:
                return None

            latest_file = max(files, key=lambda x: x.stat().st_mtime)
            return str(latest_file)

        except Exception as e:
            logger = logging.getLogger(__name__)
            logger.error(f"Failed to get latest file from {directory}: {e}")
            return None

    @staticmethod
    def cleanup_old_files(directory: str, days_old: int = 30, pattern: str = "*") -> int:
        """Clean up files older than specified days"""
        try:
            path = Path(directory)
            if not path.exists():
                return 0

            cutoff_time = time.time() - (days_old * 24 * 60 * 60)
            files_removed = 0

            for file_path in path.glob(pattern):
                if file_path.is_file() and file_path.stat().st_mtime < cutoff_time:
                    file_path.unlink()
                    files_removed += 1

            return files_removed

        except Exception as e:
            logger = logging.getLogger(__name__)
            logger.error(f"Failed to cleanup files in {directory}: {e}")
            return 0

    @staticmethod
    def find_project_root() -> Path:
        """
        Find the donchian_screener project root directory
        Works regardless of execution location by looking for project markers

        Returns:
            Path to project root directory
        """
        from pathlib import Path

        # Start from this file's location (automation/shared/utils.py)
        current_path = Path(__file__).resolve().parent

        # Project markers that should exist in root directory
        required_markers = ['mechanism', 'frontend_data']
        optional_markers = ['ml_training', 'breakout_results']

        # Walk up directory tree looking for project root
        for parent in [current_path] + list(current_path.parents):
            # Check if this directory contains required project markers
            has_required = all((parent / marker).exists() for marker in required_markers)

            if has_required:
                logger = logging.getLogger(__name__)
                logger.debug(f"Found project root: {parent}")
                return parent

        # Fallback: assume we're in automation/shared and go up 2 levels
        fallback_root = current_path.parent.parent
        logger = logging.getLogger(__name__)
        logger.warning(f"Could not find project root by markers, using fallback: {fallback_root}")
        return fallback_root

    @staticmethod
    def get_project_path(*path_parts) -> Path:
        """
        Get path relative to project root

        Args:
            *path_parts: Path components to join

        Returns:
            Path object relative to project root

        Example:
            get_project_path("frontend_data", "latest_breakouts.json")
            get_project_path("mechanism", "screeners", "donchian_screener.py")
        """
        root = FileUtils.find_project_root()
        return root.joinpath(*path_parts)

    @staticmethod
    def ensure_project_directory(*path_parts) -> Path:
        """
        Ensure directory exists relative to project root and return path

        Args:
            *path_parts: Path components to join

        Returns:
            Path object to the directory (created if necessary)
        """
        dir_path = FileUtils.get_project_path(*path_parts)
        dir_path.mkdir(parents=True, exist_ok=True)
        return dir_path

    @staticmethod
    def get_frontend_data_path() -> Path:
        """
        Get the frontend_data directory path with automatic creation

        Returns:
            Path to frontend_data directory
        """
        return FileUtils.ensure_project_directory("frontend_data")

    @staticmethod
    def save_to_frontend_data(filename: str, data: Any, indent: int = 2) -> bool:
        """
        Save data to JSON file in frontend_data directory

        Args:
            filename: Name of the file (e.g., "latest_breakouts.json")
            data: Data to save
            indent: JSON indentation

        Returns:
            True if successful, False otherwise
        """
        try:
            frontend_path = FileUtils.get_frontend_data_path()
            filepath = frontend_path / filename

            with open(filepath, 'w') as f:
                json.dump(data, f, indent=indent, default=str)

            logger = logging.getLogger(__name__)
            logger.debug(f"Saved data to: {filepath}")
            return True

        except Exception as e:
            logger = logging.getLogger(__name__)
            logger.error(f"Failed to save to frontend_data/{filename}: {e}")
            return False


file_utils = FileUtils()


# ============================================================================
# GENERAL UTILITIES
# ============================================================================

def format_number(value: Union[int, float], precision: int = 2) -> str:
    """Format number with appropriate suffixes (K, M, B)"""
    if value is None:
        return "N/A"

    try:
        value = float(value)
    except (ValueError, TypeError):
        return "N/A"

    if abs(value) >= 1e9:
        return f"{value / 1e9:.{precision}f}B"
    elif abs(value) >= 1e6:
        return f"{value / 1e6:.{precision}f}M"
    elif abs(value) >= 1e3:
        return f"{value / 1e3:.{precision}f}K"
    else:
        return f"{value:.{precision}f}"


def calculate_percentage_change(old_value: float, new_value: float) -> float:
    """Calculate percentage change between two values"""
    if old_value == 0:
        return 100.0 if new_value > 0 else 0.0

    return ((new_value - old_value) / old_value) * 100


def safe_divide(numerator: float, denominator: float, default: float = 0.0) -> float:
    """Safely divide two numbers, returning default if denominator is zero"""
    if denominator == 0:
        return default
    return numerator / denominator


def chunk_list(lst: List[Any], chunk_size: int) -> List[List[Any]]:
    """Split list into chunks of specified size"""
    return [lst[i:i + chunk_size] for i in range(0, len(lst), chunk_size)]


def flatten_dict(d: Dict[str, Any], parent_key: str = '', sep: str = '_') -> Dict[str, Any]:
    """Flatten nested dictionary"""
    items = []
    for k, v in d.items():
        new_key = f"{parent_key}{sep}{k}" if parent_key else k
        if isinstance(v, dict):
            items.extend(flatten_dict(v, new_key, sep=sep).items())
        else:
            items.append((new_key, v))
    return dict(items)


# ============================================================================
# SYSTEM UTILITIES
# ============================================================================

def get_system_stats() -> Dict[str, Any]:
    """Get current system performance statistics"""
    try:
        cpu_percent = psutil.cpu_percent(interval=1)
        memory = psutil.virtual_memory()
        disk = psutil.disk_usage('/')

        return {
            'cpu_percent': cpu_percent,
            'memory': {
                'total_gb': round(memory.total / 1024 ** 3, 2),
                'available_gb': round(memory.available / 1024 ** 3, 2),
                'used_percent': memory.percent
            },
            'disk': {
                'total_gb': round(disk.total / 1024 ** 3, 2),
                'free_gb': round(disk.free / 1024 ** 3, 2),
                'used_percent': round(disk.used / disk.total * 100, 2)
            },
            'timestamp': datetime.now().isoformat()
        }
    except Exception as e:
        return {'error': str(e), 'timestamp': datetime.now().isoformat()}


# Initialize logging for this module
logger = setup_logging(__name__)

if __name__ == "__main__":
    print("🛠️ Trading System Utilities Test")
    print("=" * 50)

    # Test logging
    test_logger = setup_logging("test_module")
    test_logger.info("Test log message")
    print("✅ Logging setup complete")


    # Test performance monitoring
    @timing_decorator()
    def test_function():
        time.sleep(0.1)
        return "test result"


    result = test_function()
    summary = performance_monitor.get_summary(hours=1)
    print(f"✅ Performance monitoring: {summary.get('total_operations', 0)} operations recorded")

    # Test utilities
    formatted = format_number(1500000)
    print(f"✅ Number formatting: {formatted}")

    change = calculate_percentage_change(100, 110)
    print(f"✅ Percentage change: {change:.1f}%")

    # Test system stats
    stats = get_system_stats()
    if 'error' not in stats:
        print(f"✅ System stats: CPU {stats['cpu_percent']:.1f}%, Memory {stats['memory']['used_percent']:.1f}%")
    else:
        print(f"⚠️ System stats error: {stats['error']}")

    print("\n🎉 All utilities tests completed!")