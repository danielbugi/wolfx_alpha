# ml_diagnostic.py - Debug ML prediction issues
# --* coding: utf-8 *--
"""
Diagnostic tool to debug ML prediction problems in the multi-timeframe screener
"""

import sys
import os
sys.path.append(os.path.join(os.path.dirname(__file__), '..', '..'))

from mechanism.screeners.multi_timeframe_screener import MultiTimeframeMLScreener
import numpy as np
import json

def debug_ml_predictions():
    """Debug ML prediction issues step by step"""
    print("� DEBUGGING ML PREDICTIONS")
    print("=" * 50)
    
    # Initialize screener
    screener = MultiTimeframeMLScreener()
    
    if not screener.ml_enhancer:
        print("❌ ML enhancer not available")
        return
    
    print(f"✅ ML enhancer loaded: {type(screener.ml_enhancer).__name__}")
    
    # Check if ml_enhancer has the expected model
    if hasattr(screener.ml_enhancer, 'ml_model_loaded'):
        print(f"   Model loaded: {screener.ml_enhancer.ml_model_loaded}")
        if hasattr(screener.ml_enhancer, 'ml_feature_names'):
            print(f"   Feature count: {len(screener.ml_enhancer.ml_feature_names)}")
            print(f"   Features: {screener.ml_enhancer.ml_feature_names[:5]}...")
    
    # Get a few sample signals to test
    print("\n� GETTING SAMPLE SIGNALS")
    print("-" * 30)
    
    daily_signals = screener.get_daily_breakout_signals()
    if not daily_signals:
        print("❌ No signals found to test")
        return
    
    # Test with first bullish breakout signal
    test_signal = None
    for signal in daily_signals:
        if signal['signal_type'] == 'bullish_breakout':
            test_signal = signal
            break
    
    if not test_signal:
        test_signal = daily_signals[0]  # Fallback to any signal
    
    print(f"✅ Testing with signal: {test_signal['symbol']} ({test_signal['signal_type']})")
    
    # Debug step 1: Check if fundamental data exists
    print(f"\n� CHECKING FUNDAMENTAL DATA FOR {test_signal['symbol']}")
    print("-" * 40)
    
    fundamental_fields = ['market_cap', 'sector', 'pe_ratio', 'growth_score', 'quality_grade']
    for field in fundamental_fields:
        value = test_signal.get(field)
        print(f"   {field}: {value} ({type(value).__name__})")
    
    # Debug step 2: Test ML feature extraction
    print(f"\n� TESTING ML FEATURE EXTRACTION")
    print("-" * 40)
    
    try:
        # Simulate what happens in the ML enhancement process
        if hasattr(screener.ml_enhancer, 'extract_ml_features_from_data'):
            # Get stock data for this symbol
            stock_data = screener.ml_enhancer.get_stock_data_with_indicators(test_signal['symbol'])
            fundamentals = screener.ml_enhancer.get_fundamentals(test_signal['symbol'])
            
            print(f"   Stock data records: {len(stock_data)}")
            print(f"   Fundamentals keys: {list(fundamentals.keys()) if fundamentals else 'None'}")
            
            if stock_data:
                print(f"   Latest stock data sample: {dict(list(stock_data[0].items())[:5])}")
            if fundamentals:
                print(f"   Fundamentals sample: {dict(list(fundamentals.items())[:5])}")
            
            # Try feature extraction
            features = screener.ml_enhancer.extract_ml_features_from_data(
                test_signal['symbol'], stock_data, fundamentals
            )
            
            print(f"   Extracted features count: {len(features)}")
            print(f"   Feature sample: {dict(list(features.items())[:5])}")
            
            # Check for missing/invalid features
            invalid_features = []
            for key, value in features.items():
                if value is None or (isinstance(value, float) and (np.isnan(value) or np.isinf(value))):
                    invalid_features.append(key)
            
            if invalid_features:
                print(f"   ⚠️  Invalid features: {invalid_features[:10]}...")
            else:
                print(f"   ✅ All features appear valid")
                
        else:
            print("   ❌ ML enhancer doesn't have extract_ml_features_from_data method")
    
    except Exception as e:
        print(f"   ❌ Feature extraction failed: {e}")
        import traceback
        traceback.print_exc()
    
    # Debug step 3: Test direct ML prediction
    print(f"\n� TESTING DIRECT ML PREDICTION")
    print("-" * 40)
    
    try:
        # Create a minimal signal for ML testing
        ml_test_signal = {
            'symbol': test_signal['symbol'],
            'type': test_signal['signal_type'],
            'urgency': test_signal['urgency'],
            'current_price': test_signal['current_price'],
            'volume_ratio': test_signal.get('volume_ratio', 1.0),
            'rsi_14': test_signal.get('rsi_14', 50.0),
            'distance_to_breakout': test_signal.get('distance_to_breakout', 0.0),
            'screening_date': test_signal.get('screening_date')
        }
        
        print(f"   Test signal: {ml_test_signal}")
        
        # Test ML prediction directly
        if hasattr(screener.ml_enhancer, 'predict_ml_momentum'):
            stock_data = screener.ml_enhancer.get_stock_data_with_indicators(test_signal['symbol'])
            fundamentals = screener.ml_enhancer.get_fundamentals(test_signal['symbol'])
            
            ml_result = screener.ml_enhancer.predict_ml_momentum(
                test_signal['symbol'], ml_test_signal, stock_data, fundamentals
            )
            
            print(f"   ML Result: {ml_result}")
            
            # Check if result looks suspicious
            if ml_result.get('ml_momentum_probability') == 10.1:
                print("   � PROBLEM DETECTED: Getting the suspicious 10.1% value!")
            
        else:
            print("   ❌ ML enhancer doesn't have predict_ml_momentum method")
    
    except Exception as e:
        print(f"   ❌ ML prediction test failed: {e}")
        import traceback
        traceback.print_exc()
    
    # Debug step 4: Test with multiple signals
    print(f"\n� TESTING WITH MULTIPLE SIGNALS")  
    print("-" * 40)
    
    test_count = min(5, len(daily_signals))
    results = []
    
    for i, signal in enumerate(daily_signals[:test_count]):
        try:
            ml_signal = {
                'symbol': signal['symbol'],
                'type': signal['signal_type'],
                'urgency': signal['urgency'],
                'current_price': signal['current_price'],
                'volume_ratio': signal.get('volume_ratio', 1.0),
                'rsi_14': signal.get('rsi_14', 50.0),
                'distance_to_breakout': signal.get('distance_to_breakout', 0.0),
                'screening_date': signal.get('screening_date')
            }
            
            if hasattr(screener.ml_enhancer, 'predict_ml_momentum'):
                stock_data = screener.ml_enhancer.get_stock_data_with_indicators(signal['symbol'])
                fundamentals = screener.ml_enhancer.get_fundamentals(signal['symbol'])
                
                ml_result = screener.ml_enhancer.predict_ml_momentum(
                    signal['symbol'], ml_signal, stock_data, fundamentals
                )
                
                prob = ml_result.get('ml_momentum_probability', 0)
                conf = ml_result.get('ml_confidence', 'unknown')
                results.append((signal['symbol'], prob, conf))
                
                print(f"   {signal['symbol']}: {prob}% ({conf})")
        
        except Exception as e:
            print(f"   {signal['symbol']}: ERROR - {e}")
    
    # Analyze results
    probabilities = [r[1] for r in results if r[1] is not None]
    if probabilities:
        unique_probs = set(probabilities)
        print(f"\n� ANALYSIS:")
        print(f"   Predictions tested: {len(probabilities)}")
        print(f"   Unique probability values: {len(unique_probs)}")
        print(f"   Min probability: {min(probabilities)}")
        print(f"   Max probability: {max(probabilities)}")
        print(f"   Average: {np.mean(probabilities):.1f}%")
        
        if len(unique_probs) == 1:
            print("   � ALL PREDICTIONS ARE IDENTICAL - PROBLEM CONFIRMED!")
        else:
            print("   ✅ Predictions show variation - ML might be working")
    
    print(f"\n� DIAGNOSIS COMPLETE")
    print("=" * 50)

if __name__ == "__main__":
    debug_ml_predictions()
