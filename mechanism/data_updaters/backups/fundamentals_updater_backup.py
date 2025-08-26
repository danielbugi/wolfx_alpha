# automation/data_updaters/fundamentals_updater.py
"""
Refactored fundamentals updater using shared infrastructure
Updates company fundamental data while preserving quality scores
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

sys.path.append(os.path.dirname(os.path.dirname(__file__)))

from shared import (
    config, db, setup_logging, retry_on_failure,
    timing_decorator, data_validation, format_number
)

warnings.filterwarnings('ignore')

# Setup logging for this module
logger = setup_logging("fundamentals_updater")


class FundamentalsUpdater:
    """Enhanced fundamentals updater using shared infrastructure"""

    def __init__(self):
        """Initialize the updater with shared configuration"""
        self.batch_size = config.data_batch_size
        self.rate_limit_delay = config.rate_limit_delay * 2  # Slower for fundamentals

        logger.info("Fundamentals Updater initialized with shared infrastructure")

    @retry_on_failure(max_retries=3, delay=1.0)
    def get_symbols_to_update(self, limit: Optional[int] = None) -> List[str]:
        """Get symbols that need fundamentals updates"""
        try:
            query = """
            SELECT DISTINCT symbol 
            FROM daily_fundamentals 
            WHERE symbol IS NOT NULL 
            AND symbol != ''
            ORDER BY symbol
            """

            if limit:
                query += f" LIMIT {limit}"

            results = db.execute_query(query)
            symbols = [row[0] for row in results]

            logger.info(f"Found {len(symbols)} symbols needing fundamentals update")
            return symbols

        except Exception as e:
            logger.error(f"Error getting symbols: {e}")
            return []

    @timing_decorator()
    @retry_on_failure(max_retries=3, delay=2.0, exceptions=(Exception,))
    def fetch_company_info(self, symbol: str) -> Optional[Dict[str, Any]]:
        """Fetch company information from Yahoo Finance"""
        try:
            logger.debug(f"Fetching company info for {symbol}")

            ticker = yf.Ticker(symbol)
            info = ticker.info

            if not info or 'symbol' not in info:
                logger.warning(f"No company info returned for {symbol}")
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

            # Validate fundamental data
            validation = data_validation.validate_fundamentals(company_info)
            if not validation['valid']:
                logger.warning(f"Fundamentals validation failed for {symbol}: {validation['errors']}")

            if validation['warnings']:
                logger.debug(f"Fundamentals warnings for {symbol}: {validation['warnings']}")

            logger.debug(f"Successfully fetched company info for {symbol}")
            return company_info

        except Exception as e:
            logger.error(f"Error fetching company info for {symbol}: {e}")
            raise

    def calculate_quality_score(self, fundamentals: Dict[str, Any]) -> float:
        """Calculate enhanced quality score based on available metrics"""
        try:
            score = 50.0  # Base score

            # P/E ratio scoring
            pe_ratio = fundamentals.get('pe_ratio')
            if pe_ratio and pe_ratio > 0:
                if 5 <= pe_ratio <= 25:  # Reasonable P/E range
                    score += 15
                elif pe_ratio <= 35:  # Acceptable range
                    score += 10
                elif pe_ratio > 0:  # At least positive
                    score += 5

            # P/B ratio scoring
            pb_ratio = fundamentals.get('pb_ratio')
            if pb_ratio and pb_ratio > 0:
                if 0.5 <= pb_ratio <= 3:  # Reasonable P/B range
                    score += 10
                elif pb_ratio <= 5:  # Acceptable range
                    score += 7
                elif pb_ratio > 0:
                    score += 3

            # Beta scoring (stability)
            beta = fundamentals.get('beta')
            if beta and beta > 0:
                if 0.5 <= beta <= 1.5:  # Moderate volatility
                    score += 10
                elif 0.3 <= beta <= 2.0:  # Acceptable volatility
                    score += 7
                else:
                    score += 3

            # Market cap scoring (size/stability)
            market_cap = fundamentals.get('market_cap')
            if market_cap and market_cap > 0:
                if market_cap >= 10e9:  # Large cap (10B+)
                    score += 10
                elif market_cap >= 2e9:  # Mid cap (2B+)
                    score += 7
                elif market_cap >= 300e6:  # Small cap (300M+)
                    score += 5
                else:  # Micro cap
                    score += 2

            # Dividend yield scoring
            dividend_yield = fundamentals.get('dividend_yield')
            if dividend_yield and dividend_yield > 0:
                if 0.01 <= dividend_yield <= 0.06:  # 1-6% dividend yield
                    score += 5
                elif dividend_yield <= 0.1:  # Up to 10%
                    score += 3

            # Sector scoring (some sectors are more stable)
            sector = fundamentals.get('sector')
            stable_sectors = [
                'Utilities', 'Consumer Staples', 'Healthcare',
                'Industrials', 'Technology'
            ]
            if sector in stable_sectors:
                score += 5

            return min(score, 100.0)

        except Exception as e:
            logger.error(f"Error calculating quality score: {e}")
            return 50.0  # Default score

    def calculate_quality_grade(self, score: float) -> str:
        """Convert quality score to letter grade"""
        if score >= 90:
            return 'A'
        elif score >= 80:
            return 'B'
        elif score >= 70:
            return 'C'
        elif score >= 60:
            return 'D'
        else:
            return 'F'

    @retry_on_failure(max_retries=2, delay=0.5)
    def get_existing_quality_data(self, symbol: str) -> Optional[Dict[str, Any]]:
        """Get existing quality data to preserve during updates"""
        try:
            query = """
                SELECT overall_quality_score, quality_grade, growth_score, 
                       profitability_score, financial_health_score, valuation_score
                FROM daily_fundamentals 
                WHERE symbol = %s
                ORDER BY date DESC
                LIMIT 1
            """

            results = db.execute_dict_query(query, (symbol,))

            if results:
                return results[0]
            return None

        except Exception as e:
            logger.error(f"Error getting existing quality data for {symbol}: {e}")
            return None

    @timing_decorator()
    def update_daily_fundamentals(self, fundamentals: Dict[str, Any], date: str) -> bool:
        """Update daily fundamentals while preserving existing quality scores"""
        try:
            symbol = fundamentals['symbol']

            # Get existing quality data
            existing_quality = self.get_existing_quality_data(symbol)

            # Use existing quality scores if available, otherwise calculate new ones
            if existing_quality and existing_quality.get('overall_quality_score'):
                quality_score = existing_quality['overall_quality_score']
                quality_grade = existing_quality['quality_grade']
                growth_score = existing_quality.get('growth_score')
                profitability_score = existing_quality.get('profitability_score')
                financial_health_score = existing_quality.get('financial_health_score')
                valuation_score = existing_quality.get('valuation_score')

                logger.debug(f"Preserving existing quality score {quality_score} for {symbol}")
            else:
                # Calculate new quality score
                quality_score = self.calculate_quality_score(fundamentals)
                quality_grade = self.calculate_quality_grade(quality_score)
                growth_score = None
                profitability_score = None
                financial_health_score = None
                valuation_score = None

                logger.debug(f"Calculated new quality score {quality_score} for {symbol}")

            # Prepare update query
            query = """
                INSERT INTO daily_fundamentals (
                    symbol, date, market_cap, shares_outstanding, float_shares,
                    pe_ratio, pb_ratio, ps_ratio, peg_ratio, beta, dividend_yield,
                    sector, industry, overall_quality_score, quality_grade,
                    growth_score, profitability_score, financial_health_score, valuation_score,
                    created_at, updated_at
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
                    overall_quality_score = CASE 
                        WHEN daily_fundamentals.overall_quality_score IS NOT NULL 
                        THEN daily_fundamentals.overall_quality_score 
                        ELSE EXCLUDED.overall_quality_score 
                    END,
                    quality_grade = CASE 
                        WHEN daily_fundamentals.quality_grade IS NOT NULL 
                        THEN daily_fundamentals.quality_grade 
                        ELSE EXCLUDED.quality_grade 
                    END,
                    updated_at = EXCLUDED.updated_at
            """

            # Prepare data tuple with cleaned values
            data = (
                symbol,
                date,
                data_validation.clean_numeric_data(fundamentals.get('market_cap')),
                data_validation.clean_numeric_data(fundamentals.get('shares_outstanding')),
                data_validation.clean_numeric_data(fundamentals.get('float_shares')),
                data_validation.clean_numeric_data(fundamentals.get('pe_ratio')),
                data_validation.clean_numeric_data(fundamentals.get('pb_ratio')),
                data_validation.clean_numeric_data(fundamentals.get('ps_ratio')),
                data_validation.clean_numeric_data(fundamentals.get('peg_ratio')),
                data_validation.clean_numeric_data(fundamentals.get('beta')),
                data_validation.clean_numeric_data(fundamentals.get('dividend_yield')),
                fundamentals.get('sector'),
                fundamentals.get('industry'),
                quality_score,
                quality_grade,
                growth_score,
                profitability_score,
                financial_health_score,
                valuation_score,
                datetime.now(),  # created_at
                datetime.now()  # updated_at
            )

            success = db.execute_insert(query, data)

            if success:
                logger.debug(f"Successfully updated fundamentals for {symbol} (Quality Score: {quality_score})")
            else:
                logger.error(f"Failed to update fundamentals for {symbol}")

            return success

        except Exception as e:
            logger.error(f"Error updating fundamentals for {fundamentals.get('symbol', 'unknown')}: {e}")
            return False

    @timing_decorator()
    def update_symbol(self, symbol: str) -> bool:
        """Update fundamentals for a single symbol"""
        try:
            # Validate symbol
            if not data_validation.validate_symbol(symbol):
                logger.warning(f"Invalid symbol format: {symbol}")
                return False

            logger.info(f"Starting fundamentals update for {symbol}")

            # Fetch company info
            company_info = self.fetch_company_info(symbol)
            if company_info is None:
                logger.warning(f"No company info available for {symbol}")
                return False

            # Update for today's date
            today = datetime.now().date()

            # Update daily fundamentals
            if not self.update_daily_fundamentals(company_info, today):
                logger.error(f"Failed to update daily fundamentals for {symbol}")
                return False

            logger.info(f"✅ Successfully completed fundamentals update for {symbol}")
            return True

        except Exception as e:
            logger.error(f"Error updating fundamentals for {symbol}: {e}")
            return False

    @timing_decorator()
    def run_fundamentals_update(self, symbols: Optional[List[str]] = None, limit: Optional[int] = None):
        """Run fundamentals update for specified symbols or all symbols"""
        start_time = datetime.now()
        logger.info(f"🚀 Starting fundamentals update at {start_time}")

        # Get symbols to update
        if symbols is None:
            symbols = self.get_symbols_to_update(limit)

        if not symbols:
            logger.warning("No symbols to update")
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
            logger.info(f"Processing {symbol} ({i}/{len(symbols)})")

            try:
                if self.update_symbol(symbol):
                    successful_updates += 1
                else:
                    failed_updates += 1

                # Longer delay for fundamentals to avoid rate limiting
                if i < len(symbols):
                    time.sleep(self.rate_limit_delay)

            except Exception as e:
                logger.error(f"Unexpected error processing {symbol}: {e}")
                failed_updates += 1
                continue

        end_time = datetime.now()
        duration = end_time - start_time

        # Log results
        success_rate = (successful_updates / len(symbols) * 100) if symbols else 0
        logger.info(f"""
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

    parser = argparse.ArgumentParser(description='Enhanced Fundamentals Updater')
    parser.add_argument('--limit', type=int, help='Limit number of symbols to update')
    parser.add_argument('--test', nargs='+', help='Test with specific symbols')
    parser.add_argument('--batch', type=int, help='Run in batch mode with specified limit')
    parser.add_argument('--config-test', action='store_true', help='Test configuration and database connection')

    args = parser.parse_args()

    if args.config_test:
        # Test configuration and database
        print("🔧 Testing configuration and database connection...")

        validation = config.validate_config()
        print(f"Configuration validation: {validation}")

        db_status = db.test_connection()
        if db_status['connected']:
            print(f"✅ Database connection successful")
            print(f"   Fundamentals records: {format_number(db_status['stats']['daily_fundamentals'])}")
        else:
            print(f"❌ Database connection failed: {db_status['error']}")

        return

    # Initialize updater
    updater = FundamentalsUpdater()

    if args.test:
        # Test mode with specific symbols
        logger.info(f"🧪 Running test update with symbols: {args.test}")
        result = updater.run_fundamentals_update(symbols=args.test)

    elif args.batch:
        # Batch mode with limit
        logger.info(f"📦 Running batch update with limit: {args.batch}")
        result = updater.run_fundamentals_update(limit=args.batch)

    else:
        # Full update
        logger.info("🚀 Running fundamentals update")
        result = updater.run_fundamentals_update(limit=args.limit)

    # Log final result
    if result['success']:
        logger.info("🎉 Fundamentals update completed successfully!")
    else:
        logger.warning("⚠️ Fundamentals update completed with some failures")


if __name__ == "__main__":
    main()