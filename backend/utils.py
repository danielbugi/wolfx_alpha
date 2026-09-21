# backend/utils.py
import os
import glob
import json
import time
from pathlib import Path
from typing import Optional, Dict, Any, List
from datetime import datetime
import re


def find_project_root() -> Path:
    """
    Find the trading system project root by looking for key directories
    Works from any location in the project
    """
    current_path = Path.cwd()

    # Look for characteristic directories that indicate project root
    required_dirs = ['mechanism', 'breakout_results']
    optional_dirs = ['ml_training', 'frontend', 'backend']

    # Check current directory and parent directories
    for path in [current_path] + list(current_path.parents):
        # Check if this looks like project root
        existing_dirs = [d.name for d in path.iterdir() if d.is_dir()]

        # Must have automation AND breakout_results
        if all(req_dir in existing_dirs for req_dir in required_dirs):
            print(f"✅ Found project root at: {path}")
            return path

        # Also check if we're in backend and parent has the required dirs
        if path.name == 'backend' and path.parent.exists():
            parent_dirs = [d.name for d in path.parent.iterdir() if d.is_dir()]
            if all(req_dir in parent_dirs for req_dir in required_dirs):
                print(f"✅ Found project root at: {path.parent}")
                return path.parent

    # Fallback - assume current directory or go up one level
    if current_path.name == 'backend':
        project_root = current_path.parent
        print(f"⚠️ Using fallback project root: {project_root}")
        return project_root
    else:
        print(f"⚠️ Using current directory as project root: {current_path}")
        return current_path


def find_latest_ml_enhanced_file() -> Optional[str]:
    """
    Find the latest ML-enhanced JSON file using smart project detection
    FIXED: Now handles both YYYYMMDD_HHMM and YYYYMMDD_HHMMSS timestamp formats
    """
    try:
        project_root = find_project_root()

        # Try multiple possible locations for the JSON files
        search_patterns = [
            project_root / "breakout_results" / "multi_timeframe_ml_enhanced_*.json",
        ]

        all_files = []
        for pattern in search_patterns:
            files = glob.glob(str(pattern))
            all_files.extend(files)
            if files:
                print(f"📁 Found {len(files)} files matching pattern: {pattern}")

        if not all_files:
            print(f"❌ No ML-enhanced files found. Searched patterns:")
            for pattern in search_patterns:
                print(f"   - {pattern}")
            return None

        # 🔧 FIXED: Enhanced timestamp extraction to handle both formats
        def extract_timestamp(filename):
            """
            Extract timestamp from filename - handles both formats:
            - Old: donchian_breakouts_ml_enhanced_20250713_1934.json (YYYYMMDD_HHMM)
            - New: donchian_breakouts_ml_enhanced_20250729_155521.json (YYYYMMDD_HHMMSS)
            """
            # Try new format first (YYYYMMDD_HHMMSS) - 6 digit time
            match = re.search(r'(\d{8}_\d{6})\.json$', filename)
            if match:
                timestamp_str = match.group(1)
                print(f"📅 Found 6-digit timestamp: {timestamp_str} in {os.path.basename(filename)}")
                return timestamp_str

            # Try old format (YYYYMMDD_HHMM) - 4 digit time
            match = re.search(r'(\d{8}_\d{4})\.json$', filename)
            if match:
                timestamp_str = match.group(1)
                print(f"📅 Found 4-digit timestamp: {timestamp_str} in {os.path.basename(filename)}")
                # Pad with 00 seconds for comparison consistency
                return timestamp_str + "00"

            print(f"⚠️ Could not extract timestamp from: {os.path.basename(filename)}")
            return "00000000_000000"  # fallback for files without timestamp

        # Debug: Show all files and their extracted timestamps
        print(f"🔍 Analyzing {len(all_files)} files for latest timestamp:")
        file_timestamps = []
        for file_path in all_files:
            filename = os.path.basename(file_path)
            timestamp = extract_timestamp(filename)
            file_timestamps.append((file_path, timestamp, filename))
            print(f"   📄 {filename} → timestamp: {timestamp}")

        # Sort by timestamp (string comparison works for this format)
        latest_file = max(all_files, key=extract_timestamp)
        latest_timestamp = extract_timestamp(os.path.basename(latest_file))

        print(f"✅ Latest ML-enhanced file: {os.path.basename(latest_file)}")
        print(f"📅 Latest timestamp: {latest_timestamp}")

        return latest_file

    except Exception as e:
        print(f"❌ Error finding ML-enhanced file: {str(e)}")
        return None


def load_ml_enhanced_data() -> Optional[Dict[str, Any]]:
    """
    Load the latest ML-enhanced data with comprehensive error handling
    """
    try:
        latest_file = find_latest_ml_enhanced_file()
        if not latest_file:
            print("❌ No ML-enhanced file found")
            return None

        print(f"📊 Loading ML data from: {os.path.basename(latest_file)}")

        with open(latest_file, 'r', encoding='utf-8') as f:
            data = json.load(f)

        # Validate the data structure
        if not isinstance(data, dict):
            print(f"❌ Invalid data format: expected dict, got {type(data)}")
            return None

        # Log structure summary
        structure_info = {
            "main_keys": list(data.keys()),
            "total_size": len(str(data)),
        }

        # 🔧 FIXED: Check for correct "signals" structure instead of "breakouts"
        signal_sources = []
        if "signals" in data:
            signals_dict = data["signals"]
            if isinstance(signals_dict, dict):
                structure_info["signals_structure"] = {
                    "bullish_breakouts": len(signals_dict.get("bullish_breakouts", [])),
                    "bearish_breakouts": len(signals_dict.get("bearish_breakouts", [])),
                    "near_bullish": len(signals_dict.get("near_bullish", [])),
                    "near_bearish": len(signals_dict.get("near_bearish", []))
                }
                total_in_signals = sum(structure_info["signals_structure"].values())
                signal_sources.append(f"signals: {total_in_signals}")

        if "ai_insights" in data:
            ai_insights = data["ai_insights"]
            if isinstance(ai_insights, dict):
                structure_info["ai_insights_structure"] = {
                    "top_ai_picks": len(ai_insights.get("top_ai_picks", [])),
                    "high_confidence_signals": len(ai_insights.get("high_confidence_signals", [])),
                }
                signal_sources.append(f"ai_insights: {sum(structure_info['ai_insights_structure'].values())}")

        print(f"📊 ML data loaded successfully:")
        print(f"   📁 File: {os.path.basename(latest_file)}")
        print(f"   🔑 Main keys: {structure_info['main_keys']}")
        print(f"   📊 Signal sources: {', '.join(signal_sources) if signal_sources else 'None detected'}")

        return data

    except FileNotFoundError:
        print(f"❌ ML-enhanced file not found: {latest_file}")
        return None
    except json.JSONDecodeError as e:
        print(f"❌ Invalid JSON in ML-enhanced file: {str(e)}")
        return None
    except Exception as e:
        print(f"❌ Error loading ML-enhanced data: {str(e)}")
        return None


# load_ml_enhanced_data() globs the output directory, reads, and fully
# json.load()s the latest screener file from disk on every call -- fine for
# one call, expensive when alpha.py, strategy.py, stock.py, and main.py's
# dashboard each independently called it uncached on every request. One
# shared, module-level TTL cache means the whole app pays for at most one
# parse per _ML_DATA_CACHE_TTL_SECONDS instead of one per caller per request.
_ML_DATA_CACHE_TTL_SECONDS = 300
_ml_data_cache: Dict[str, Any] = {"data": None, "ts": 0.0}


def load_ml_enhanced_data_cached() -> Optional[Dict[str, Any]]:
    """Cached wrapper around load_ml_enhanced_data() -- use this from request
    handlers instead of calling load_ml_enhanced_data() directly."""
    now = time.time()
    if _ml_data_cache["data"] is not None and (now - _ml_data_cache["ts"]) < _ML_DATA_CACHE_TTL_SECONDS:
        return _ml_data_cache["data"]

    data = load_ml_enhanced_data()
    _ml_data_cache["data"] = data
    _ml_data_cache["ts"] = now
    return data


def load_complete_signals_dataset() -> Optional[List[Dict[str, Any]]]:
    """
    Load all signals from the ML-enhanced data as a flat list
    FIXED: Now extracts from correct "signals" structure
    """
    try:
        ml_data = load_ml_enhanced_data()
        if not ml_data:
            return None

        all_signals = []

        # 🔧 FIXED: Extract from "signals" structure (not "breakouts")
        if "signals" in ml_data:
            signals_dict = ml_data["signals"]
            if isinstance(signals_dict, dict):
                for signal_type in ["bullish_breakouts", "bearish_breakouts", "near_bullish", "near_bearish"]:
                    signals = signals_dict.get(signal_type, [])
                    if isinstance(signals, list):
                        all_signals.extend(signals)
                        print(f"📊 Loaded {len(signals)} {signal_type} signals")

        # Extract from ai_insights (if signals didn't yield enough)
        if len(all_signals) < 500 and "ai_insights" in ml_data:
            ai_insights = ml_data["ai_insights"]
            for source in ["top_ai_picks", "high_confidence_signals"]:
                signals = ai_insights.get(source, [])
                all_signals.extend(signals)

            # Also check trade recommendations
            trade_recs = ai_insights.get("trade_recommendations", {})
            for rec_type, signals in trade_recs.items():
                if isinstance(signals, list):
                    all_signals.extend(signals)

        # Remove duplicates by symbol + type
        seen_signals = set()
        unique_signals = []
        for signal in all_signals:
            if isinstance(signal, dict):
                symbol = signal.get("symbol", "")
                signal_type = signal.get("type", "")
                signal_id = f"{symbol}_{signal_type}"

                if signal_id not in seen_signals:
                    unique_signals.append(signal)
                    seen_signals.add(signal_id)

        print(f"📊 Complete signals dataset: {len(unique_signals)} unique signals")
        return unique_signals

    except Exception as e:
        print(f"❌ Error loading complete signals dataset: {str(e)}")
        return None


def validate_signal_data(signals: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Validate and analyze signal data quality
    """
    if not signals:
        return {"valid": False, "error": "No signals provided"}

    validation_results = {
        "valid": True,
        "total_signals": len(signals),
        "validation_errors": [],
        "type_distribution": {},
        "ml_data_coverage": 0,
        "required_fields_present": 0
    }

    required_fields = ["symbol", "type", "current_price"]
    ml_fields = ["ml_momentum_probability", "ml_confidence"]

    for i, signal in enumerate(signals):
        if not isinstance(signal, dict):
            validation_results["validation_errors"].append(f"Signal {i}: Not a dictionary")
            continue

        # Check required fields
        missing_fields = [field for field in required_fields if field not in signal or signal[field] is None]
        if missing_fields:
            validation_results["validation_errors"].append(
                f"Signal {i} ({signal.get('symbol', 'UNKNOWN')}): Missing {missing_fields}")
        else:
            validation_results["required_fields_present"] += 1

        # Count ML data presence
        if any(field in signal and signal[field] is not None for field in ml_fields):
            validation_results["ml_data_coverage"] += 1

        # Count by type
        signal_type = signal.get("type", "unknown")
        validation_results["type_distribution"][signal_type] = validation_results["type_distribution"].get(signal_type,
                                                                                                           0) + 1

    # Calculate percentages
    total = validation_results["total_signals"]
    if total > 0:
        validation_results["ml_data_coverage_pct"] = (validation_results["ml_data_coverage"] / total) * 100
        validation_results["required_fields_present_pct"] = (validation_results[
                                                                 "required_fields_present"] / total) * 100

    # Determine overall validity
    if len(validation_results["validation_errors"]) > total * 0.1:  # More than 10% errors
        validation_results["valid"] = False
        validation_results["error"] = f"Too many validation errors: {len(validation_results['validation_errors'])}"

    print(f"📊 Signal validation complete:")
    print(f"   ✅ Total signals: {validation_results['total_signals']}")
    print(f"   📊 Type distribution: {validation_results['type_distribution']}")
    print(f"   🤖 ML data coverage: {validation_results['ml_data_coverage_pct']:.1f}%")
    print(f"   ⚠️ Validation errors: {len(validation_results['validation_errors'])}")

    return validation_results


def get_file_info() -> Dict[str, Any]:
    """
    Get comprehensive information about available ML files
    """
    try:
        project_root = find_project_root()

        file_info = {
            "project_root": str(project_root),
            "search_locations": [],
            "files_found": [],
            "latest_file": None,
            "total_files": 0
        }

        # Search in multiple locations
        search_patterns = [
            project_root / "breakout_results" / "donchian_breakouts_ml_enhanced_*.json",
            project_root / "frontend_data" / "donchian_breakouts_ml_enhanced_*.json",
            project_root / "mechanism" / "breakout_results" / "donchian_breakouts_ml_enhanced_*.json"
        ]

        for pattern in search_patterns:
            pattern_str = str(pattern)
            file_info["search_locations"].append(pattern_str)

            files = glob.glob(pattern_str)
            for file_path in files:
                file_stat = os.stat(file_path)
                file_info["files_found"].append({
                    "path": file_path,
                    "name": os.path.basename(file_path),
                    "size": file_stat.st_size,
                    "modified": datetime.fromtimestamp(file_stat.st_mtime).isoformat()
                })

        file_info["total_files"] = len(file_info["files_found"])

        # Find latest file
        latest_file_path = find_latest_ml_enhanced_file()
        if latest_file_path:
            file_info["latest_file"] = {
                "path": latest_file_path,
                "name": os.path.basename(latest_file_path),
                "size": os.path.getsize(latest_file_path)
            }

        return file_info

    except Exception as e:
        return {"error": f"Failed to get file info: {str(e)}"}


# 🔧 ADDED: Test function to verify timestamp extraction
def test_timestamp_extraction():
    """Test the fixed timestamp extraction logic"""
    print("🧪 TESTING TIMESTAMP EXTRACTION")
    print("=" * 50)

    test_files = [
        "donchian_breakouts_ml_enhanced_20250713_1934.json",  # Old format (4-digit)
        "donchian_breakouts_ml_enhanced_20250720_101916.json",  # New format (6-digit)
        "donchian_breakouts_ml_enhanced_20250729_155521.json",  # Newest format (6-digit)
    ]

    def extract_timestamp(filename):
        # Try new format first (YYYYMMDD_HHMMSS) - 6 digit time
        match = re.search(r'(\d{8}_\d{6})\.json$', filename)
        if match:
            return match.group(1)

        # Try old format (YYYYMMDD_HHMM) - 4 digit time
        match = re.search(r'(\d{8}_\d{4})\.json$', filename)
        if match:
            return match.group(1) + "00"  # Pad with 00 seconds

        return "00000000_000000"

    for filename in test_files:
        timestamp = extract_timestamp(filename)
        print(f"📄 {filename}")
        print(f"   → {timestamp}")

    # Show which would be latest
    latest = max(test_files, key=extract_timestamp)
    print(f"\n🏆 Latest file would be: {latest}")


# Export the main functions
__all__ = [
    'find_latest_ml_enhanced_file',
    'load_ml_enhanced_data',
    'load_ml_enhanced_data_cached',
    'load_complete_signals_dataset',
    'validate_signal_data',
    'get_file_info',
    'find_project_root',
    'test_timestamp_extraction'
]

if __name__ == "__main__":
    # Test the timestamp extraction when run directly
    test_timestamp_extraction()