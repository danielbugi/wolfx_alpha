# ml_training/deployment/ml_integration.py

import sys
import os
import json
import pandas as pd
import numpy as np
from datetime import datetime
from typing import Dict, List, Optional

# Add paths for importing ML modules
sys.path.append(os.path.join(os.path.dirname(__file__), '..'))
sys.path.append(os.path.join(os.path.dirname(__file__), '../models'))

try:
    from models.momentum_predictor import MomentumBreakoutPredictor
    from deployment.model_registry import model_registry
except ImportError as e:
    print(f"‚ö†Ô∏è Import warning: {e}")

class MLBreakoutIntegration:
    """
    Integration layer between ML models and existing trading system
    Enhances daily breakout screening with ML predictions
    """
    
    def __init__(self, model_path=None):
        self.predictor = MomentumBreakoutPredictor()
        self.model_loaded = False
        
        # Try to load production model
        if model_path:
            self.model_loaded = self.predictor.load_model(model_path)
        else:
            # Try to load from model registry
            production_path = model_registry.get_production_model_path()
            if production_path:
                self.model_loaded = self.predictor.load_model(production_path)
                print(f"‚úÖ Loaded production model from registry")
            else:
                print("‚ö†Ô∏è No production model found in registry")
    
    def enhance_breakout_signals(self, breakout_signals: List[Dict]) -> List[Dict]:
        """
        Enhance existing breakout signals with ML momentum predictions
        
        Args:
            breakout_signals: List of breakout dictionaries from donchian_screener.py
            
        Returns:
            Enhanced breakout signals with ML predictions
        """
        if not self.model_loaded:
            print("‚ö†Ô∏è ML model not loaded - returning original signals")
            return breakout_signals
        
        print(f"Ì¥ñ Enhancing {len(breakout_signals)} breakout signals with ML predictions...")
        
        enhanced_signals = []
        
        for signal in breakout_signals:
            try:
                # Extract features for ML prediction
                features = self.extract_features_from_signal(signal)
                
                # Get ML momentum prediction
                momentum_probability = self.predictor.predict_momentum_probability(features)
                
                # Add ML enhancements to signal
                enhanced_signal = signal.copy()
                enhanced_signal.update({
                    'ml_momentum_probability': round(momentum_probability * 100, 1),
                    'ml_confidence_level': self.get_confidence_level(momentum_probability),
                    'ml_predicted_category': self.get_momentum_category(momentum_probability),
                    'ml_trade_recommendation': self.get_trade_recommendation(momentum_probability, signal),
                    'ml_risk_score': self.calculate_risk_score(momentum_probability, signal),
                    'ml_enhanced': True
                })
                
                enhanced_signals.append(enhanced_signal)
                
            except Exception as e:
                print(f"‚ö†Ô∏è Error enhancing signal for {signal.get('symbol', 'Unknown')}: {e}")
                # Return original signal if ML enhancement fails
                signal['ml_enhanced'] = False
                enhanced_signals.append(signal)
        
        # Sort by ML momentum probability (highest first)
        enhanced_signals.sort(key=lambda x: x.get('ml_momentum_probability', 0), reverse=True)
        
        print(f"‚úÖ Enhanced {len(enhanced_signals)} signals with ML predictions")
        return enhanced_signals
    
    def extract_features_from_signal(self, signal: Dict) -> Dict:
        """
        Extract ML features from a breakout signal dictionary
        """
        features = {}
        
        # Basic breakout features
        features['is_bullish'] = 1 if signal.get('type') in ['bullish_breakout', 'near_bullish'] else 0
        features['entry_price'] = signal.get('current_price', 0)
        
        # Technical features from signal
        features['volume_ratio'] = signal.get('volume_ratio', 1.0)
        features['rsi_14'] = signal.get('rsi_value', 50)  # If available in signal
        features['price_position'] = signal.get('distance_to_breakout', 0)
        
        # Donchian features
        donchian_high = signal.get('donchian_high', features['entry_price'])
        donchian_low = signal.get('donchian_low', features['entry_price'])
        
        if donchian_high > donchian_low:
            donchian_range = donchian_high - donchian_low
            features['donchian_position'] = ((features['entry_price'] - donchian_low) / donchian_range) * 100
            features['channel_width_pct'] = (donchian_range / features['entry_price']) * 100
        else:
            features['donchian_position'] = 50
            features['channel_width_pct'] = 5
        
        # Quality features (if available)
        features['overall_quality_score'] = signal.get('quality_score', 5) / 10  # Normalize to 0-1
        features['quality_grade_numeric'] = self.grade_to_numeric(signal.get('quality_grade', 'C'))
        
        # Market context features (defaults)
        features['market_avg_rsi'] = 50
        features['market_avg_volume_ratio'] = 1.0
        features['market_avg_price_position'] = 50
        
        # Price momentum features (estimated from available data)
        features['price_5d_change'] = signal.get('momentum_5d', 0)
        features['price_10d_change'] = signal.get('momentum_10d', 0)
        features['volatility_10d'] = signal.get('volatility', 2.0)
        
        # Interaction features
        features['quality_volume'] = features['overall_quality_score'] * features['volume_ratio']
        features['momentum_quality'] = abs(features['price_5d_change']) * features['overall_quality_score']
        
        # Sector features (simplified)
        sector = signal.get('sector', 'Unknown')
        major_sectors = ['Technology', 'Healthcare', 'Financial Services', 'Consumer Cyclical', 'Industrials']
        for sector_name in major_sectors:
            features[f'sector_{sector_name.lower().replace(" ", "_")}'] = 1 if sector == sector_name else 0
        
        # Fill any missing features with defaults
        default_features = {
            'sma_10_vs_20': 0,
            'sma_20_vs_50': 0,
            'bollinger_position': 50,
            'rsi_trend': 0,
            'volume_trend': 0,
            'channel_narrowing': 0,
            'growth_score': 0.5,
            'profitability_score': 0.5,
            'financial_health_score': 0.5,
            'valuation_score': 0.5,
            'pe_ratio': 15,
            'pb_ratio': 2,
            'ps_ratio': 3,
            'peg_ratio': 1,
            'beta': 1.0,
            'dividend_yield': 0,
            'log_market_cap': 9,  # ~1B market cap
            'avg_daily_return': 0,
            'avg_volume_10d': 1000000,
            'volume_volatility': 1.0,
            'volume_acceleration': 1.0,
            'position_in_recent_range': 50,
            'above_recent_high': 0,
            'below_recent_low': 0,
            'market_data_quality': 1.0,
            'stability_quality': 0.5,
            'rsi_macd_confluence': 0,
            'breakout_strength': 1.0
        }
        
        for key, default_value in default_features.items():
            if key not in features:
                features[key] = default_value
        
        return features
    
    def grade_to_numeric(self, grade: str) -> float:
        """Convert letter grade to numeric value"""
        grade_mapping = {'A': 1.0, 'B': 0.75, 'C': 0.5, 'D': 0.25, 'F': 0.0}
        return grade_mapping.get(grade, 0.5)
    
    def get_confidence_level(self, probability: float) -> str:
        """Get confidence level based on probability"""
        if probability >= 0.8:
            return "very_high"
        elif probability >= 0.7:
            return "high"
        elif probability >= 0.6:
            return "medium"
        elif probability >= 0.5:
            return "low"
        else:
            return "very_low"
    
    def get_momentum_category(self, probability: float) -> str:
        """Get momentum category based on probability"""
        if probability >= 0.8:
            return "exceptional"
        elif probability >= 0.65:
            return "strong"
        elif probability >= 0.5:
            return "moderate"
        elif probability >= 0.35:
            return "weak"
        else:
            return "poor"
    
    def get_trade_recommendation(self, probability: float, signal: Dict) -> str:
        """Get trade recommendation based on ML prediction and signal quality"""
        confidence = self.get_confidence_level(probability)
        signal_type = signal.get('type', '')
        
        if probability >= 0.75 and confidence in ['high', 'very_high']:
            return "strong_buy" if 'bullish' in signal_type else "strong_sell"
        elif probability >= 0.65:
            return "buy" if 'bullish' in signal_type else "sell"
        elif probability >= 0.5:
            return "hold"
        else:
            return "avoid"
    
    def calculate_risk_score(self, probability: float, signal: Dict) -> int:
        """Calculate risk score (0-100, higher = more risky)"""
        base_risk = 50  # Neutral risk
        
        # Adjust for prediction confidence
        if probability >= 0.8:
            risk_adjustment = -20  # Lower risk for high confidence
        elif probability >= 0.6:
            risk_adjustment = -10
        elif probability <= 0.4:
            risk_adjustment = 20   # Higher risk for low confidence
        else:
            risk_adjustment = 0
        
        # Adjust for volatility
        volatility = signal.get('volatility', 2.0)
        volatility_risk = min(20, volatility * 5)  # Cap at 20
        
        # Adjust for volume
        volume_ratio = signal.get('volume_ratio', 1.0)
        volume_risk = 10 if volume_ratio < 0.8 else -5  # Low volume = higher risk
        
        total_risk = base_risk + risk_adjustment + volatility_risk + volume_risk
        return max(0, min(100, int(total_risk)))
    
    def create_ml_enhanced_json(self, enhanced_signals: List[Dict], output_file: str = None) -> Dict:
        """
        Create ML-enhanced JSON output for frontend consumption
        """
        print("Ì≥ä Creating ML-enhanced JSON output...")
        
        # Separate by signal types
        bullish_breakouts = [s for s in enhanced_signals if s.get('type') == 'bullish_breakout']
        bearish_breakouts = [s for s in enhanced_signals if s.get('type') == 'bearish_breakout']
        near_bullish = [s for s in enhanced_signals if s.get('type') == 'near_bullish']
        near_bearish = [s for s in enhanced_signals if s.get('type') == 'near_bearish']
        
        # ML-specific insights
        high_confidence_signals = [s for s in enhanced_signals 
                                 if s.get('ml_confidence_level') in ['high', 'very_high']]
        
        top_ml_picks = enhanced_signals[:20]  # Top 20 by ML probability
        
        # Create enhanced JSON structure
        enhanced_json = {
            "metadata": {
                "timestamp": datetime.now().isoformat(),
                "symbols_screened": len(set(s.get('symbol', '') for s in enhanced_signals)),
                "ml_enhanced": True,
                "model_version": getattr(self.predictor, 'model_version', 'unknown'),
                "version": "3.0"
            },
            "summary": {
                "total_signals": len(enhanced_signals),
                "bullish_breakouts": len(bullish_breakouts),
                "bearish_breakouts": len(bearish_breakouts),
                "near_bullish": len(near_bullish),
                "near_bearish": len(near_bearish),
                "high_confidence_signals": len(high_confidence_signals),
                "ml_avg_probability": round(np.mean([s.get('ml_momentum_probability', 0) for s in enhanced_signals]), 1)
            },
            "signals": {
                "bullish_breakouts": bullish_breakouts,
                "bearish_breakouts": bearish_breakouts,
                "near_bullish": near_bullish,
                "near_bearish": near_bearish
            },
            "ml_insights": {
                "top_ml_picks": top_ml_picks,
                "high_confidence": high_confidence_signals,
                "recommendations": {
                    "strong_buy": [s for s in enhanced_signals if s.get('ml_trade_recommendation') == 'strong_buy'],
                    "buy": [s for s in enhanced_signals if s.get('ml_trade_recommendation') == 'buy'],
                    "strong_sell": [s for s in enhanced_signals if s.get('ml_trade_recommendation') == 'strong_sell'],
                    "sell": [s for s in enhanced_signals if s.get('ml_trade_recommendation') == 'sell']
                }
            }
        }
        
        # Save to file if specified
        if output_file:
            with open(output_file, 'w') as f:
                json.dump(enhanced_json, f, indent=2, default=str)
            print(f"‚úÖ ML-enhanced JSON saved: {output_file}")
        
        return enhanced_json
    
    def integrate_with_daily_screening(self, original_json_file: str, output_file: str = None):
        """
        Integrate ML predictions with daily screening results
        """
        print(f"Ì¥ó Integrating ML with daily screening: {original_json_file}")
        
        try:
            # Load original screening results
            with open(original_json_file, 'r') as f:
                original_data = json.load(f)
            
            # Extract all signals
            all_signals = []
            signals_dict = original_data.get('signals', {})
            
            for signal_type, signals in signals_dict.items():
                for signal in signals:
                    signal['type'] = signal_type  # Ensure type is set
                    all_signals.append(signal)
            
            # Enhance with ML
            enhanced_signals = self.enhance_breakout_signals(all_signals)
            
            # Create enhanced JSON
            if not output_file:
                base_name = original_json_file.replace('.json', '')
                output_file = f"{base_name}_ml_enhanced.json"
            
            enhanced_json = self.create_ml_enhanced_json(enhanced_signals, output_file)
            
            print(f"‚úÖ ML integration complete")
            print(f"Ì≥ä Enhanced {len(enhanced_signals)} signals")
            print(f"ÌæØ High confidence signals: {enhanced_json['summary']['high_confidence_signals']}")
            
            return enhanced_json
            
        except Exception as e:
            print(f"‚ùå Error in ML integration: {e}")
            return None

def main():
    """
    Main function for testing ML integration
    """
    print("Ì¥ñ ML BREAKOUT INTEGRATION TEST")
    print("=" * 50)
    
    # Initialize integration
    integration = MLBreakoutIntegration()
    
    if not integration.model_loaded:
        print("‚ùå No ML model loaded - run training pipeline first")
        return
    
    # Test with sample data
    sample_signals = [
        {
            "symbol": "AAPL",
            "type": "bullish_breakout",
            "current_price": 175.50,
            "donchian_high": 180.00,
            "donchian_low": 165.00,
            "volume_ratio": 1.5,
            "distance_to_breakout": 0.5,
            "quality_score": 8,
            "quality_grade": "A",
            "sector": "Technology"
        },
        {
            "symbol": "TSLA", 
            "type": "near_bullish",
            "current_price": 245.20,
            "donchian_high": 250.00,
            "donchian_low": 220.00,
            "volume_ratio": 0.8,
            "distance_to_breakout": 1.9,
            "quality_score": 6,
            "quality_grade": "B",
            "sector": "Consumer Cyclical"
        }
    ]
    
    # Enhance signals
    enhanced = integration.enhance_breakout_signals(sample_signals)
    
    # Display results
    print(f"\nÌ≥ä ENHANCED SIGNALS:")
    for signal in enhanced:
        print(f"   {signal['symbol']}: {signal.get('ml_momentum_probability', 0):.1f}% probability")
        print(f"      Confidence: {signal.get('ml_confidence_level', 'unknown')}")
        print(f"      Recommendation: {signal.get('ml_trade_recommendation', 'unknown')}")
        print(f"      Risk Score: {signal.get('ml_risk_score', 'unknown')}")

if __name__ == "__main__":
    main()
