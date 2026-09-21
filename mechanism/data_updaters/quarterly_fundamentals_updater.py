#!/usr/bin/env python3
"""
Quarterly Fundamentals Updater

Backfills/refreshes the quarterly_fundamentals table with an actual time series
of trailing quarters per symbol (income statement, balance sheet, cash flow),
so quarter-over-quarter changes -- e.g. a net loss flipping to a net profit --
can be detected. Previously nothing wrote to this table on an ongoing basis:
its ~1,000 existing rows were a one-time migration snapshot (one row per
symbol, same quarter date, no fiscal_year/fiscal_quarter), not a real series.
"""

import yfinance as yf
import pandas as pd
import numpy as np
import time
from datetime import datetime
from typing import Dict, List, Optional, Any
import warnings
import concurrent.futures

STATEMENT_TIMEOUT_SECONDS = 20

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

try:
    from shared import (
        config, db, setup_logging, retry_on_failure,
        performance_monitor, file_utils, ProgressBar
    )
except ImportError as e:
    print(f"Import error: {e}")
    sys.exit(1)

warnings.filterwarnings('ignore')


class QuarterlyFundamentalsUpdater:
    """Backfills/refreshes trailing-quarter fundamentals per symbol"""

    def __init__(self):
        self.logger = setup_logging(__name__)
        # 3 yfinance calls/symbol here vs. 1 in fundamentals_updater.py --
        # more conservative delay given the documented yfinance rate-limit
        # outage history (see CLAUDE.md / MILESTONES.md).
        self.rate_limit_delay = 3.0
        self.logger.info("Quarterly Fundamentals Updater initialized with shared infrastructure")

    @staticmethod
    def _safe_float(value) -> Optional[float]:
        try:
            if value is None:
                return None
            f = float(value)
            if np.isnan(f) or np.isinf(f):
                return None
            return f
        except (ValueError, TypeError):
            return None

    @staticmethod
    def _clamp_ratio(value: Optional[float], bound: float = 9999.0) -> Optional[float]:
        """
        quarterly_fundamentals' margin/ratio columns are DECIMAL(6,2) (max
        magnitude 9999.99). Companies with near-zero or negative equity/assets
        (e.g. heavily leveraged or freshly-unprofitable names) can produce
        ratios in the thousands or millions of percent -- null those out
        instead of letting the whole row's INSERT fail on overflow.
        """
        if value is None:
            return None
        return value if abs(value) <= bound else None

    def _get(self, df: Optional[pd.DataFrame], key: str, col) -> Optional[float]:
        """Safely read one cell from a yfinance statement DataFrame"""
        if df is None or df.empty or key not in df.index or col not in df.columns:
            return None
        return self._safe_float(df.loc[key, col])

    def _with_timeout(self, fn, timeout: int, label: str):
        """
        Hard wall-clock timeout via a worker thread. yfinance's statement
        properties have no timeout of their own -- same rationale as
        fetch_company_info() in fundamentals_updater.py (a stalled network
        call there once hung for 7.6 hours). shutdown(wait=False) so a
        stuck call costs at most `timeout` seconds, not the rest of the run.
        """
        executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
        future = executor.submit(fn)
        try:
            return future.result(timeout=timeout)
        except concurrent.futures.TimeoutError:
            self.logger.error(f"Timed out after {timeout}s fetching {label}")
            return None
        finally:
            executor.shutdown(wait=False)

    def _fetch_yfinance_quarters(self, symbol: str) -> List[Dict[str, Any]]:
        """Fetch trailing quarterly statement line items from yfinance --
        raw fields only, no margins/ratios (see _compute_ratios, applied
        uniformly to whichever provider supplied the raw rows)."""
        ticker = yf.Ticker(symbol)

        income = self._with_timeout(lambda: ticker.quarterly_income_stmt, STATEMENT_TIMEOUT_SECONDS, f"{symbol} income stmt")
        balance = self._with_timeout(lambda: ticker.quarterly_balance_sheet, STATEMENT_TIMEOUT_SECONDS, f"{symbol} balance sheet")
        cashflow = self._with_timeout(lambda: ticker.quarterly_cashflow, STATEMENT_TIMEOUT_SECONDS, f"{symbol} cashflow")

        if income is None or income.empty:
            self.logger.warning(f"No quarterly income statement for {symbol}")
            return []

        # Quarters present in the income statement, newest first (yfinance's
        # own column order) -- used as the canonical list of periods.
        quarters = list(income.columns)

        raw_rows = []
        for col in quarters:
            revenue = self._get(income, 'Total Revenue', col)
            net_income = self._get(income, 'Net Income', col)

            if revenue is None and net_income is None:
                # Nothing usable for this period -- skip rather than insert an
                # all-null row.
                continue

            quarter_date = pd.Timestamp(col).date()
            # Calendar-quarter approximation from the period-end date -- not
            # necessarily each company's own fiscal quarter numbering, but
            # `quarter` (the actual date) is what's used for ordering/dedup.
            # (Tiingo's statements, by contrast, give explicit year/quarter
            # integers -- see tiingo_client.get_quarterly_statements.)
            fiscal_year = quarter_date.year
            fiscal_quarter = (quarter_date.month - 1) // 3 + 1

            raw_rows.append({
                'symbol': symbol,
                'quarter': quarter_date,
                'fiscal_year': fiscal_year,
                'fiscal_quarter': fiscal_quarter,
                'revenue': revenue,
                'gross_profit': self._get(income, 'Gross Profit', col),
                'operating_income': self._get(income, 'Operating Income', col),
                'net_income': net_income,
                'ebitda': self._get(income, 'EBITDA', col),
                'eps': self._get(income, 'Diluted EPS', col),
                'total_assets': self._get(balance, 'Total Assets', col),
                'total_debt': self._get(balance, 'Total Debt', col),
                'total_equity': self._get(balance, 'Stockholders Equity', col),
                'current_assets': self._get(balance, 'Current Assets', col),
                'current_liabilities': self._get(balance, 'Current Liabilities', col),
                'cash': self._get(balance, 'Cash And Cash Equivalents', col),
                'operating_cash_flow': self._get(cashflow, 'Operating Cash Flow', col),
                'free_cash_flow': self._get(cashflow, 'Free Cash Flow', col),
                'capital_expenditures': self._get(cashflow, 'Capital Expenditure', col),
                'piotroski_f_score': None,  # yfinance doesn't provide this -- Tiingo-only
            })

        return raw_rows

    def _compute_ratios(self, row: Dict[str, Any]) -> Dict[str, Any]:
        """
        Derive margin/ROE/ROA/leverage ratios from raw statement line items.
        Provider-agnostic by design -- applied identically to yfinance- and
        Tiingo-sourced rows so a symbol's history doesn't shift definitions
        mid-series depending on which provider fetched which quarter.
        """
        revenue = row.get('revenue')
        gross_profit = row.get('gross_profit')
        operating_income = row.get('operating_income')
        net_income = row.get('net_income')
        total_assets = row.get('total_assets')
        total_debt = row.get('total_debt')
        total_equity = row.get('total_equity')
        current_assets = row.get('current_assets')
        current_liabilities = row.get('current_liabilities')

        # ROE/ROA/debt-to-equity can blow up to absurd magnitudes for
        # companies with near-zero or negative equity (e.g. MSTR, DJT) --
        # clamp rather than let the INSERT fail on DECIMAL(6,2) overflow.
        row['gross_margin'] = self._clamp_ratio((gross_profit / revenue * 100) if gross_profit is not None and revenue else None)
        row['operating_margin'] = self._clamp_ratio((operating_income / revenue * 100) if operating_income is not None and revenue else None)
        row['net_margin'] = self._clamp_ratio((net_income / revenue * 100) if net_income is not None and revenue else None)
        row['roe'] = self._clamp_ratio((net_income / total_equity * 100) if net_income is not None and total_equity else None)
        row['roa'] = self._clamp_ratio((net_income / total_assets * 100) if net_income is not None and total_assets else None)
        row['debt_to_equity'] = self._clamp_ratio((total_debt / total_equity) if total_debt is not None and total_equity else None)
        row['current_ratio'] = self._clamp_ratio((current_assets / current_liabilities) if current_assets is not None and current_liabilities else None)
        return row

    def _purge_overlapping_quarters(self, symbol: str, new_quarter_dates: List) -> None:
        """
        Tiingo and yfinance don't agree on exact period-end dates for the
        same real fiscal quarter -- confirmed empirically: AAPL's fiscal Q3
        2026 is dated 2026-06-30 by yfinance's calendar-quarter
        approximation (see _fetch_yfinance_quarters) vs. 2026-06-27 by
        Tiingo's own reported date, and can even land in different
        calendar-approximated fiscal_quarter numbers for companies whose
        fiscal year doesn't align with the calendar. Since `quarter` (a
        DATE) is quarterly_fundamentals' UNIQUE/dedup key, switching a
        symbol from yfinance- to Tiingo-sourced data would otherwise
        silently create a near-duplicate row per quarter instead of
        updating the existing one -- corrupting the very features that
        read this table as a trailing-8-quarter series (consecutive
        profitable quarters, YoY growth, turnaround detection). Delete any
        existing row within 10 days of an incoming Tiingo quarter date
        before upserting, so Tiingo becomes the sole source of truth for
        the quarters it covers.
        """
        if not new_quarter_dates:
            return
        try:
            existing = db.execute_dict_query(
                "SELECT quarter FROM quarterly_fundamentals WHERE symbol = %s", (symbol,)
            )
            to_delete = [
                row['quarter'] for row in existing
                if row['quarter'] not in new_quarter_dates
                and any(abs((row['quarter'] - nq).days) <= 10 for nq in new_quarter_dates)
            ]
            if to_delete:
                db.execute_insert(
                    "DELETE FROM quarterly_fundamentals WHERE symbol = %s AND quarter = ANY(%s)",
                    (symbol, to_delete)
                )
                self.logger.info(f"{symbol}: purged {len(to_delete)} near-duplicate quarter row(s) superseded by Tiingo")
        except Exception as e:
            self.logger.error(f"{symbol}: failed to purge overlapping quarters: {e}")

    @retry_on_failure(max_retries=2, delay=2.0)
    def fetch_quarterly_rows(self, symbol: str) -> List[Dict[str, Any]]:
        """Fetch trailing quarterly statements and build one row dict per
        quarter, from the configured provider (Tiingo if DATA_PROVIDER=
        tiingo, falling back to yfinance if Tiingo has nothing for this
        symbol)."""
        raw_rows: List[Dict[str, Any]] = []

        if config.data_provider == 'tiingo':
            from shared.tiingo_client import get_quarterly_statements
            raw_rows = get_quarterly_statements(symbol)
            if raw_rows:
                self._purge_overlapping_quarters(symbol, [row['quarter'] for row in raw_rows])
            else:
                self.logger.debug(f"{symbol}: no quarterly statements from Tiingo, falling back to yfinance")
                raw_rows = self._fetch_yfinance_quarters(symbol)
        else:
            raw_rows = self._fetch_yfinance_quarters(symbol)

        if not raw_rows:
            return []

        raw_rows = [self._compute_ratios(row) for row in raw_rows]

        # YoY growth vs. the quarter 4 positions back in this same batch
        # (both providers typically only return a handful of trailing
        # quarters per call, so this will often be null on the first
        # backfill and fill in naturally as more quarters accumulate on
        # future runs).
        for i, row in enumerate(raw_rows):
            prior = raw_rows[i + 4] if i + 4 < len(raw_rows) else None
            if prior:
                if row['revenue'] and prior['revenue']:
                    row['revenue_growth_yoy'] = self._clamp_ratio((row['revenue'] - prior['revenue']) / abs(prior['revenue']) * 100)
                else:
                    row['revenue_growth_yoy'] = None
                if row['eps'] and prior['eps']:
                    row['eps_growth_yoy'] = self._clamp_ratio((row['eps'] - prior['eps']) / abs(prior['eps']) * 100)
                else:
                    row['eps_growth_yoy'] = None
            else:
                row['revenue_growth_yoy'] = None
                row['eps_growth_yoy'] = None

        return raw_rows

    def upsert_quarter(self, row: Dict[str, Any]) -> bool:
        query = """
            INSERT INTO quarterly_fundamentals (
                symbol, quarter, fiscal_year, fiscal_quarter,
                revenue, gross_profit, operating_income, net_income, ebitda, eps,
                total_assets, total_debt, total_equity, current_assets, current_liabilities, cash,
                operating_cash_flow, free_cash_flow, capital_expenditures,
                gross_margin, operating_margin, net_margin, roe, roa, debt_to_equity, current_ratio,
                revenue_growth_yoy, eps_growth_yoy, piotroski_f_score, created_at, updated_at
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (symbol, quarter)
            DO UPDATE SET
                fiscal_year = EXCLUDED.fiscal_year,
                fiscal_quarter = EXCLUDED.fiscal_quarter,
                revenue = EXCLUDED.revenue,
                gross_profit = EXCLUDED.gross_profit,
                operating_income = EXCLUDED.operating_income,
                net_income = EXCLUDED.net_income,
                ebitda = EXCLUDED.ebitda,
                eps = EXCLUDED.eps,
                total_assets = EXCLUDED.total_assets,
                total_debt = EXCLUDED.total_debt,
                total_equity = EXCLUDED.total_equity,
                current_assets = EXCLUDED.current_assets,
                current_liabilities = EXCLUDED.current_liabilities,
                cash = EXCLUDED.cash,
                operating_cash_flow = EXCLUDED.operating_cash_flow,
                free_cash_flow = EXCLUDED.free_cash_flow,
                capital_expenditures = EXCLUDED.capital_expenditures,
                gross_margin = EXCLUDED.gross_margin,
                operating_margin = EXCLUDED.operating_margin,
                net_margin = EXCLUDED.net_margin,
                roe = EXCLUDED.roe,
                roa = EXCLUDED.roa,
                debt_to_equity = EXCLUDED.debt_to_equity,
                current_ratio = EXCLUDED.current_ratio,
                revenue_growth_yoy = EXCLUDED.revenue_growth_yoy,
                eps_growth_yoy = EXCLUDED.eps_growth_yoy,
                piotroski_f_score = EXCLUDED.piotroski_f_score,
                updated_at = EXCLUDED.updated_at
        """
        params = (
            row['symbol'], row['quarter'], row['fiscal_year'], row['fiscal_quarter'],
            row['revenue'], row['gross_profit'], row['operating_income'], row['net_income'], row['ebitda'], row['eps'],
            row['total_assets'], row['total_debt'], row['total_equity'], row['current_assets'], row['current_liabilities'], row['cash'],
            row['operating_cash_flow'], row['free_cash_flow'], row['capital_expenditures'],
            row['gross_margin'], row['operating_margin'], row['net_margin'], row['roe'], row['roa'], row['debt_to_equity'], row['current_ratio'],
            row['revenue_growth_yoy'], row['eps_growth_yoy'], row.get('piotroski_f_score'), datetime.now(), datetime.now(),
        )
        try:
            db.execute_insert(query, params)
            return True
        except Exception as e:
            self.logger.error(f"Database upsert failed for {row['symbol']} {row['quarter']}: {e}")
            return False

    def update_symbol(self, symbol: str) -> bool:
        try:
            rows = self.fetch_quarterly_rows(symbol)
            if not rows:
                self.logger.warning(f"No quarterly data available for {symbol}")
                return False

            success_count = sum(1 for row in rows if self.upsert_quarter(row))
            self.logger.info(f"✅ {symbol}: upserted {success_count}/{len(rows)} quarters")
            return success_count > 0

        except Exception as e:
            self.logger.error(f"Error updating quarterly fundamentals for {symbol}: {e}")
            return False

    @retry_on_failure(max_retries=3, delay=1.0)
    def get_symbols_to_update(self, limit: Optional[int] = None, recheck_after_days: int = 25) -> List[str]:
        """
        Symbols actually worth a quarterly-fundamentals API call today.

        Previously this queried every active symbol unconditionally, every
        run -- fine for a one-time backfill, wasteful once this runs daily
        (a company only files a new 10-Q/10-K ~4 times a year, so a daily
        blind re-fetch of all ~3,000 symbols was ~99% wasted requests; see
        2026-09-18 conversation).

        The staleness signal here is `updated_at` on each symbol's most
        recent quarter row, NOT the quarter's own end date -- that
        distinction matters and was wrong in an earlier version of this
        method: companies report ~30-45 days after quarter-end, so by the
        time a "new" quarter first appears in our data it's already most
        of the way through any reasonable re-check window measured from
        quarter-end. `updated_at` instead reflects "when did we last ask
        and confirm this was still current" -- upsert_quarter's ON
        CONFLICT clause refreshes it on every run regardless of whether
        the financial values actually changed, so it's a true last-checked
        timestamp. No separate earnings-calendar data source is needed:
        `recheck_after_days` (default 25) just needs to be shorter than a
        ~91-day quarter so a new filing is never missed for long, while
        still skipping the ~90% of days a symbol has nothing new to report.

        Always includes a symbol with no quarterly_fundamentals row yet
        (brand new -- needs an initial fetch regardless of timing). NOT
        applied to fundamentals_updater.py's daily_fundamentals (market
        cap/PE/PB) -- those are price-derived and genuinely change every
        day the stock trades, so a daily refresh there is correct, not
        wasteful, and this staleness logic would be the wrong fix for it.
        """
        try:
            query = """
            SELECT sp.symbol
            FROM (SELECT DISTINCT symbol FROM stock_prices) sp
            LEFT JOIN (
                SELECT symbol, MAX(updated_at) AS last_checked
                FROM quarterly_fundamentals
                GROUP BY symbol
            ) qf ON qf.symbol = sp.symbol
            WHERE sp.symbol IS NOT NULL
            AND sp.symbol != ''
            AND sp.symbol NOT IN (SELECT symbol FROM inactive_symbols)
            AND (
                qf.last_checked IS NULL
                OR qf.last_checked <= NOW() - INTERVAL '%s days'
            )
            ORDER BY sp.symbol
            """
            params = (recheck_after_days,)
            if limit:
                query += " LIMIT %s"
                params = (recheck_after_days, limit)

            results = db.execute_dict_query(query, params)
            symbols = [row['symbol'] for row in results]
            self.logger.info(
                f"Found {len(symbols)} symbols due for a quarterly fundamentals check "
                f"(no data yet, or not checked in the last {recheck_after_days} days)"
            )
            return symbols

        except Exception as e:
            self.logger.error(f"Error getting symbols: {e}")
            return []

    def run_update(self, symbols: Optional[List[str]] = None, limit: Optional[int] = None):
        start_time = datetime.now()
        self.logger.info(f"🚀 Starting quarterly fundamentals update at {start_time}")

        if symbols is None:
            symbols = self.get_symbols_to_update(limit)

        if not symbols:
            self.logger.warning("No symbols to update")
            return {'success': False, 'message': 'No symbols found', 'symbols_processed': 0,
                    'symbols_successful': 0, 'symbols_failed': 0}

        successful_updates = 0
        failed_updates = 0

        progress = ProgressBar(len(symbols), prefix="Quarterly fundamentals", logger=self.logger)
        for i, symbol in enumerate(symbols, 1):
            try:
                success = self.update_symbol(symbol)
                if success:
                    successful_updates += 1
                    progress.log(f"✅ {symbol}: quarterly fundamentals updated")
                else:
                    failed_updates += 1
                    progress.log(f"⚠️ {symbol}: quarterly fundamentals update failed", level="warning")

                if i < len(symbols):
                    time.sleep(self.rate_limit_delay)

            except Exception as e:
                progress.log(f"Unexpected error processing {symbol}: {e}", level="error")
                failed_updates += 1
                success = False

            progress.update(success)

        progress.close()

        duration = datetime.now() - start_time
        success_rate = (successful_updates / len(symbols) * 100) if symbols else 0
        self.logger.info(f"""
        🎉 Quarterly fundamentals update completed in {duration}:
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
            'success_rate': success_rate,
        }


def get_symbols_from_file(path, only_new=True):
    """
    Load a symbol list from one of symbol_scraper.py's stock_lists CSV/TXT/
    JSON outputs -- same pattern as repopulate_from_tiingo.py's
    get_new_symbols_from_file(), used here for onboarding the Russell 3000
    expansion's quarterly fundamentals (2026-09-18). `only_new` filters to
    symbols not already in quarterly_fundamentals, rather than stock_prices
    -- this script's own coverage table, not the price-history one.
    """
    import pandas as pd
    if path.endswith('.csv'):
        symbols = pd.read_csv(path)['symbol'].tolist()
    elif path.endswith('.txt'):
        with open(path) as f:
            symbols = [line.strip() for line in f if line.strip()]
    elif path.endswith('.json'):
        import json
        with open(path) as f:
            symbols = json.load(f)['symbols']
    else:
        raise ValueError(f"Unsupported file format: {path}")

    symbols = sorted(set(symbols))

    if only_new:
        existing = {row['symbol'] for row in db.execute_dict_query(
            "SELECT DISTINCT symbol FROM quarterly_fundamentals"
        )}
        symbols = [s for s in symbols if s not in existing]

    return symbols


def main():
    import argparse

    parser = argparse.ArgumentParser(description='Quarterly Fundamentals Updater')
    parser.add_argument('--limit', type=int, help='Limit number of symbols to update')
    parser.add_argument('--test', nargs='+', help='Test with specific symbols')
    parser.add_argument('--batch', type=int, help='Run in batch mode with specified limit')
    parser.add_argument('--from-file', help='Load symbols from a stock_lists CSV/TXT/JSON file instead of the DB')
    parser.add_argument('--only-new', action='store_true', help='With --from-file: only symbols not already in quarterly_fundamentals')

    args = parser.parse_args()

    setup_logging(level='INFO')
    updater = QuarterlyFundamentalsUpdater()

    if args.test:
        updater.logger.info(f"🧪 Running test update with symbols: {args.test}")
        result = updater.run_update(symbols=args.test)
    elif args.from_file:
        symbols = get_symbols_from_file(args.from_file, only_new=args.only_new)
        updater.logger.info(f"📂 Running update from file: {len(symbols)} symbols")
        result = updater.run_update(symbols=symbols)
    elif args.batch:
        updater.logger.info(f"📦 Running batch update with limit: {args.batch}")
        result = updater.run_update(limit=args.batch)
    else:
        updater.logger.info("🚀 Running full quarterly fundamentals update")
        result = updater.run_update(limit=args.limit)

    if result['success']:
        print("🎉 Quarterly fundamentals update completed successfully!")
    else:
        print("⚠️ Quarterly fundamentals update completed with some failures")


if __name__ == "__main__":
    main()
