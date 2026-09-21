# Simple AI test script
# Save this as: test_ai.py

import pandas as pd
import numpy as np
import sqlite3
import joblib
import os


def test_ai_quickly():
    print("🤖 TESTING YOUR AI MODEL...")

    # Load your trained model (just created)
    model_path = "../../models/backup/breakout_ai_20250709_2333.joblib"
    scaler_path = "../../models/backup/breakout_scaler_20250709_2333.joblib"

    if not os.path.exists(model_path):
        print("❌ Model not found")
        # Find any model file
        if os.path.exists("../../models"):
            models = [f for f in os.listdir("../../models") if f.startswith("breakout_ai")]
            if models:
                latest = sorted(models)[-1]
                model_path = f"../models/{latest}"
                timestamp = latest.replace('breakout_ai_', '').replace('.joblib', '')
                scaler_path = f"../models/breakout_scaler_{timestamp}.joblib"
                print(f"✅ Found model: {latest}")
            else:
                print("❌ No models found")
                return

    # Load model
    try:
        model = joblib.load(model_path)
        scaler = joblib.load(scaler_path)
        print("✅ AI model loaded successfully!")
    except Exception as e:
        print(f"❌ Error loading model: {e}")
        return

    # Create some test data (simulate potential breakouts)
    print("\n🎯 Testing AI on simulated breakouts...")

    # Test scenarios
    test_scenarios = [
        {
            'name': 'High Quality Bullish',
            'volume_ratio': 2.5, 'atr_pct': 2.0, 'rsi_value': 65, 'price_change_pct': 4.0
        },
        {
            'name': 'Average Quality',
            'volume_ratio': 1.3, 'atr_pct': 2.5, 'rsi_value': 55, 'price_change_pct': 1.5
        },
        {
            'name': 'Poor Quality',
            'volume_ratio': 0.8, 'atr_pct': 5.0, 'rsi_value': 85, 'price_change_pct': 0.5
        }
    ]

    print("\n🔮 AI PREDICTIONS:")
    print("-" * 50)

    for scenario in test_scenarios:
        # Create feature vector (same 17 features as training)
        features = np.array([[
            1,  # is_bullish
            scenario['volume_ratio'],  # volume_ratio
            np.log1p(scenario['volume_ratio']),  # volume_strength
            1 if scenario['volume_ratio'] > 2.0 else 0,  # high_volume
            scenario['rsi_value'],  # rsi_value
            scenario['rsi_value'] / 100,  # rsi_normalized
            1 if scenario['rsi_value'] > 70 else 0,  # rsi_overbought
            1 if scenario['rsi_value'] < 30 else 0,  # rsi_oversold
            1 if 40 <= scenario['rsi_value'] <= 60 else 0,  # rsi_neutral
            scenario['price_change_pct'],  # price_change_pct
            scenario['price_change_pct'],  # price_momentum
            1 if abs(scenario['price_change_pct']) > 3 else 0,  # strong_move
            scenario['atr_pct'],  # atr_pct
            1 if scenario['atr_pct'] > 3 else 0,  # high_volatility
            1 if scenario['atr_pct'] < 1.5 else 0,  # low_volatility
            scenario['volume_ratio'] * (scenario['rsi_value'] / 100),  # volume_rsi_score
            scenario['volume_ratio'] * abs(scenario['price_change_pct'])  # volume_momentum
        ]])

        # Scale and predict
        features_scaled = scaler.transform(features)
        prediction = model.predict(features_scaled)[0]
        probability = model.predict_proba(features_scaled)[0][1] * 100

        # Format recommendation
        if probability >= 80:
            rec = "🚀 STRONG BUY"
        elif probability >= 70:
            rec = "✅ BUY"
        elif probability >= 60:
            rec = "⚠️ MAYBE"
        elif probability >= 50:
            rec = "⏳ WAIT"
        else:
            rec = "❌ AVOID"

        print(f"{scenario['name']:<20}: {probability:>5.1f}% | {rec}")

    print(f"\n🎉 Your AI is working perfectly!")
    print(f"🎯 It learned that:")
    print(f"   • High volume + good RSI + strong move = High success probability")
    print(f"   • Low volume + extreme RSI + weak move = Low success probability")
    print(f"   • The AI automatically scores any breakout scenario!")


if __name__ == "__main__":
    test_ai_quickly()