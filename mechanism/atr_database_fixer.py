#!/usr/bin/env python3
"""
ATR Database Fixer Script
Calculates and populates missing ATR values in technical_indicators table
Generates comprehensive report of all fixes applied
"""

import pandas as pd
import numpy as np
import sys
import os
import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Tuple, Optional

sys.path.insert(0, os.path.dirname(__file__))

try:
    from shared import db, setup_logging, config

    logger = setup_logging("atr_database_fixer")
except ImportError as e:
    print(f"Import error: {e}")
    print("Make sure the script is in the mechanism/ directory with shared/ subdirectory")
    sys.exit(1)


class ATRDatabaseFixer:
    """Comprehensive ATR calculation and database fixing utility"""

    def __init__(self):
        self.logger = setup_logging("ATRDatabaseFixer")
        self.logger.info("ATR Database Fixer initialized")

        # Statistics tracking
        self.stats = {
            'start_time': None,
            'end_time': None,
            'symbols_analyzed': 0,
            'symbols_needing_atr': 0,
            'symbols_processed_successfully': 0,
            'symbols_failed': 0,
            'total_atr_records_created': 0,
            'total_atr_records_updated': 0,
            'validation_passed': 0,
            'validation_failed': 0,
            'errors_encountered': []
        }

        self.report_data = {
            'execution_summary': {},
            'symbol_details': [],
            'validation_results': {},
            'before_after_comparison': {},
            'recommendations': []
        }

    def safe_float(self, value) -> Optional[float]:
        """Safely convert to float"""
        if value is None:
            return None
        try:
            result = float(value)
            if np.isnan(result) or np.isinf(result):
                return None
            return result
        except (ValueError, TypeError):
            return None

    def analyze_database_atr_status(self) -> Dict:
        """Analyze current ATR status in database"""
        self.logger.info("Analyzing current ATR status in database...")

        try:
            # Overall ATR status
            overview_query = """
                SELECT 
                    COUNT(*) as total_indicator_records,
                    COUNT(CASE WHEN atr_14 IS NOT NULL THEN 1 END) as records_with_atr,
                    COUNT(CASE WHEN atr_14 IS NULL THEN 1 END) as records_without_atr,
                    COUNT(DISTINCT symbol) as total_symbols,
                    MIN(date) as earliest_date,
                    MAX(date) as latest_date
                FROM technical_indicators
            """

            overview = db.execute_dict_query(overview_query)[0]

            # Symbol-level ATR status
            symbol_status_query = """
                SELECT 
                    symbol,
                    COUNT(*) as total_records,
                    COUNT(CASE WHEN atr_14 IS NOT NULL THEN 1 END) as with_atr,
                    COUNT(CASE WHEN atr_14 IS NULL THEN 1 END) as without_atr,
                    MAX(date) as latest_date,
                    MIN(date) as earliest_date
                FROM technical_indicators
                GROUP BY symbol
                HAVING COUNT(CASE WHEN atr_14 IS NULL THEN 1 END) > 0
                ORDER BY without_atr DESC, symbol
            """

            symbols_needing_atr = db.execute_dict_query(symbol_status_query)

            # Recent data status (last 30 days)
            recent_status_query = """
                SELECT 
                    COUNT(*) as recent_total_records,
                    COUNT(CASE WHEN atr_14 IS NOT NULL THEN 1 END) as recent_with_atr,
                    COUNT(CASE WHEN atr_14 IS NULL THEN 1 END) as recent_without_atr,
                    COUNT(DISTINCT symbol) as recent_symbols
                FROM technical_indicators
                WHERE date >= CURRENT_DATE - INTERVAL '30 days'
            """

            recent_status = db.execute_dict_query(recent_status_query)[0]

            analysis = {
                'overview': overview,
                'symbols_needing_atr': symbols_needing_atr,
                'recent_status': recent_status,
                'analysis_timestamp': datetime.now().isoformat()
            }

            self.logger.info(f"Database Analysis Complete:")
            self.logger.info(f"  Total indicator records: {overview['total_indicator_records']:,}")
            self.logger.info(f"  Records with ATR: {overview['records_with_atr']:,}")
            self.logger.info(f"  Records WITHOUT ATR: {overview['records_without_atr']:,}")
            self.logger.info(f"  Symbols needing ATR: {len(symbols_needing_atr)}")

            return analysis

        except Exception as e:
            self.logger.error(f"Database analysis failed: {e}")
            self.stats['errors_encountered'].append(f"Database analysis failed: {e}")
            return {}

    def calculate_atr_for_symbol(self, symbol: str, period: int = 14) -> Dict:
        """Calculate ATR for a single symbol and return detailed results"""
        symbol_result = {
            'symbol': symbol,
            'success': False,
            'records_updated': 0,
            'records_created': 0,
            'atr_range': {'min': None, 'max': None, 'avg': None},
            'date_range': {'start': None, 'end': None},
            'error': None
        }

        try:
            self.logger.debug(f"Calculating ATR for {symbol}")

            # Get price data ordered chronologically
            price_query = """
                SELECT date, high, low, close
                FROM stock_prices
                WHERE symbol = %s
                ORDER BY date
            """

            price_data = db.execute_dict_query(price_query, (symbol,))

            if len(price_data) < period + 1:
                error_msg = f"Insufficient price data ({len(price_data)} days, need {period + 1})"
                symbol_result['error'] = error_msg
                self.logger.warning(f"{symbol}: {error_msg}")
                return symbol_result

            # Calculate True Range for each day
            tr_calculations = []
            for i in range(1, len(price_data)):
                current = price_data[i]
                previous = price_data[i - 1]

                high = self.safe_float(current['high'])
                low = self.safe_float(current['low'])
                close_prev = self.safe_float(previous['close'])

                if all(x is not None for x in [high, low, close_prev]):
                    # True Range = max(high-low, |high-close_prev|, |low-close_prev|)
                    high_low = high - low
                    high_close_prev = abs(high - close_prev)
                    low_close_prev = abs(low - close_prev)

                    true_range = max(high_low, high_close_prev, low_close_prev)

                    tr_calculations.append({
                        'date': current['date'],
                        'true_range': true_range,
                        'high': high,
                        'low': low,
                        'close_prev': close_prev
                    })

            if len(tr_calculations) < period:
                error_msg = f"Insufficient TR calculations ({len(tr_calculations)}, need {period})"
                symbol_result['error'] = error_msg
                self.logger.warning(f"{symbol}: {error_msg}")
                return symbol_result

            # Calculate ATR using Wilder's smoothing method
            atr_updates = []
            atr = None
            atr_values = []

            for i, tr_record in enumerate(tr_calculations):
                if i < period - 1:
                    continue  # Need at least 'period' values
                elif i == period - 1:
                    # First ATR calculation - use simple average of last 'period' TRs
                    recent_trs = [tr_calculations[j]['true_range'] for j in range(i - period + 1, i + 1)]
                    atr = sum(recent_trs) / len(recent_trs)
                else:
                    # Subsequent ATR calculations - use Wilder's smoothing
                    current_tr = tr_record['true_range']
                    alpha = 1.0 / period  # Wilder's smoothing factor
                    atr = alpha * current_tr + (1 - alpha) * atr

                # Store ATR update
                atr_value = round(atr, 6)
                atr_values.append(atr_value)
                atr_updates.append({
                    'date': tr_record['date'],
                    'atr_14': atr_value
                })

            # Update database with ATR values
            if atr_updates:
                # Update database with ATR values using the correct db interface
                update_query = """
                    UPDATE technical_indicators
                    SET atr_14 = %s, updated_at = %s
                    WHERE symbol = %s AND date = %s AND atr_14 IS NULL
                """

                updated_count = 0

                try:
                    for atr_record in atr_updates:
                        # Use the existing db.execute_insert method (which handles UPDATE too)
                        try:
                            db.execute_insert(update_query, (
                                atr_record['atr_14'],
                                datetime.now(),
                                symbol,
                                atr_record['date']
                            ))
                            updated_count += 1
                        except Exception as e:
                            self.logger.warning(f"Failed to update ATR for {symbol} on {atr_record['date']}: {e}")
                            continue

                    # Calculate statistics
                    symbol_result.update({
                        'success': True,
                        'records_updated': updated_count,
                        'atr_range': {
                            'min': round(min(atr_values), 6),
                            'max': round(max(atr_values), 6),
                            'avg': round(sum(atr_values) / len(atr_values), 6)
                        },
                        'date_range': {
                            'start': atr_updates[0]['date'].isoformat() if hasattr(atr_updates[0]['date'],
                                                                                   'isoformat') else str(
                                atr_updates[0]['date']),
                            'end': atr_updates[-1]['date'].isoformat() if hasattr(atr_updates[-1]['date'],
                                                                                  'isoformat') else str(
                                atr_updates[-1]['date'])
                        }
                    })

                    self.logger.info(
                        f"✅ {symbol}: Updated {updated_count} ATR records (range: {symbol_result['atr_range']['min']:.4f} - {symbol_result['atr_range']['max']:.4f})")

                except Exception as e:
                    error_msg = f"Database update failed: {e}"
                    symbol_result['error'] = error_msg
                    self.logger.error(f"{symbol}: {error_msg}")

            return symbol_result

        except Exception as e:
            error_msg = f"ATR calculation failed: {e}"
            symbol_result['error'] = error_msg
            self.logger.error(f"{symbol}: {error_msg}")
            return symbol_result

    def validate_atr_calculations(self, symbols_processed: List[str]) -> Dict:
        """Validate the ATR calculations are reasonable"""
        self.logger.info("Validating ATR calculations...")

        try:
            validation_query = """
                SELECT 
                    symbol,
                    COUNT(*) as atr_count,
                    MIN(atr_14) as min_atr,
                    MAX(atr_14) as max_atr,
                    AVG(atr_14) as avg_atr,
                    STDDEV(atr_14) as stddev_atr
                FROM technical_indicators
                WHERE symbol = ANY(%s)
                AND atr_14 IS NOT NULL
                AND date >= CURRENT_DATE - INTERVAL '90 days'
                GROUP BY symbol
            """

            validation_results = db.execute_dict_query(validation_query, (symbols_processed,))

            validation_summary = {
                'total_symbols_validated': len(validation_results),
                'validation_issues': [],
                'statistics': {
                    'avg_atr_range': {'min': None, 'max': None, 'mean': None},
                    'symbols_with_suspicious_atr': []
                }
            }

            atr_averages = []
            for result in validation_results:
                symbol = result['symbol']
                min_atr = float(result['min_atr'])
                max_atr = float(result['max_atr'])
                avg_atr = float(result['avg_atr'])

                atr_averages.append(avg_atr)

                # Validation checks
                if min_atr <= 0:
                    validation_summary['validation_issues'].append(
                        f"{symbol}: ATR minimum is {min_atr} (should be positive)")

                if max_atr > avg_atr * 10:  # ATR shouldn't vary too wildly
                    validation_summary['validation_issues'].append(
                        f"{symbol}: ATR max ({max_atr:.4f}) is suspiciously high vs avg ({avg_atr:.4f})")

                if avg_atr > 50:  # Very high ATR might indicate calculation error
                    validation_summary['symbols_with_suspicious_atr'].append({
                        'symbol': symbol,
                        'avg_atr': avg_atr,
                        'reason': 'Unusually high ATR value'
                    })

            if atr_averages:
                validation_summary['statistics']['avg_atr_range'] = {
                    'min': round(min(atr_averages), 4),
                    'max': round(max(atr_averages), 4),
                    'mean': round(sum(atr_averages) / len(atr_averages), 4)
                }

            # Overall validation status
            total_issues = len(validation_summary['validation_issues']) + len(
                validation_summary['symbols_with_suspicious_atr'])
            validation_summary[
                'overall_status'] = 'PASSED' if total_issues == 0 else 'WARNINGS' if total_issues < 5 else 'FAILED'

            self.logger.info(
                f"Validation complete: {validation_summary['overall_status']} ({total_issues} issues found)")

            return validation_summary

        except Exception as e:
            self.logger.error(f"Validation failed: {e}")
            return {'overall_status': 'ERROR', 'error': str(e)}

    def generate_comprehensive_report(self) -> Dict:
        """Generate comprehensive report of ATR fixing operation"""
        self.logger.info("Generating comprehensive report...")

        try:
            # Calculate execution time
            if self.stats['start_time'] and self.stats['end_time']:
                execution_time = self.stats['end_time'] - self.stats['start_time']
                execution_seconds = execution_time.total_seconds()
            else:
                execution_seconds = 0

            # Success rates
            total_symbols = self.stats['symbols_analyzed']
            success_rate = (self.stats['symbols_processed_successfully'] / max(1, total_symbols)) * 100

            # Create comprehensive report
            report = {
                'report_metadata': {
                    'report_timestamp': datetime.now().isoformat(),
                    'report_version': 'ATR_DATABASE_FIXER_v1.0',
                    'execution_time_seconds': round(execution_seconds, 2),
                    'execution_time_formatted': str(timedelta(seconds=execution_seconds)),
                },
                'execution_summary': {
                    'total_symbols_analyzed': total_symbols,
                    'symbols_needing_atr': self.stats['symbols_needing_atr'],
                    'symbols_processed_successfully': self.stats['symbols_processed_successfully'],
                    'symbols_failed': self.stats['symbols_failed'],
                    'success_rate_percent': round(success_rate, 2),
                    'total_atr_records_updated': self.stats['total_atr_records_updated'],
                    'average_records_per_symbol': round(
                        self.stats['total_atr_records_updated'] / max(1, self.stats['symbols_processed_successfully']),
                        1)
                },
                'symbol_details': self.report_data['symbol_details'],
                'validation_results': self.report_data['validation_results'],
                'before_after_comparison': self.report_data['before_after_comparison'],
                'errors_encountered': self.stats['errors_encountered'],
                'recommendations': self.generate_recommendations(),
                'database_impact': {
                    'records_modified': self.stats['total_atr_records_updated'],
                    'tables_affected': ['technical_indicators'],
                    'backup_recommended': True,
                    'rollback_possible': True
                }
            }

            return report

        except Exception as e:
            self.logger.error(f"Report generation failed: {e}")
            return {'error': f"Report generation failed: {e}"}

    def generate_recommendations(self) -> List[str]:
        """Generate recommendations based on execution results"""
        recommendations = []

        success_rate = (self.stats['symbols_processed_successfully'] / max(1, self.stats['symbols_analyzed'])) * 100

        if success_rate < 95:
            recommendations.append(
                f"SUCCESS RATE WARNING: Only {success_rate:.1f}% of symbols processed successfully. Review failed symbols and error logs.")

        if self.stats['symbols_failed'] > 0:
            recommendations.append(
                f"FAILED SYMBOLS: {self.stats['symbols_failed']} symbols failed processing. Check data quality for these symbols.")

        if len(self.stats['errors_encountered']) > 0:
            recommendations.append("ERRORS DETECTED: Review error log for systematic issues that may need addressing.")

        if self.stats['total_atr_records_updated'] > 0:
            recommendations.append(
                "VERIFY CALCULATIONS: Run sample validation queries to ensure ATR values are reasonable for your symbols.")
            recommendations.append(
                "UPDATE DATA PIPELINE: Ensure your daily data updater includes ATR calculation to prevent this issue in future.")

        if self.report_data.get('validation_results', {}).get('overall_status') == 'WARNINGS':
            recommendations.append(
                "VALIDATION WARNINGS: Some ATR values may be unusual. Review symbols flagged in validation section.")

        # Always include
        recommendations.append("BACKUP VERIFICATION: Verify that database backup was taken before running this script.")
        recommendations.append(
            "SCREENER TESTING: Test your Donchian screener to ensure ATR values are now populated correctly.")

        return recommendations

    def save_report(self, report: Dict) -> str:
        """Save report to file"""
        try:
            # Save to reports directory
            reports_dir = Path("reports")
            reports_dir.mkdir(exist_ok=True)

            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            report_filename = f"atr_database_fix_report_{timestamp}.json"
            report_path = reports_dir / report_filename

            with open(report_path, 'w') as f:
                json.dump(report, f, indent=2, default=str)

            # Also save to frontend_data for dashboard access
            frontend_dir = Path("frontend_data")
            frontend_dir.mkdir(exist_ok=True)

            latest_report_path = frontend_dir / "latest_atr_fix_report.json"
            with open(latest_report_path, 'w') as f:
                json.dump(report, f, indent=2, default=str)

            self.logger.info(f"Report saved to: {report_path}")
            self.logger.info(f"Latest report saved to: {latest_report_path}")

            return str(report_path)

        except Exception as e:
            self.logger.error(f"Failed to save report: {e}")
            return ""

    def run_atr_fix(self, test_symbols: Optional[List[str]] = None, limit: Optional[int] = None) -> bool:
        """Main method to run ATR database fix"""
        try:
            self.stats['start_time'] = datetime.now()

            self.logger.info("🔧 ATR Database Fixer Starting")
            self.logger.info("=" * 60)

            # Step 1: Analyze current database status
            self.logger.info("Step 1: Analyzing database ATR status...")
            before_analysis = self.analyze_database_atr_status()

            if not before_analysis:
                self.logger.error("Failed to analyze database status")
                return False

            self.report_data['before_after_comparison']['before'] = before_analysis

            # Step 2: Get symbols to process
            if test_symbols:
                symbols_to_process = test_symbols
                self.logger.info(f"Test mode: Processing symbols {symbols_to_process}")
            else:
                symbols_needing_atr = before_analysis.get('symbols_needing_atr', [])
                symbols_to_process = [s['symbol'] for s in symbols_needing_atr]

                if limit:
                    symbols_to_process = symbols_to_process[:limit]
                    self.logger.info(f"Limited to first {limit} symbols")

            self.stats['symbols_analyzed'] = len(symbols_to_process)
            self.stats['symbols_needing_atr'] = len(symbols_to_process)

            if not symbols_to_process:
                self.logger.info("✅ No symbols need ATR calculation!")
                return True

            self.logger.info(f"Step 2: Processing {len(symbols_to_process)} symbols needing ATR calculation...")

            # Step 3: Process each symbol
            processed_symbols = []
            for i, symbol in enumerate(symbols_to_process, 1):
                self.logger.info(f"Processing {symbol} ({i}/{len(symbols_to_process)})")

                result = self.calculate_atr_for_symbol(symbol)
                self.report_data['symbol_details'].append(result)

                if result['success']:
                    self.stats['symbols_processed_successfully'] += 1
                    self.stats['total_atr_records_updated'] += result['records_updated']
                    processed_symbols.append(symbol)
                else:
                    self.stats['symbols_failed'] += 1
                    if result['error']:
                        self.stats['errors_encountered'].append(f"{symbol}: {result['error']}")

                # Progress reporting
                if i % 50 == 0:
                    progress = (i / len(symbols_to_process)) * 100
                    self.logger.info(f"Progress: {progress:.1f}% ({i}/{len(symbols_to_process)})")

            # Step 4: Validate calculations
            self.logger.info("Step 4: Validating ATR calculations...")
            validation_results = {'overall_status': 'SKIPPED'}  # Default value

            if processed_symbols:
                validation_results = self.validate_atr_calculations(processed_symbols)
                self.report_data['validation_results'] = validation_results

                if validation_results.get('overall_status') == 'PASSED':
                    self.stats['validation_passed'] = len(processed_symbols)
                else:
                    self.stats['validation_failed'] = len(processed_symbols)
            else:
                self.logger.warning("No symbols processed successfully - skipping validation")

            # Step 5: Analyze final database status
            self.logger.info("Step 5: Analyzing final database status...")
            after_analysis = self.analyze_database_atr_status()
            self.report_data['before_after_comparison']['after'] = after_analysis

            # Step 6: Generate and save comprehensive report
            self.stats['end_time'] = datetime.now()

            self.logger.info("Step 6: Generating comprehensive report...")
            final_report = self.generate_comprehensive_report()

            report_path = self.save_report(final_report)

            # Final summary
            success_rate = (self.stats['symbols_processed_successfully'] / max(1, self.stats['symbols_analyzed'])) * 100
            execution_time = self.stats['end_time'] - self.stats['start_time']

            self.logger.info("=" * 60)
            self.logger.info("🎉 ATR DATABASE FIX COMPLETED")
            self.logger.info("=" * 60)
            self.logger.info(f"Execution time: {execution_time}")
            self.logger.info(f"Symbols analyzed: {self.stats['symbols_analyzed']}")
            self.logger.info(f"Symbols processed successfully: {self.stats['symbols_processed_successfully']}")
            self.logger.info(f"Symbols failed: {self.stats['symbols_failed']}")
            self.logger.info(f"Success rate: {success_rate:.1f}%")
            self.logger.info(f"ATR records updated: {self.stats['total_atr_records_updated']:,}")
            self.logger.info(f"Validation status: {validation_results.get('overall_status', 'N/A')}")
            self.logger.info(f"Report saved: {report_path}")
            self.logger.info("=" * 60)

            return success_rate > 90  # Consider successful if >90% symbols processed

        except Exception as e:
            self.logger.error(f"ATR fix operation failed: {e}")
            self.stats['errors_encountered'].append(f"Main operation failed: {e}")
            return False


def main():
    """Main function"""
    import argparse

    parser = argparse.ArgumentParser(description='ATR Database Fixer - Calculate and populate missing ATR values')
    parser.add_argument('--test', nargs='+', help='Test with specific symbols')
    parser.add_argument('--limit', type=int, help='Limit number of symbols to process')
    parser.add_argument('--dry-run', action='store_true', help='Analyze only, do not modify database')

    args = parser.parse_args()

    print("🔧 ATR Database Fixer")
    print("=" * 50)
    print("This script will calculate and populate missing ATR values in your technical_indicators table.")
    print("A comprehensive report will be generated showing all changes made.")
    print()

    if not args.dry_run:
        confirmation = input("Do you want to proceed with modifying the database? (yes/no): ")
        if confirmation.lower() != 'yes':
            print("Operation cancelled.")
            return

    # Initialize and run fixer
    fixer = ATRDatabaseFixer()

    if args.dry_run:
        print("DRY RUN MODE: Analyzing database only, no changes will be made.")
        # Just run analysis
        analysis = fixer.analyze_database_atr_status()
        print(f"Symbols needing ATR: {len(analysis.get('symbols_needing_atr', []))}")
        return

    success = fixer.run_atr_fix(test_symbols=args.test, limit=args.limit)

    if success:
        print("✅ ATR database fix completed successfully!")
        print("📊 Check the generated report for detailed results.")
    else:
        print("❌ ATR database fix encountered issues.")
        print("📋 Check the logs and generated report for details.")


if __name__ == "__main__":
    main()