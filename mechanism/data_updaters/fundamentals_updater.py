#!/usr/bin/env python3
"""
Fixed Fundamentals Updater with proper quality score calculation
Updates company fundamental data with consistent quality scoring
"""

import yfinance as yf
import pandas as pd
import numpy as np
import time
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple, Any
import warnings

# Import shared infrastructure
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

try:
    from shared import (
        config, db, setup_logging, retry_on_failure,
        performance_monitor, file_utils
    )
except ImportError as e:
    print(f"Import error: {e}")
    sys.exit(1)

warnings.filterwarnings('ignore')


class FundamentalsUpdater:
    """Enhanced fundamentals updater with proper quality score calculation"""

    def __init__(self):
        """Initialize the updater with shared configuration"""
        self.logger = setup_logging(__name__)

        # Configuration with defaults
        self.batch_size = getattr(config, 'DATA_UPDATE_BATCH_SIZE', 1000)
        self.rate_limit_delay = 2.0  # Slower for fundamentals

        self.logger.info("Fundamentals Updater initialized with shared infrastructure")

    def safe_float(self, value):
        """Safely convert to float"""
        try:
            return float(value) if value is not None else None
        except (ValueError, TypeError):
            return None

    def calculate_growth_score(self, fundamentals: Dict[str, Any]) -> float:
        """Calculate growth score (0-25) - consistent with quality_score_generator"""
        score = 0

        # Market cap indicates growth potential
        market_cap = self.safe_float(fundamentals.get('market_cap'))
        if market_cap:
            score += 10  # Base score for having market cap data

        # PE ratio scoring (lower PE can indicate growth potential)
        pe_ratio = self.safe_float(fundamentals.get('pe_ratio'))
        if pe_ratio:
            if 10 <= pe_ratio <= 25:  # Reasonable growth PE
                score += 10
            elif 5 <= pe_ratio <= 35:  # Acceptable range
                score += 7
            elif pe_ratio > 0:  # Any positive PE
                score += 5

        # PEG ratio scoring (if available)
        peg_ratio = self.safe_float(fundamentals.get('peg_ratio'))
        if peg_ratio:
            if 0.5 <= peg_ratio <= 1.5:  # Good PEG
                score += 5
            elif 0 < peg_ratio <= 2.0:  # Acceptable PEG
                score += 3

        return min(score, 25)

    def calculate_profitability_score(self, fundamentals: Dict[str, Any]) -> float:
        """Calculate profitability score (0-25)"""
        score = 0

        # PE ratio (indicates profitability)
        pe_ratio = self.safe_float(fundamentals.get('pe_ratio'))
        if pe_ratio:
            if 5 <= pe_ratio <= 30:  # Profitable and reasonable
                score += 15
            elif pe_ratio > 0:  # At least profitable
                score += 10
        else:
            # No PE might mean not profitable
            score += 2

        # Dividend yield (indicates cash generation)
        dividend_yield = self.safe_float(fundamentals.get('dividend_yield'))
        if dividend_yield:
            if dividend_yield >= 0.02:  # Good dividend (2%+)
                score += 10
            elif dividend_yield >= 0.01:  # Decent dividend (1%+)
                score += 7
            elif dividend_yield > 0:  # Some dividend
                score += 5
        else:
            # Growth companies might not pay dividends
            score += 3

        return min(score, 25)

    def calculate_financial_health_score(self, fundamentals: Dict[str, Any]) -> float:
        """Calculate financial health score (0-25)"""
        score = 0

        # Market cap (stability indicator)
        market_cap = self.safe_float(fundamentals.get('market_cap'))
        if market_cap:
            if market_cap >= 10_000_000_000:  # Large cap (10B+)
                score += 15
            elif market_cap >= 2_000_000_000:  # Mid cap (2B+)
                score += 12
            elif market_cap >= 300_000_000:  # Small cap (300M+)
                score += 8
            else:  # Micro cap
                score += 5

        # Price to Book ratio (asset backing)
        pb_ratio = self.safe_float(fundamentals.get('pb_ratio'))
        if pb_ratio:
            if 0.5 <= pb_ratio <= 3.0:  # Reasonable book value
                score += 10
            elif 0 < pb_ratio <= 5.0:  # Acceptable range
                score += 7
            elif pb_ratio > 0:
                score += 3

        return min(score, 25)

    def calculate_valuation_score(self, fundamentals: Dict[str, Any]) -> float:
        """Calculate valuation score (0-25)"""
        score = 0

        # PE ratio (valuation metric)
        pe_ratio = self.safe_float(fundamentals.get('pe_ratio'))
        if pe_ratio:
            if 8 <= pe_ratio <= 20:  # Attractive valuation
                score += 10
            elif 5 <= pe_ratio <= 35:  # Reasonable valuation
                score += 7
            elif pe_ratio > 0:  # Any positive PE
                score += 5

        # Price to Sales ratio
        ps_ratio = self.safe_float(fundamentals.get('ps_ratio'))
        if ps_ratio:
            if 0.5 <= ps_ratio <= 3.0:  # Good PS ratio
                score += 8
            elif 0 < ps_ratio <= 10.0:  # Acceptable PS
                score += 5
            elif ps_ratio > 0:
                score += 3

        # Price to Book ratio
        pb_ratio = self.safe_float(fundamentals.get('pb_ratio'))
        if pb_ratio:
            if 0.8 <= pb_ratio <= 2.5:  # Attractive PB
                score += 7
            elif 0 < pb_ratio <= 5.0:  # Reasonable PB
                score += 4
            elif pb_ratio > 0:
                score += 2

        return min(score, 25)

    def calculate_overall_quality_score(self, growth, profitability, financial_health, valuation):
        """Calculate overall quality score (0-100)"""
        return growth + profitability + financial_health + valuation

    def assign_quality_grade(self, overall_score):
        """Assign letter grade based on overall score"""
        if overall_score >= 85:
            return 'A'
        elif overall_score >= 75:
            return 'B'
        elif overall_score >= 65:
            return 'C'
        elif overall_score >= 50:
            return 'D'
        else:
            return 'F'

    @retry_on_failure(max_retries=3, delay=1.0)
    def get_symbols_to_update(self, limit: Optional[int] = None) -> List[str]:
        """Get symbols that need fundamentals updates"""
        try:
            query = """
            SELECT DISTINCT symbol 
            FROM stock_prices 
            WHERE symbol IS NOT NULL 
            AND symbol != ''
            ORDER BY symbol
            """

            if limit:
                query += f" LIMIT {limit}"

            results = db.execute_dict_query(query)
            symbols = [row['symbol'] for row in results]

            self.logger.info(f"Found {len(symbols)} symbols needing fundamentals update")
            return symbols

        except Exception as e:
            self.logger.error(f"Error getting symbols: {e}")
            return []

    @retry_on_failure(max_retries=3, delay=2.0)
    def fetch_company_info(self, symbol: str) -> Optional[Dict[str, Any]]:
        """Fetch company information from Yahoo Finance"""
        try:
            self.logger.debug(f"Fetching company info for {symbol}")

            ticker = yf.Ticker(symbol)
            info = ticker.info

            if not info or len(info) < 5:  # Basic check for valid data
                self.logger.warning(f"No company info returned for {symbol}")
                return None

            # Extract key company information matching schema
            company_info = {
                'symbol': symbol,
                'market_cap': info.get('marketCap'),
                'shares_outstanding': info.get('sharesOutstanding'),
                'float_shares': info.get('floatShares'),
                'pe_ratio': info.get('trailingPE'),
                'pb_ratio': info.get('priceToBook'),
                'ps_ratio': info.get('priceToSalesTrailing12Months'),
                'peg_ratio': info.get('pegRatio'),
                'beta': info.get('beta'),
                'dividend_yield': info.get('dividendYield'),
                'sector': info.get('sector'),
                'industry': info.get('industry'),
                'last_updated': datetime.now()
            }

            self.logger.debug(f"Successfully fetched company info for {symbol}")
            return company_info

        except Exception as e:
            self.logger.error(f"Error fetching company info for {symbol}: {e}")
            raise

    @retry_on_failure(max_retries=2, delay=0.5)
    def get_existing_quality_data(self, symbol: str) -> Optional[Dict[str, Any]]:
        """Get existing quality data to preserve during updates"""
        try:
            query = """
                SELECT overall_quality_score, quality_grade, growth_score, 
                       profitability_score, financial_health_score, valuation_score
                FROM daily_fundamentals 
                WHERE symbol = %s
                AND overall_quality_score IS NOT NULL
                ORDER BY date DESC
                LIMIT 1
            """

            results = db.execute_dict_query(query, (symbol,))

            if results:
                return results[0]
            return None

        except Exception as e:
            self.logger.error(f"Error getting existing quality data for {symbol}: {e}")
            return None

    def clean_numeric_data(self, value):
        """Clean numeric data for database insertion"""
        if value is None:
            return None
        try:
            # Convert to float first
            float_val = float(value)
            # Check for invalid values
            if np.isnan(float_val) or np.isinf(float_val):
                return None
            return float_val
        except (ValueError, TypeError):
            return None

    def update_daily_fundamentals(self, fundamentals: Dict[str, Any], date: str) -> bool:
        """Update daily fundamentals with proper quality score calculation"""
        try:
            symbol = fundamentals['symbol']

            # Get existing quality data
            existing_quality = self.get_existing_quality_data(symbol)

            # Calculate quality scores (always calculate to ensure consistency)
            growth_score = self.calculate_growth_score(fundamentals)
            profitability_score = self.calculate_profitability_score(fundamentals)
            financial_health_score = self.calculate_financial_health_score(fundamentals)
            valuation_score = self.calculate_valuation_score(fundamentals)
            overall_score = self.calculate_overall_quality_score(
                growth_score, profitability_score, financial_health_score, valuation_score
            )
            quality_grade = self.assign_quality_grade(overall_score)

            # Always use newly calculated scores
            self.logger.debug(f"Calculated quality score for {symbol}: {overall_score} ({quality_grade})")
            final_growth = growth_score
            final_profitability = profitability_score
            final_financial_health = financial_health_score
            final_valuation = valuation_score
            final_overall = overall_score
            final_grade = quality_grade

            # Prepare update query
            query = """
                INSERT INTO daily_fundamentals (
                    symbol, date, market_cap, shares_outstanding, float_shares,
                    pe_ratio, pb_ratio, ps_ratio, peg_ratio, beta, dividend_yield,
                    sector, industry, growth_score, profitability_score, 
                    financial_health_score, valuation_score, overall_quality_score, 
                    quality_grade, created_at, updated_at
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (symbol, date) 
                DO UPDATE SET
                    market_cap = EXCLUDED.market_cap,
                    shares_outstanding = EXCLUDED.shares_outstanding,
                    float_shares = EXCLUDED.float_shares,
                    pe_ratio = EXCLUDED.pe_ratio,
                    pb_ratio = EXCLUDED.pb_ratio,
                    ps_ratio = EXCLUDED.ps_ratio,
                    peg_ratio = EXCLUDED.peg_ratio,
                    beta = EXCLUDED.beta,
                    dividend_yield = EXCLUDED.dividend_yield,
                    sector = EXCLUDED.sector,
                    industry = EXCLUDED.industry,
                    growth_score = EXCLUDED.growth_score,
                    profitability_score = EXCLUDED.profitability_score,
                    financial_health_score = EXCLUDED.financial_health_score,
                    valuation_score = EXCLUDED.valuation_score,
                    overall_quality_score = EXCLUDED.overall_quality_score,
                    quality_grade = EXCLUDED.quality_grade,
                    updated_at = EXCLUDED.updated_at
            """

            # Prepare data tuple with cleaned values
            data = (
                symbol,
                date,
                self.clean_numeric_data(fundamentals.get('market_cap')),
                self.clean_numeric_data(fundamentals.get('shares_outstanding')),
                self.clean_numeric_data(fundamentals.get('float_shares')),
                self.clean_numeric_data(fundamentals.get('pe_ratio')),
                self.clean_numeric_data(fundamentals.get('pb_ratio')),
                self.clean_numeric_data(fundamentals.get('ps_ratio')),
                self.clean_numeric_data(fundamentals.get('peg_ratio')),
                self.clean_numeric_data(fundamentals.get('beta')),
                self.clean_numeric_data(fundamentals.get('dividend_yield')),
                fundamentals.get('sector'),
                fundamentals.get('industry'),
                final_growth,
                final_profitability,
                final_financial_health,
                final_valuation,
                final_overall,
                final_grade,
                datetime.now(),  # created_at
                datetime.now()  # updated_at
            )

            self.logger.debug(
                f"{symbol} | G:{growth_score} P:{profitability_score} F:{financial_health_score} "
                f"V:{valuation_score} Q:{overall_score} Grade:{quality_grade}"
            )

            # Execute the insert/update
            try:
                db.execute_insert(query, data)
                self.logger.info(f"Successfully updated fundamentals for {symbol} (Quality Score: {final_overall})")
                return True
            except Exception as e:
                self.logger.error(f"Database insert failed for {symbol}: {e}")
                return False

        except Exception as e:
            self.logger.error(f"Error updating fundamentals for {fundamentals.get('symbol', 'unknown')}: {e}")
            return False

    def update_symbol(self, symbol: str) -> bool:
        """Update fundamentals for a single symbol"""
        try:
            self.logger.info(f"Starting fundamentals update for {symbol}")

            # Fetch company info
            company_info = self.fetch_company_info(symbol)
            if company_info is None:
                self.logger.warning(f"No company info available for {symbol}")
                return False

            # Update for today's date
            today = datetime.now().date()

            # Update daily fundamentals
            if not self.update_daily_fundamentals(company_info, today):
                self.logger.error(f"Failed to update daily fundamentals for {symbol}")
                return False

            self.logger.info(f"✅ Successfully completed fundamentals update for {symbol}")
            return True

        except Exception as e:
            self.logger.error(f"Error updating fundamentals for {symbol}: {e}")
            return False

    def run_fundamentals_update(self, symbols: Optional[List[str]] = None, limit: Optional[int] = None):
        """Run fundamentals update for specified symbols or all symbols"""
        start_time = datetime.now()
        self.logger.info(f"🚀 Starting fundamentals update at {start_time}")

        # Get symbols to update
        if symbols is None:
            symbols = self.get_symbols_to_update(limit)

        if not symbols:
            self.logger.warning("No symbols to update")
            return {
                'success': False,
                'message': 'No symbols found',
                'symbols_processed': 0,
                'symbols_successful': 0,
                'symbols_failed': 0
            }

        successful_updates = 0
        failed_updates = 0

        # Process symbols with longer delays (fundamentals are less time-sensitive)
        for i, symbol in enumerate(symbols, 1):
            self.logger.info(f"Processing {symbol} ({i}/{len(symbols)})")

            try:
                if self.update_symbol(symbol):
                    successful_updates += 1
                else:
                    failed_updates += 1

                # Longer delay for fundamentals to avoid rate limiting
                if i < len(symbols):
                    time.sleep(self.rate_limit_delay)

            except Exception as e:
                self.logger.error(f"Unexpected error processing {symbol}: {e}")
                failed_updates += 1
                continue

        end_time = datetime.now()
        duration = end_time - start_time

        # Log results
        success_rate = (successful_updates / len(symbols) * 100) if symbols else 0
        self.logger.info(f"""
        🎉 Fundamentals update completed in {duration}:
        ✅ Successful updates: {successful_updates}
        ❌ Failed updates: {failed_updates}
        📊 Total symbols: {len(symbols)}
        📈 Success rate: {success_rate:.1f}%
        """)

        return {
            'success': failed_updates == 0,
            'duration': duration.total_seconds(),
            'symbols_processed': len(symbols),
            'symbols_successful': successful_updates,
            'symbols_failed': failed_updates,
            'success_rate': success_rate
        }


def main():
    """Main function to run fundamentals updates"""
    import argparse

    parser = argparse.ArgumentParser(description='Fixed Fundamentals Updater')
    parser.add_argument('--limit', type=int, help='Limit number of symbols to update')
    parser.add_argument('--test', nargs='+', help='Test with specific symbols')
    parser.add_argument('--batch', type=int, help='Run in batch mode with specified limit')
    parser.add_argument('--config-test', action='store_true', help='Test configuration and database connection')

    args = parser.parse_args()

    if args.config_test:
        # Test configuration and database
        print("🔧 Testing configuration and database connection...")

        try:
            validation = config.validate_config()
            print(f"Configuration validation: {validation}")
        except Exception as e:
            print(f"Configuration error: {e}")

        try:
            health = db.get_health_status()
            if health.get('connected'):
                print(f"✅ Database connection successful")
                print(f"   Database: {health.get('database')}")
                if 'record_counts' in health:
                    fundamentals_count = health['record_counts'].get('daily_fundamentals', 0)
                    print(f"   Fundamentals records: {fundamentals_count:,}")
            else:
                print(f"❌ Database connection failed: {health.get('error')}")
        except Exception as e:
            print(f"Database test error: {e}")

        return

    # Setup logging
    setup_logging(level='INFO')

    # Initialize updater
    updater = FundamentalsUpdater()

    if args.test:
        # Test mode with specific symbols
        updater.logger.info(f"🧪 Running test update with symbols: {args.test}")
        result = updater.run_fundamentals_update(symbols=args.test)

    elif args.batch:
        # Batch mode with limit
        updater.logger.info(f"📦 Running batch update with limit: {args.batch}")
        result = updater.run_fundamentals_update(limit=args.batch)

    else:
        # Full update
        updater.logger.info("🚀 Running fundamentals update")
        result = updater.run_fundamentals_update(limit=args.limit)

    # Log final result
    if result['success']:
        print("🎉 Fundamentals update completed successfully!")
    else:
        print("⚠️ Fundamentals update completed with some failures")


if __name__ == "__main__":
    main()