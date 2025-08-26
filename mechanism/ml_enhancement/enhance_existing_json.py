# automation/ml_enhancement/enhance_existing_json.py

"""
Standalone script to enhance your existing donchian screener JSON with ML predictions
Works from any directory and finds models dynamically (no hardcoded timestamps)
"""

import json
import sys
import os
from datetime import datetime
import numpy as np
from pathlib import Path

# Add ML enhancer path
sys.path.append(os.path.dirname(__file__))

try:
    from ml_signal_enhancer import MLMomentumEnhancer

    ML_AVAILABLE = True
except ImportError as e:
    print(f"ERROR: Cannot import ML enhancer: {e}")
    sys.exit(1)


class StandaloneJSONEnhancer:
    """
    Enhance existing donchian screener JSON output with ML predictions
    Works from any directory - finds project root and models dynamically
    """

    def __init__(self):
        print("STANDALONE JSON ML ENHANCER")
        print("=" * 50)
        print("Initializing ML JSON Enhancer...")

        self.ml_enhancer = MLMomentumEnhancer()

        if not self.ml_enhancer.model_loaded:
            print("ERROR: ML model not loaded.")
            print("")
            print("TROUBLESHOOTING:")
            print("1. Make sure you have trained ML models")
            print("2. Check that model files exist (any timestamp):")
            print("   - momentum_predictor_v{timestamp}.joblib")
            print("   - momentum_predictor_v{timestamp}_scaler.joblib")
            print("   - momentum_predictor_v{timestamp}_features.json")
            print("3. If no models found, retrain:")
            print("   python ml_training/scripts/ml_pipeline_runner.py full")
            sys.exit(1)

        print("SUCCESS: ML model loaded and ready")
        print(f"Model version: {self.ml_enhancer.model_version}")
        print(f"Feature count: {len(self.ml_enhancer.feature_names)}")

    def find_project_root(self):
        """Find the project root directory dynamically"""
        current_dir = Path.cwd()

        # The REAL project root must have BOTH automation AND ml_training
        required_dirs = ['mechanism', 'ml_training']
        optional_markers = ['.env', 'breakout_results', 'frontend_data']

        # Start from current directory and go up
        search_dir = current_dir
        for _ in range(5):  # Limit search to 5 levels up
            print(f"Checking directory: {search_dir}")

            # Check if this directory has ALL required directories
            has_required = all((search_dir / req_dir).exists() for req_dir in required_dirs)

            if has_required:
                # Also check for optional markers to confirm
                optional_found = sum(1 for marker in optional_markers if (search_dir / marker).exists())
                print(f"Found project root: {search_dir}")
                print(f"  - mechanism: ✓")
                print(f"  - ml_training: ✓")
                print(f"  - optional markers: {optional_found}")
                return search_dir
            else:
                missing = [req for req in required_dirs if not (search_dir / req).exists()]
                print(f"  Missing required directories: {missing}")

            # Go up one level
            parent = search_dir.parent
            if parent == search_dir:  # Reached filesystem root
                break
            search_dir = parent

        # Fallback to current directory
        print(f"WARNING: Could not find project root with mechanism + ml_training")
        print(f"Using current directory as fallback: {current_dir}")
        return current_dir

    def find_json_files(self):
        """Find JSON breakout files in the project"""
        project_root = self.find_project_root()

        # Possible locations for JSON files
        possible_files = [
            project_root / 'frontend_data' / 'latest_breakouts.json',
            project_root / 'breakout_results' / 'latest_breakouts.json',
            project_root / 'latest_breakouts.json'
        ]

        existing_files = [f for f in possible_files if f.exists()]
        return existing_files

    def load_existing_json(self, json_file_path):
        """Load existing donchian screener JSON output"""
        try:
            json_path = Path(json_file_path)
            if not json_path.exists():
                print(f"ERROR: File not found: {json_file_path}")
                return None

            with open(json_path, 'r') as f:
                data = json.load(f)
            print(f"SUCCESS: Loaded {json_path}")
            return data
        except Exception as e:
            print(f"ERROR: Cannot load {json_file_path}: {e}")
            return None

    def extract_all_signals(self, json_data):
        """Extract all signals from JSON data structure"""
        all_signals = []

        # Extract from signals section
        signals_section = json_data.get('signals', {})
        for signal_type, signals in signals_section.items():
            if isinstance(signals, list):
                for signal in signals:
                    signal['type'] = signal_type  # Ensure type is set
                    all_signals.append(signal)

        # Also check for breakouts section (alternative structure)
        breakouts_section = json_data.get('breakouts', {})
        for breakout_type, breakouts in breakouts_section.items():
            if isinstance(breakouts, list):
                for breakout in breakouts:
                    breakout['type'] = breakout_type
                    all_signals.append(breakout)

        print(f"Extracted {len(all_signals)} signals from JSON")
        return all_signals

    def enhance_json_file(self, input_file, output_file=None):
        """Enhance an existing JSON file with ML predictions"""
        print(f"\nENHANCING JSON FILE: {input_file}")
        print("=" * 60)

        # Load existing JSON
        original_data = self.load_existing_json(input_file)
        if not original_data:
            return False

        # Extract all signals
        all_signals = self.extract_all_signals(original_data)
        if len(all_signals) == 0:
            print("WARNING: No signals found in JSON file")
            print("Make sure the JSON file has a 'signals' section with breakout data")
            return False

        # Enhance signals with ML
        enhanced_signals = self.ml_enhancer.enhance_signals_batch(all_signals)

        # Create enhanced JSON structure
        enhanced_json = self.create_enhanced_json(original_data, enhanced_signals)

        # Determine output filename
        if not output_file:
            input_path = Path(input_file)
            output_file = input_path.parent / f"{input_path.stem}_ml_enhanced.json"

        # Save enhanced JSON
        try:
            output_path = Path(output_file)
            output_path.parent.mkdir(parents=True, exist_ok=True)

            with open(output_path, 'w') as f:
                json.dump(enhanced_json, f, indent=2, default=str)
            print(f"\nSUCCESS: Enhanced JSON saved to {output_path}")

            # Also save timestamped backup
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            project_root = self.find_project_root()
            backup_dir = project_root / 'frontend_data'
            backup_dir.mkdir(exist_ok=True)
            backup_file = backup_dir / f"ml_enhanced_backup_{timestamp}.json"

            with open(backup_file, 'w') as f:
                json.dump(enhanced_json, f, indent=2, default=str)
            print(f"Backup saved to: {backup_file}")

            return True

        except Exception as e:
            print(f"ERROR: Cannot save enhanced JSON: {e}")
            return False

    def create_enhanced_json(self, original_data, enhanced_signals):
        """Create enhanced JSON structure maintaining original format"""

        # Separate enhanced signals by type
        signals_by_type = {}
        for signal in enhanced_signals:
            signal_type = signal.get('type', 'unknown')
            if signal_type not in signals_by_type:
                signals_by_type[signal_type] = []
            signals_by_type[signal_type].append(signal)

        # ML insights
        high_confidence = [s for s in enhanced_signals
                           if s.get('ml_confidence') in ['high', 'very_high']]
        ai_top_picks = sorted(enhanced_signals,
                              key=lambda x: x.get('ml_momentum_probability', 0),
                              reverse=True)[:20]

        # Trade recommendations
        strong_buys = [s for s in enhanced_signals if s.get('ml_trade_recommendation') == 'strong_buy']
        buys = [s for s in enhanced_signals if s.get('ml_trade_recommendation') == 'buy']
        strong_sells = [s for s in enhanced_signals if s.get('ml_trade_recommendation') == 'strong_sell']
        sells = [s for s in enhanced_signals if s.get('ml_trade_recommendation') == 'sell']

        # Risk analysis
        low_risk = [s for s in enhanced_signals if s.get('ml_risk_score', 50) < 30]
        medium_risk = [s for s in enhanced_signals if 30 <= s.get('ml_risk_score', 50) <= 70]
        high_risk = [s for s in enhanced_signals if s.get('ml_risk_score', 50) > 70]

        # Calculate ML statistics
        ml_probabilities = [s.get('ml_momentum_probability', 50) for s in enhanced_signals
                            if s.get('ml_momentum_probability') is not None]
        avg_ml_probability = round(np.mean(ml_probabilities), 1) if ml_probabilities else None

        # Enhanced JSON structure
        enhanced_json = {
            "metadata": {
                **original_data.get('metadata', {}),
                "ml_enhanced": True,
                "ml_enhancement_timestamp": datetime.now().isoformat(),
                "ml_model_version": self.ml_enhancer.model_version,
                "original_signals_count": len(enhanced_signals),
                "version": "3.0_ml_enhanced"
            },
            "summary": {
                **original_data.get('summary', {}),
                # ML-specific summary additions
                "ml_enhancement": {
                    "high_confidence_signals": len(high_confidence),
                    "avg_momentum_probability": avg_ml_probability,
                    "strong_buy_recommendations": len(strong_buys),
                    "strong_sell_recommendations": len(strong_sells),
                    "low_risk_signals": len(low_risk),
                    "high_risk_signals": len(high_risk)
                }
            },
            "signals": signals_by_type,  # Enhanced signals organized by type
            "ai_insights": {
                "top_ai_picks": ai_top_picks,
                "high_confidence_signals": high_confidence,
                "trade_recommendations": {
                    "strong_buy": strong_buys,
                    "buy": buys,
                    "strong_sell": strong_sells,
                    "sell": sells
                },
                "risk_analysis": {
                    "low_risk": low_risk,
                    "medium_risk": medium_risk,
                    "high_risk": high_risk
                }
            },
            "ml_statistics": {
                "probability_distribution": {
                    "very_high": len([s for s in enhanced_signals if s.get('ml_confidence') == 'very_high']),
                    "high": len([s for s in enhanced_signals if s.get('ml_confidence') == 'high']),
                    "medium": len([s for s in enhanced_signals if s.get('ml_confidence') == 'medium']),
                    "low": len([s for s in enhanced_signals if s.get('ml_confidence') == 'low']),
                    "very_low": len([s for s in enhanced_signals if s.get('ml_confidence') == 'very_low'])
                },
                "average_probability": avg_ml_probability,
                "max_probability": max(ml_probabilities) if ml_probabilities else 0,
                "min_probability": min(ml_probabilities) if ml_probabilities else 0
            }
        }

        return enhanced_json

    def print_enhancement_summary(self, enhanced_json):
        """Print summary of ML enhancement results"""
        print("\nML ENHANCEMENT SUMMARY")
        print("=" * 50)

        total_signals = len(self.extract_all_signals(enhanced_json))
        ml_summary = enhanced_json.get('summary', {}).get('ml_enhancement', {})

        print(f"Total signals enhanced: {total_signals}")
        print(f"High confidence signals: {ml_summary.get('high_confidence_signals', 0)}")
        print(f"Average ML probability: {ml_summary.get('avg_momentum_probability', 0)}%")
        print(f"Strong buy recommendations: {ml_summary.get('strong_buy_recommendations', 0)}")
        print(f"Strong sell recommendations: {ml_summary.get('strong_sell_recommendations', 0)}")

        # Top AI picks
        top_picks = enhanced_json.get('ai_insights', {}).get('top_ai_picks', [])
        if len(top_picks) > 0:
            print(f"\nTOP 5 AI MOMENTUM PICKS:")
            for i, signal in enumerate(top_picks[:5], 1):
                symbol = signal.get('symbol', 'Unknown')
                prob = signal.get('ml_momentum_probability', 0)
                confidence = signal.get('ml_confidence', 'unknown')
                recommendation = signal.get('ml_trade_recommendation', 'unknown')
                print(f"   {i}. {symbol}: {prob:.1f}% probability ({confidence}) - {recommendation}")

        print(f"\nML Statistics:")
        ml_stats = enhanced_json.get('ml_statistics', {})
        prob_dist = ml_stats.get('probability_distribution', {})
        print(f"   Very High Confidence: {prob_dist.get('very_high', 0)} signals")
        print(f"   High Confidence: {prob_dist.get('high', 0)} signals")
        print(f"   Medium Confidence: {prob_dist.get('medium', 0)} signals")
        print(f"   Low Confidence: {prob_dist.get('low', 0)} signals")


def main():
    """Main function to enhance JSON files"""
    # Initialize enhancer
    enhancer = StandaloneJSONEnhancer()

    # Check for command line arguments
    if len(sys.argv) > 1:
        input_file = sys.argv[1]
        output_file = sys.argv[2] if len(sys.argv) > 2 else None
    else:
        # Find JSON files automatically
        json_files = enhancer.find_json_files()

        if not json_files:
            print("ERROR: No breakout JSON file found!")
            print("Searched in:")
            project_root = enhancer.find_project_root()
            search_locations = [
                project_root / 'frontend_data' / 'latest_breakouts.json',
                project_root / 'breakout_results' / 'latest_breakouts.json',
                project_root / 'latest_breakouts.json'
            ]
            for location in search_locations:
                print(f"  - {location}")
            print(f"\nUsage:")
            print(f"   python enhance_existing_json.py [input_file] [output_file]")
            print(f"   python enhance_existing_json.py  # Auto-find files")
            print(f"\nFirst run your screener to generate breakout data:")
            print(f"   python mechanism/screeners/donchian_screener.py")
            return

        # Use the first (most recent) JSON file found
        input_file = str(json_files[0])
        output_file = str(Path(input_file).parent / f"{Path(input_file).stem}_ml_enhanced.json")

    # Check if input file exists
    if not Path(input_file).exists():
        print(f"ERROR: Input file not found: {input_file}")
        print(f"\nUsage:")
        print(f"   python enhance_existing_json.py [input_file] [output_file]")
        print(f"   python enhance_existing_json.py  # Auto-find files")
        return

    print(f"Input file: {input_file}")
    print(f"Output file: {output_file}")

    # Enhance the JSON file
    success = enhancer.enhance_json_file(input_file, output_file)

    if success:
        # Load and display summary
        with open(output_file, 'r') as f:
            enhanced_data = json.load(f)
        enhancer.print_enhancement_summary(enhanced_data)

        print(f"\n🎉 SUCCESS! Your breakout signals are now ML-enhanced!")
        print(f"📊 View enhanced results in: {output_file}")
        print(f"\nNext steps:")
        print(f"   1. Review the top AI picks")
        print(f"   2. Focus on high-confidence signals")
        print(f"   3. Integrate with your trading workflow")
    else:
        print(f"❌ Enhancement failed. Check the error messages above.")


if __name__ == "__main__":
    main()