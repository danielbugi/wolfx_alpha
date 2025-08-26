#!/usr/bin/env python3
"""
ML Model Training Readiness Checker
Comprehensive analysis of database readiness for breakout prediction model training
"""

import sys
import os
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import warnings

warnings.filterwarnings('ignore')

# Add automation to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'mechanism'))

try:
    from shared import db, setup_logging

    print("✅ SUCCESS: Imported shared modules")
except ImportError as e:
    print(f"❌ Import error: {e}")
    print("Run this from the project root directory")
    sys.exit(1)


class MLReadinessChecker:
    """Comprehensive ML readiness assessment"""

    def __init__(self):
        self.logger = setup_logging(__name__, level='WARNING')
        self.readiness_score = 0
        self.max_score = 100
        self.issues = []
        self.recommendations = []

    def check_database_overview(self):
        """Check overall database health and record counts"""
        print("\n" + "=" * 60)
        print("📊 DATABASE OVERVIEW")
        print("=" * 60)

        score = 0

        try:
            tables_required = {
                'stock_prices': 400000,  # Minimum 400K records
                'technical_indicators': 300000,  # Minimum 300K records
                'daily_fundamentals': 400000,  # Minimum 400K records
                'breakouts': 20000,  # Minimum 20K breakout events
                'ml_training_data': 20000  # Minimum 20K ML records
            }

            for table, min_records in tables_required.items():
                try:
                    query = f"SELECT COUNT(*) as count FROM {table}"
                    result = db.execute_dict_query(query)
                    count = result[0]['count'] if result else 0

                    status = "✅" if count >= min_records else "⚠️"
                    print(f"  {status} {table:20} {count:>10,} records (min: {min_records:,})")

                    if count >= min_records:
                        score += 4  # 20 points total for 5 tables
                    elif count >= min_records * 0.7:
                        score += 2  # Partial credit
                        self.issues.append(f"{table} has {count:,} records (below minimum {min_records:,})")
                    else:
                        self.issues.append(f"{table} critically low: {count:,} records (minimum {min_records:,})")

                except Exception as e:
                    print(f"  ❌ {table:20} ERROR: {e}")
                    self.issues.append(f"Cannot access {table}: {e}")

        except Exception as e:
            print(f"❌ Database overview failed: {e}")
            self.issues.append(f"Database connection issues: {e}")

        self.readiness_score += score
        print(f"\n📊 Database Overview Score: {score}/20")
        return score

    def check_breakout_data_quality(self):
        """Check quality and distribution of breakout data"""
        print("\n" + "=" * 60)
        print("🎯 BREAKOUT DATA QUALITY")
        print("=" * 60)

        score = 0

        try:
            # Check breakout distribution
            query = """
            SELECT 
                breakout_type,
                COUNT(*) as count,
                ROUND(AVG(CASE WHEN success THEN 1 ELSE 0 END) * 100, 1) as success_rate
            FROM breakouts 
            GROUP BY breakout_type
            """

            results = db.execute_dict_query(query)

            if results:
                print("  Breakout Type Distribution:")
                total_breakouts = sum(row['count'] for row in results)
                bullish_count = 0
                bearish_count = 0

                for row in results:
                    breakout_type = row['breakout_type']
                    count = row['count']
                    success_rate = row['success_rate']
                    percentage = (count / total_breakouts) * 100

                    print(
                        f"    {breakout_type:10} {count:>8,} ({percentage:5.1f}%) - {success_rate:5.1f}% success rate")

                    if breakout_type == 'bullish':
                        bullish_count = count
                    elif breakout_type == 'bearish':
                        bearish_count = count

                # Check balance
                if bullish_count > 0 and bearish_count > 0:
                    balance_ratio = min(bullish_count, bearish_count) / max(bullish_count, bearish_count)
                    print(f"\n  📊 Dataset Balance Ratio: {balance_ratio:.2f}")

                    if balance_ratio >= 0.4:  # At least 40% representation
                        score += 5
                        print("  ✅ Good balance between bullish and bearish breakouts")
                    else:
                        score += 2
                        self.issues.append(f"Imbalanced dataset: {balance_ratio:.2f} ratio between breakout types")

                # Check total volume
                if total_breakouts >= 30000:
                    score += 5
                    print("  ✅ Sufficient breakout volume for ML training")
                elif total_breakouts >= 20000:
                    score += 3
                    print("  ⚠️ Adequate breakout volume, but more data would be better")
                else:
                    score += 1
                    self.issues.append(f"Low breakout volume: {total_breakouts:,} (recommended: 30,000+)")

            else:
                self.issues.append("No breakout data found")

            # Check data freshness
            query = "SELECT MAX(date) as latest_date, MIN(date) as earliest_date FROM breakouts"
            result = db.execute_dict_query(query)

            if result and result[0]['latest_date']:
                latest_date = result[0]['latest_date']
                earliest_date = result[0]['earliest_date']

                if isinstance(latest_date, str):
                    latest_date = datetime.strptime(latest_date, '%Y-%m-%d').date()
                if isinstance(earliest_date, str):
                    earliest_date = datetime.strptime(earliest_date, '%Y-%m-%d').date()

                date_range = (latest_date - earliest_date).days
                today = datetime.now().date()
                days_old = (today - latest_date).days

                print(f"\n  📅 Data Range: {earliest_date} to {latest_date} ({date_range} days)")
                print(f"  📅 Data Age: {days_old} days old")

                if date_range >= 365:  # At least 1 year of data
                    score += 5
                    print("  ✅ Sufficient historical range for training")
                elif date_range >= 180:  # At least 6 months
                    score += 3
                    print("  ⚠️ Adequate historical range")
                else:
                    score += 1
                    self.issues.append(f"Limited historical range: {date_range} days (recommended: 365+ days)")

                if days_old <= 30:  # Recent data
                    score += 5
                    print("  ✅ Recent breakout data available")
                elif days_old <= 90:
                    score += 3
                    print("  ⚠️ Moderately recent data")
                else:
                    self.issues.append(f"Stale breakout data: {days_old} days old")

        except Exception as e:
            print(f"❌ Breakout data quality check failed: {e}")
            self.issues.append(f"Breakout data quality check failed: {e}")

        self.readiness_score += score
        print(f"\n🎯 Breakout Data Quality Score: {score}/20")
        return score

    def check_feature_completeness(self):
        """Check completeness of features for ML training"""
        print("\n" + "=" * 60)
        print("🔧 FEATURE COMPLETENESS")
        print("=" * 60)

        score = 0

        try:
            # Check technical indicators completeness
            query = """
            SELECT 
                COUNT(*) as total_records,
                COUNT(donchian_high_20) as donchian_records,
                COUNT(rsi_14) as rsi_records,
                COUNT(volume_ratio) as volume_records,
                COUNT(sma_20) as sma_records
            FROM technical_indicators
            WHERE date >= CURRENT_DATE - INTERVAL '1 year'
            """

            result = db.execute_dict_query(query)

            if result:
                row = result[0]
                total = row['total_records']

                features = {
                    'Donchian Channels': row['donchian_records'],
                    'RSI': row['rsi_records'],
                    'Volume Ratio': row['volume_records'],
                    'SMA': row['sma_records']
                }

                print("  Technical Indicators Completeness (last year):")

                for feature, count in features.items():
                    if total > 0:
                        completeness = (count / total) * 100
                        status = "✅" if completeness >= 95 else "⚠️" if completeness >= 80 else "❌"
                        print(f"    {status} {feature:20} {completeness:5.1f}% ({count:,}/{total:,})")

                        if completeness >= 95:
                            score += 2.5  # 10 points total for 4 features
                        elif completeness >= 80:
                            score += 1.5
                            self.issues.append(f"{feature} completeness low: {completeness:.1f}%")
                        else:
                            self.issues.append(f"{feature} critically incomplete: {completeness:.1f}%")

            # Check fundamentals completeness
            query = """
            SELECT 
                COUNT(*) as total_records,
                COUNT(market_cap) as market_cap_records,
                COUNT(pe_ratio) as pe_records,
                COUNT(sector) as sector_records,
                COUNT(overall_quality_score) as quality_records
            FROM daily_fundamentals
            WHERE date >= CURRENT_DATE - INTERVAL '1 year'
            """

            result = db.execute_dict_query(query)

            if result:
                row = result[0]
                total = row['total_records']

                fundamentals = {
                    'Market Cap': row['market_cap_records'],
                    'PE Ratio': row['pe_records'],
                    'Sector': row['sector_records'],
                    'Quality Score': row['quality_records']
                }

                print("\n  Fundamentals Completeness (last year):")

                for feature, count in fundamentals.items():
                    if total > 0:
                        completeness = (count / total) * 100
                        status = "✅" if completeness >= 90 else "⚠️" if completeness >= 70 else "❌"
                        print(f"    {status} {feature:20} {completeness:5.1f}% ({count:,}/{total:,})")

                        if completeness >= 90:
                            score += 2.5  # 10 points total for 4 features
                        elif completeness >= 70:
                            score += 1.5
                            self.issues.append(f"{feature} fundamentals completeness low: {completeness:.1f}%")
                        else:
                            self.issues.append(f"{feature} fundamentals critically incomplete: {completeness:.1f}%")

        except Exception as e:
            print(f"❌ Feature completeness check failed: {e}")
            self.issues.append(f"Feature completeness check failed: {e}")

        self.readiness_score += score
        print(f"\n🔧 Feature Completeness Score: {score}/20")
        return score

    def check_ml_training_data(self):
        """Check ML training data table readiness"""
        print("\n" + "=" * 60)
        print("🤖 ML TRAINING DATA")
        print("=" * 60)

        score = 0

        try:
            # Check if ml_training_data exists and has records
            query = "SELECT COUNT(*) as count FROM ml_training_data"
            result = db.execute_dict_query(query)

            if result:
                count = result[0]['count']
                print(f"  📊 ML Training Records: {count:,}")

                if count >= 30000:
                    score += 5
                    print("  ✅ Excellent volume of ML training data")
                elif count >= 20000:
                    score += 4
                    print("  ✅ Good volume of ML training data")
                elif count >= 10000:
                    score += 2
                    print("  ⚠️ Adequate ML training data")
                    self.recommendations.append("Generate more ML training data for better model performance")
                else:
                    score += 1
                    self.issues.append(f"Insufficient ML training data: {count:,} (recommended: 20,000+)")

            # Check feature columns in ml_training_data
            query = """
            SELECT column_name 
            FROM information_schema.columns 
            WHERE table_name = 'ml_training_data'
            ORDER BY ordinal_position
            """

            result = db.execute_dict_query(query)

            if result:
                columns = [row['column_name'] for row in result]
                required_features = [
                    'symbol', 'date', 'entry_price', 'breakout_type', 'success',
                    'rsi_value', 'volume_ratio', 'atr_pct', 'overall_quality_score',
                    'sector', 'max_gain_10d', 'max_loss_10d'
                ]

                missing_features = [f for f in required_features if f not in columns]

                print(f"\n  🔧 Available Features: {len(columns)}")
                print(f"  📋 Required Features: {len(required_features)}")

                if not missing_features:
                    score += 10
                    print("  ✅ All required ML features available")
                elif len(missing_features) <= 2:
                    score += 7
                    print(f"  ⚠️ Minor missing features: {missing_features}")
                    self.recommendations.append(f"Add missing ML features: {missing_features}")
                else:
                    score += 3
                    self.issues.append(f"Many missing ML features: {missing_features}")

            # Check target variable distribution
            query = """
            SELECT 
                success,
                COUNT(*) as count,
                ROUND(COUNT(*) * 100.0 / SUM(COUNT(*)) OVER(), 1) as percentage
            FROM ml_training_data 
            GROUP BY success
            """

            result = db.execute_dict_query(query)

            if result:
                print("\n  🎯 Target Variable Distribution:")
                success_counts = {}

                for row in result:
                    success = "Success" if row['success'] else "Failure"
                    count = row['count']
                    percentage = row['percentage']
                    print(f"    {success:10} {count:>8,} ({percentage:5.1f}%)")
                    success_counts[row['success']] = count

                # Check target balance
                if True in success_counts and False in success_counts:
                    success_ratio = min(success_counts.values()) / max(success_counts.values())
                    print(f"\n  ⚖️ Target Balance Ratio: {success_ratio:.2f}")

                    if success_ratio >= 0.3:  # At least 30% minority class
                        score += 5
                        print("  ✅ Good target variable balance")
                    elif success_ratio >= 0.2:
                        score += 3
                        print("  ⚠️ Acceptable target balance")
                        self.recommendations.append("Consider balancing techniques for target variable")
                    else:
                        score += 1
                        self.issues.append(f"Imbalanced target variable: {success_ratio:.2f} ratio")

        except Exception as e:
            print(f"❌ ML training data check failed: {e}")
            self.issues.append(f"ML training data check failed: {e}")

        self.readiness_score += score
        print(f"\n🤖 ML Training Data Score: {score}/20")
        return score

    def check_data_quality(self):
        """Check overall data quality and consistency"""
        print("\n" + "=" * 60)
        print("🔍 DATA QUALITY")
        print("=" * 60)

        score = 0

        try:
            # Check for data gaps in recent period
            query = """
            SELECT 
                date,
                COUNT(DISTINCT symbol) as symbol_count
            FROM stock_prices 
            WHERE date >= CURRENT_DATE - INTERVAL '30 days'
            GROUP BY date 
            ORDER BY date DESC
            LIMIT 10
            """

            result = db.execute_dict_query(query)

            if result:
                print("  Recent Data Consistency (last 10 trading days):")
                symbol_counts = [row['symbol_count'] for row in result]

                if symbol_counts:
                    avg_symbols = np.mean(symbol_counts)
                    min_symbols = min(symbol_counts)
                    max_symbols = max(symbol_counts)
                    consistency = min_symbols / max_symbols if max_symbols > 0 else 0

                    for row in result[:5]:  # Show first 5 days
                        date = row['date']
                        count = row['symbol_count']
                        status = "✅" if count >= avg_symbols * 0.95 else "⚠️"
                        print(f"    {status} {date}: {count:,} symbols")

                    print(f"\n  📊 Symbol Count Range: {min_symbols:,} - {max_symbols:,}")
                    print(f"  📊 Consistency Score: {consistency:.2f}")

                    if consistency >= 0.95:
                        score += 10
                        print("  ✅ Excellent data consistency")
                    elif consistency >= 0.90:
                        score += 7
                        print("  ✅ Good data consistency")
                    elif consistency >= 0.85:
                        score += 5
                        print("  ⚠️ Acceptable data consistency")
                        self.recommendations.append("Investigate data gaps in recent trading days")
                    else:
                        score += 2
                        self.issues.append(f"Poor data consistency: {consistency:.2f}")

            # Check for duplicate records
            query = """
            SELECT COUNT(*) as duplicates
            FROM (
                SELECT symbol, date, COUNT(*) 
                FROM stock_prices 
                GROUP BY symbol, date 
                HAVING COUNT(*) > 1
            ) dup_check
            """

            result = db.execute_dict_query(query)

            if result:
                duplicates = result[0]['duplicates']
                print(f"\n  🔍 Duplicate Records Check:")
                print(f"    Stock Prices Duplicates: {duplicates:,}")

                if duplicates == 0:
                    score += 5
                    print("    ✅ No duplicate records found")
                elif duplicates < 100:
                    score += 3
                    print("    ⚠️ Minor duplicate issues")
                    self.recommendations.append("Clean up duplicate stock price records")
                else:
                    score += 1
                    self.issues.append(f"Significant duplicate records: {duplicates:,}")

            # Check for extreme outliers in price data
            query = """
            SELECT 
                COUNT(*) as total_records,
                COUNT(CASE WHEN close <= 0 THEN 1 END) as zero_prices,
                COUNT(CASE WHEN volume <= 0 THEN 1 END) as zero_volume
            FROM stock_prices 
            WHERE date >= CURRENT_DATE - INTERVAL '1 year'
            """

            result = db.execute_dict_query(query)

            if result:
                row = result[0]
                total = row['total_records']
                zero_prices = row['zero_prices']
                zero_volume = row['zero_volume']

                print(f"\n  📊 Data Quality Metrics (last year):")
                print(f"    Zero/Negative Prices: {zero_prices:,} ({zero_prices / total * 100:.2f}%)")
                print(f"    Zero Volume Records: {zero_volume:,} ({zero_volume / total * 100:.2f}%)")

                if zero_prices == 0 and zero_volume < total * 0.01:  # Less than 1%
                    score += 5
                    print("    ✅ Excellent data quality")
                elif zero_prices < total * 0.001 and zero_volume < total * 0.05:  # Less than 0.1% and 5%
                    score += 3
                    print("    ✅ Good data quality")
                else:
                    score += 1
                    self.issues.append("Data quality issues with zero/negative values")

        except Exception as e:
            print(f"❌ Data quality check failed: {e}")
            self.issues.append(f"Data quality check failed: {e}")

        self.readiness_score += score
        print(f"\n🔍 Data Quality Score: {score}/20")
        return score

    def generate_recommendations(self):
        """Generate specific recommendations for ML readiness"""
        print("\n" + "=" * 60)
        print("💡 RECOMMENDATIONS")
        print("=" * 60)

        # Add readiness-based recommendations
        if self.readiness_score >= 85:
            print("  🎉 Your database is READY for ML model training!")
            self.recommendations.append("Proceed with ML model development")
            self.recommendations.append("Consider creating train/validation/test splits")
            self.recommendations.append("Start with simple models (Random Forest, XGBoost)")

        elif self.readiness_score >= 70:
            print("  ✅ Your database is MOSTLY READY for ML training")
            self.recommendations.append("Address minor issues before training")
            self.recommendations.append("Consider generating more historical data")

        elif self.readiness_score >= 50:
            print("  ⚠️ Your database needs IMPROVEMENTS before ML training")
            self.recommendations.append("Focus on data quality and completeness issues")
            self.recommendations.append("Generate more ML training data")
            self.recommendations.append("Improve feature completeness")

        else:
            print("  ❌ Your database is NOT READY for ML training")
            self.recommendations.append("Significant data collection and quality improvements needed")
            self.recommendations.append("Focus on basic data pipeline stability first")

        # Print all recommendations
        if self.recommendations:
            print("\n  📋 Specific Recommendations:")
            for i, rec in enumerate(self.recommendations, 1):
                print(f"    {i}. {rec}")

        # Print issues to address
        if self.issues:
            print("\n  🚨 Issues to Address:")
            for i, issue in enumerate(self.issues, 1):
                print(f"    {i}. {issue}")

    def run_full_assessment(self):
        """Run complete ML readiness assessment"""
        print("🤖 ML MODEL TRAINING READINESS ASSESSMENT")
        print("=" * 60)
        print("Analyzing database readiness for breakout prediction model training...")

        # Run all checks
        db_score = self.check_database_overview()
        breakout_score = self.check_breakout_data_quality()
        feature_score = self.check_feature_completeness()
        ml_score = self.check_ml_training_data()
        quality_score = self.check_data_quality()

        # Generate final report
        print("\n" + "=" * 60)
        print("📊 FINAL ML READINESS REPORT")
        print("=" * 60)

        print(f"  📊 Database Overview:     {db_score:2.1f}/20")
        print(f"  🎯 Breakout Data Quality: {breakout_score:2.1f}/20")
        print(f"  🔧 Feature Completeness:  {feature_score:2.1f}/20")
        print(f"  🤖 ML Training Data:      {ml_score:2.1f}/20")
        print(f"  🔍 Data Quality:          {quality_score:2.1f}/20")
        print(f"  {'=' * 30}")
        print(f"  🏆 TOTAL SCORE:           {self.readiness_score:2.1f}/100")

        # Readiness level
        if self.readiness_score >= 85:
            readiness = "🟢 READY"
        elif self.readiness_score >= 70:
            readiness = "🟡 MOSTLY READY"
        elif self.readiness_score >= 50:
            readiness = "🟠 NEEDS IMPROVEMENT"
        else:
            readiness = "🔴 NOT READY"

        print(f"\n  🎯 ML READINESS STATUS: {readiness}")

        # Generate recommendations
        self.generate_recommendations()

        return self.readiness_score


def main():
    """Main execution function"""
    import argparse

    parser = argparse.ArgumentParser(description='ML Model Training Readiness Checker')
    parser.add_argument('--quick', action='store_true', help='Quick assessment only')

    args = parser.parse_args()

    # Setup logging
    setup_logging(level='WARNING')

    # Run assessment
    checker = MLReadinessChecker()

    if args.quick:
        # Quick check - just database overview
        print("🤖 QUICK ML READINESS CHECK")
        print("=" * 60)
        checker.check_database_overview()
        checker.check_ml_training_data()
        print(f"\n🎯 Quick Score: {checker.readiness_score}/40")
    else:
        # Full assessment
        score = checker.run_full_assessment()

        print(f"\n✅ Assessment completed! Score: {score}/100")

        if score >= 70:
            print("\n🚀 Next steps:")
            print("  1. Run historical breakouts generator:")
            print("     python mechanism/ml_generators/historical_breakouts_generator.py")
            print("  2. Start ML model development")
            print("  3. Consider feature engineering and selection")


if __name__ == "__main__":
    main()