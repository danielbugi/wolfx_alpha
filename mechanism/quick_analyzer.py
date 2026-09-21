# Quick script to analyze your results and adjust thresholds
# File: quick_analyze_results.py

import json
import numpy as np

def analyze_results():
    try:
        # Load your results
        with open('frontend_data/latest_multi_timeframe_ml_enhanced.json', 'r') as f:
            data = json.load(f)
        
        ml_enhanced = data.get('ai_insights', {}).get('ml_enhanced_signals', [])
        
        if not ml_enhanced:
            print("No ML enhanced signals found")
            return
        
        # Extract scores
        combined_scores = [s.get('combined_score', 0) for s in ml_enhanced]
        alignment_scores = [s.get('alignment_score', 0) for s in ml_enhanced]
        ml_probabilities = [s.get('ml_momentum_probability', 0) for s in ml_enhanced]
        
        # Statistics
        print("í³Š SCORE ANALYSIS")
        print("="*50)
        print(f"Total ML Enhanced Signals: {len(ml_enhanced)}")
        print()
        print("Combined Scores (60% alignment + 40% ML):")
        print(f"  Max: {max(combined_scores):.1f}%")
        print(f"  Average: {np.mean(combined_scores):.1f}%")
        print(f"  75th percentile: {np.percentile(combined_scores, 75):.1f}%")
        print(f"  90th percentile: {np.percentile(combined_scores, 90):.1f}%")
        print()
        print("Alignment Scores:")
        print(f"  Max: {max(alignment_scores):.1f}%")
        print(f"  Average: {np.mean(alignment_scores):.1f}%")
        print()
        print("ML Probabilities:")
        print(f"  Max: {max(ml_probabilities):.1f}%")
        print(f"  Average: {np.mean(ml_probabilities):.1f}%")
        print()
        
        # Show top 10 signals
        sorted_signals = sorted(ml_enhanced, key=lambda x: x.get('combined_score', 0), reverse=True)
        print("í¿† TOP 10 SIGNALS:")
        print("="*50)
        for i, signal in enumerate(sorted_signals[:10], 1):
            symbol = signal.get('symbol', 'UNKNOWN')
            combined = signal.get('combined_score', 0)
            alignment = signal.get('alignment_score', 0)
            ml_prob = signal.get('ml_momentum_probability', 0)
            grade = signal.get('alignment_grade', 'F')
            signal_type = signal.get('signal_type', 'unknown')
            
            print(f"{i:2d}. {symbol:>6s} | {signal_type:>15s} | "
                  f"Combined: {combined:>5.1f}% | Align: {alignment:>5.1f}% ({grade}) | ML: {ml_prob:>5.1f}%")
        
        # Suggest better thresholds
        print()
        print("í²¡ SUGGESTED THRESHOLDS:")
        print("="*50)
        
        top_90th = np.percentile(combined_scores, 90)
        top_75th = np.percentile(combined_scores, 75)
        
        print(f"Top AI Picks (top 10%): Combined score â‰¥ {top_90th:.1f}%")
        print(f"High Confidence (top 25%): Combined score â‰¥ {top_75th:.1f}%")
        
        # Count signals at suggested thresholds
        top_picks = [s for s in ml_enhanced if s.get('combined_score', 0) >= top_90th]
        high_conf = [s for s in ml_enhanced if s.get('combined_score', 0) >= top_75th]
        
        print(f"This would give you: {len(top_picks)} Top AI Picks, {len(high_conf)} High Confidence")
        
    except FileNotFoundError:
        print("Results file not found. Run the screener first.")
    except Exception as e:
        print(f"Error analyzing results: {e}")

if __name__ == "__main__":
    analyze_results()
