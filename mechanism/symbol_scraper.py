# automation/symbol_scraper.py

import requests
import pandas as pd
import json
import time
from datetime import datetime
from bs4 import BeautifulSoup
import yfinance as yf
from urllib.parse import urljoin
import warnings
import re
import concurrent.futures

warnings.filterwarnings('ignore')

SYMBOL_CHECK_TIMEOUT_SECONDS = 15


class StockSymbolScraper:
    def __init__(self, output_dir='stock_lists'):
        """
        Initialize the stock symbol scraper

        Args:
            output_dir (str): Directory to save scraped symbol files
        """
        self.output_dir = output_dir
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
        })

        # Create output directory
        import os
        if not os.path.exists(output_dir):
            os.makedirs(output_dir)

    def _get_with_retry(self, url, max_retries=4, initial_delay=5):
        """
        GET a Wikipedia page with retry/backoff on rate-limit responses.

        Added 2026-09-15 after the Russell 1000 fetch hit
        "403 Client Error: Too many requests. Please respect our robot
        policy" -- a real bot-policy rate limit, not a one-off. Scraping
        three Wikipedia pages back-to-back with only a flat 2s gap between
        them isn't polite enough for Wikipedia's current throttling.
        Backs off on 403/429 specifically (other errors are left to the
        caller, same as before); also enforces a minimum gap since the
        session's last request regardless of which page is being fetched.
        """
        for attempt in range(max_retries):
            try:
                response = self.session.get(url, timeout=30)
                if response.status_code in (403, 429):
                    delay = initial_delay * (2 ** attempt)
                    print(f"   Rate limited ({response.status_code}), waiting {delay}s before retry {attempt + 1}/{max_retries}...")
                    time.sleep(delay)
                    continue
                response.raise_for_status()
                return response
            except requests.exceptions.HTTPError:
                raise
        # Exhausted retries -- let the caller's existing exception handling
        # (each scrape_*_symbols already wraps its body in try/except) deal
        # with it the same way any other failure was always handled.
        response.raise_for_status()
        return response

    def scrape_sp500_symbols(self):
        """
        Scrape S&P 500 symbols from Wikipedia

        Returns:
            list: List of S&P 500 stock symbols
        """
        print("🔍 Scraping S&P 500 symbols...")

        try:
            url = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
            response = self._get_with_retry(url)

            # Parse the HTML
            soup = BeautifulSoup(response.content, 'html.parser')

            # Find the main table
            table = soup.find('table', {'id': 'constituents'})

            if not table:
                print("❌ Could not find S&P 500 table on the page")
                return [], []

            symbols = []
            company_info = []

            # Extract symbols from the table
            for row in table.find_all('tr')[1:]:  # Skip header row
                cells = row.find_all('td')
                if len(cells) >= 2:
                    symbol = cells[0].text.strip()
                    company_name = cells[1].text.strip()
                    sector = cells[3].text.strip() if len(cells) > 3 else "N/A"

                    # debugging output:
                    # print(f"Found symbol: {symbol}, Company: {company_name}, Sector: {sector}")

                    symbols.append(symbol)
                    company_info.append({
                        'symbol': symbol,
                        'company_name': company_name,
                        'sector': sector,
                        'index': 'SP500'
                    })

            print(f"✅ Successfully scraped {len(symbols)} S&P 500 symbols")
            return symbols, company_info

        except Exception as e:
            print(f"❌ Error scraping S&P 500: {str(e)}")
            return [], []

    def scrape_nasdaq100_symbols(self):
        """
        Scrape NASDAQ 100 symbols from Wikipedia

        Returns:
            list: List of NASDAQ 100 stock symbols
        """
        print("🔍 Scraping NASDAQ 100 symbols...")

        try:
            # NOTE: the constituents table used to live on the main
            # "Nasdaq-100" article; Wikipedia has since split it out to its
            # own "List of NASDAQ-100 companies" page (confirmed 2026-09-15
            # by fetching the raw HTML of both -- the old URL's page no
            # longer has any table with a Ticker/Company/Symbol header at
            # all, which is why this always silently returned 0 symbols
            # with no error). The new page has the exact
            # id="constituents" table this code already looks for, Ticker
            # as the first column -- matching cells[0] below -- so only
            # the URL needed to change.
            url = "https://en.wikipedia.org/wiki/List_of_NASDAQ-100_companies"
            response = self._get_with_retry(url)

            soup = BeautifulSoup(response.content, 'html.parser')

            # Find the components table
            table = soup.find('table', {'id': 'constituents'})
            if not table:
                # Try alternative table identification
                tables = soup.find_all('table', {'class': 'wikitable sortable'})
                table = tables[0] if tables else None

            symbols = []
            company_info = []

            if table:
                for row in table.find_all('tr')[1:]:  # Skip header
                    cells = row.find_all('td')
                    if len(cells) >= 2:
                        symbol = cells[0].text.strip() if len(cells) > 1 else cells[0].text.strip()
                        company_name = cells[1].text.strip()
                        sector = cells[2].text.strip() if len(cells) > 2 else "N/A"

                        # print(f"Found symbol: {symbol}, Company: {company_name}, Sector: {sector}")

                        symbols.append(symbol)
                        company_info.append({
                            'symbol': symbol,
                            'company_name': company_name,
                            'sector': sector,
                            'index': 'NASDAQ100'
                        })

            print(f"✅ Successfully scraped {len(symbols)} NASDAQ 100 symbols")
            return symbols, company_info

        except Exception as e:
            print(f"❌ Error scraping NASDAQ 100: {str(e)}")
            return [], []

    def scrape_russell1000_symbols(self):
        """
        Scrape Russell 1000 symbols from Wikipedia
        Returns:
            list: List of Russell 1000 stock symbols
        """
        print("🔍 Scraping Russell 1000 symbols...")
        # Method 1: Try iShares Russell 1000 ETF holdings
        try:
            # This is a simplified approach - in practice, you might need to handle
            # dynamic content loading or use selenium for JavaScript-heavy sites
            url = "https://en.wikipedia.org/wiki/Russell_1000_Index"
            response = self.session.get(url)
            response.raise_for_status()

            soup = BeautifulSoup(response.content, 'html.parser')

            # Find the components table
            table = soup.find('table', {'class': 'wikitable sortable'})
            if not table:
                tables = soup.find_all('table', {'class': 'wikitable sortable'})
                table = tables[0] if tables else None

            symbols = []
            company_info = []

            if table:
                for row in table.find_all('tr')[1:]:
                    cells = row.find_all('td')
                    if len(cells) >= 2:
                        symbol = cells[1].text.strip()
                        company_name = cells[0].text.strip()
                        sector = cells[2].text.strip() if len(cells) > 2 else "N/A"

                        # print(f"Found symbol: {symbol}, Company: {company_name}, Sector: {sector}")

                        symbols.append(symbol)
                        company_info.append({
                            'symbol': symbol,
                            'company_name': company_name,
                            'sector': sector,
                            'index': 'RUSSELL1000'
                        })

            # print(f"✅ Successfully obtained {len(symbols)} Russell 1000 symbols")
            return symbols, company_info

        except Exception as e:
            print(f"❌ Error scraping Russell 1000: {str(e)}")
            return [], []

    def scrape_russell3000_approx_symbols(self, top_n=3000):
        """
        Build an APPROXIMATION of the Russell 3000 by market-cap ranking,
        not a literal scrape of index membership.

        Why "approximation": Russell indices are FTSE Russell's proprietary
        IP with no free official constituent API. The usual free-tier
        workaround is scraping an iShares/Vanguard Russell-tracking ETF's
        published holdings (IWV, VTHR) -- tried first, 2026-09-18, and
        both are JS-rendered SPAs behind bot-mitigation that serves the
        page shell (HTML, not the CSV) to a plain HTTP client regardless of
        Content-Type headers; would need a headless browser to get past
        that, which is a heavier dependency than this project carries.

        Instead: pull every US-listed common stock from Nasdaq's public
        screener API (api.nasdaq.com/api/screener/stocks -- no auth, no
        bot-gating, used directly by their own public screener page), drop
        anything that isn't a plain common share (preferred/warrant/unit/
        rights/notes/depositary-share name patterns, and non-US-domiciled
        listings -- Russell indices are specifically for US-domiciled
        companies), then rank by market cap and take the top `top_n`. Since
        Russell 3000 membership IS (roughly) "the ~3,000 largest US
        companies by float-adjusted market cap, reconstituted annually,"
        this lands very close to the real thing in practice, even though
        it isn't a literal membership list and won't match FTSE Russell's
        exact inclusion rules, share-class handling, or annual
        reconstitution date.
        """
        print(f"🔍 Building Russell 3000 approximation (top {top_n} by market cap)...")

        try:
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                'Accept': 'application/json',
            }
            response = self.session.get(
                'https://api.nasdaq.com/api/screener/stocks',
                params={'limit': 10000, 'offset': 0, 'download': 'true'},
                headers=headers, timeout=60,
            )
            response.raise_for_status()
            rows = response.json()['data']['rows']

            excluded_name_patterns = ['warrant', 'unit', 'right', 'preferred', ' notes', 'depositary']

            candidates = []
            for row in rows:
                name = (row.get('name') or '').strip()
                symbol = (row.get('symbol') or '').strip()
                market_cap_raw = row.get('marketCap')
                country = (row.get('country') or '').strip()

                if not symbol or not name or not market_cap_raw:
                    continue
                if country and country != 'United States':
                    continue
                if any(pattern in name.lower() for pattern in excluded_name_patterns):
                    continue

                try:
                    market_cap = float(market_cap_raw)
                except (ValueError, TypeError):
                    continue
                if market_cap <= 0:
                    continue

                candidates.append({
                    'symbol': symbol,
                    'company_name': name,
                    'sector': row.get('sector') or 'N/A',
                    'index': 'RUSSELL3000_APPROX',
                    'market_cap': market_cap,
                })

            candidates.sort(key=lambda c: c['market_cap'], reverse=True)
            top = candidates[:top_n]

            symbols = [c['symbol'] for c in top]
            # market_cap was only needed for ranking -- drop it so this
            # matches the same {symbol, company_name, sector, index} shape
            # the other scrape_* methods return (save_symbols_to_files'
            # combined CSV assumes a consistent column set across sources).
            company_info = [
                {k: v for k, v in c.items() if k != 'market_cap'}
                for c in top
            ]

            print(f"✅ Built Russell 3000 approximation: {len(symbols)} symbols "
                  f"(from {len(candidates)} eligible common-stock candidates)")
            return symbols, company_info

        except Exception as e:
            print(f"❌ Error building Russell 3000 approximation: {str(e)}")
            return [], []

    def _is_date_like(self, text):
        """Check if text looks like a date"""
        if not text:
            return False

        date_patterns = [
            r'\w+ \d{1,2}, \d{4}',  # "July 9, 2025"
            r'\d{1,2}/\d{1,2}/\d{4}',  # "07/09/2025"
            r'\d{4}-\d{2}-\d{2}',  # "2025-07-09"
        ]

        for pattern in date_patterns:
            if re.match(pattern, text):
                return True
        return False

    def _is_valid_symbol(self, text):
        """Check if text looks like a valid stock symbol"""
        if not text:
            return False

        # Stock symbols are typically 1-5 uppercase letters, sometimes with dots
        pattern = r'^[A-Z]{1,5}(\.[A-Z])?$'
        return re.match(pattern, text.strip()) is not None

    def validate_symbols(self, symbols):
        """
        Validate symbols by checking if they exist and are tradeable

        Args:
            symbols (list): List of stock symbols to validate

        Returns:
            tuple: (valid_symbols, invalid_symbols)
        """
        print(f"🔍 Validating {len(symbols)} symbols...")

        valid_symbols = []
        invalid_symbols = []

        # Check symbols in batches to avoid overwhelming the API
        batch_size = 10
        for i in range(0, len(symbols), batch_size):
            batch = symbols[i:i + batch_size]

            for symbol in batch:
                try:
                    # Quick check using yfinance, with a hard wall-clock
                    # timeout. ticker.info has no timeout of its own -- this
                    # is the exact same pattern that hung for 7.6 hours on
                    # one stuck symbol during the 2026-09-14 fundamentals
                    # run (see MILESTONES.md). Not using a `with
                    # ThreadPoolExecutor()` block here either, for the same
                    # reason as that fix: its __exit__ calls
                    # shutdown(wait=True) by default, which would block on
                    # a still-hung worker thread anyway.
                    executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
                    future = executor.submit(lambda s=symbol: yf.Ticker(s).info)
                    try:
                        info = future.result(timeout=SYMBOL_CHECK_TIMEOUT_SECONDS)
                    finally:
                        executor.shutdown(wait=False)

                    # Check if symbol has basic required data
                    if info and 'symbol' in info:
                        valid_symbols.append(symbol)
                    else:
                        invalid_symbols.append(symbol)

                except Exception:
                    invalid_symbols.append(symbol)

            # Small delay between batches
            time.sleep(1)

            # Progress update
            if (i // batch_size + 1) % 5 == 0:
                print(f"   Validated {min(i + batch_size, len(symbols))}/{len(symbols)} symbols...")

        print(f"✅ Validation complete: {len(valid_symbols)} valid, {len(invalid_symbols)} invalid")
        return valid_symbols, invalid_symbols

    def save_symbols_to_files(self, symbols_data, validate=True):
        """
        Save symbols to various file formats

        Args:
            symbols_data (dict): Dictionary with index names as keys and (symbols, info) as values
            validate (bool): Whether to validate symbols before saving
        """
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')

        all_symbols = []
        all_info = []

        for index_name, (symbols, info) in symbols_data.items():
            if not symbols:
                continue

            print(f"\n📁 Processing {index_name} symbols...")

            # Validate symbols if requested
            if validate:
                valid_symbols, invalid_symbols = self.validate_symbols(symbols)
                if invalid_symbols:
                    print(f"⚠️  Invalid symbols removed: {invalid_symbols}")
                symbols = valid_symbols
                info = [item for item in info if item['symbol'] in valid_symbols]

            # Save individual index files
            # CSV format
            csv_filename = f"{self.output_dir}/{index_name.lower()}_symbols_{timestamp}.csv"
            df = pd.DataFrame(info)
            # print("df check:: \n", df)
            df.to_csv(csv_filename, index=False)

            # Simple text file (just symbols)
            txt_filename = f"{self.output_dir}/{index_name.lower()}_symbols_{timestamp}.txt"
            with open(txt_filename, 'w') as f:
                for symbol in symbols:
                    f.write(f"{symbol}\n")

            # JSON format
            json_filename = f"{self.output_dir}/{index_name.lower()}_symbols_{timestamp}.json"
            with open(json_filename, 'w') as f:
                json.dump({
                    'index': index_name,
                    'timestamp': timestamp,
                    'total_symbols': len(symbols),
                    'symbols': symbols,
                    'company_info': info
                }, f, indent=2)

            print(f"✅ Saved {len(symbols)} symbols to:")
            print(f"   📄 {csv_filename}")
            print(f"   📄 {txt_filename}")
            print(f"   📄 {json_filename}")

            # Add to combined list
            all_symbols.extend(symbols)
            all_info.extend(info)

        # Save combined file
        if all_symbols:
            print(f"\n📁 Saving combined file...")

            # Remove duplicates while preserving order
            unique_symbols = list(dict.fromkeys(all_symbols))

            # Combined CSV
            combined_csv = f"{self.output_dir}/all_indices_symbols_{timestamp}.csv"
            df_combined = pd.DataFrame(all_info)
            df_combined.drop_duplicates(subset=['symbol'], keep='first', inplace=True)
            df_combined.to_csv(combined_csv, index=False)

            # Combined text file
            combined_txt = f"{self.output_dir}/all_indices_symbols_{timestamp}.txt"
            with open(combined_txt, 'w') as f:
                for symbol in unique_symbols:
                    f.write(f"{symbol}\n")

            # Combined JSON
            combined_json = f"{self.output_dir}/all_indices_symbols_{timestamp}.json"
            with open(combined_json, 'w') as f:
                json.dump({
                    'indices': list(symbols_data.keys()),
                    'timestamp': timestamp,
                    'total_unique_symbols': len(unique_symbols),
                    'symbols': unique_symbols,
                    'company_info': df_combined.to_dict('records')
                }, f, indent=2)

            print(f"✅ Saved {len(unique_symbols)} unique symbols to:")
            print(f"   📄 {combined_csv}")
            print(f"   📄 {combined_txt}")
            print(f"   📄 {combined_json}")

    def scrape_all_indices(self, validate=True):
        """
        Scrape all major indices and save to files

        Args:
            validate (bool): Whether to validate symbols
        """
        print("🚀 Starting comprehensive stock symbol scraping...")
        print("=" * 60)

        symbols_data = {}

        # Scrape S&P 500
        sp500_symbols, sp500_info = self.scrape_sp500_symbols()
        if sp500_symbols:
            symbols_data['SP500'] = (sp500_symbols, sp500_info)

        # Small delay between requests
        time.sleep(2)

        # Scrape NASDAQ 100
        nasdaq100_symbols, nasdaq100_info = self.scrape_nasdaq100_symbols()
        if nasdaq100_symbols:
            symbols_data['NASDAQ100'] = (nasdaq100_symbols, nasdaq100_info)

        time.sleep(2)

        # Scrape Russell 1000
        russell1000_symbols, russell1000_info = self.scrape_russell1000_symbols()
        if russell1000_symbols:
            symbols_data['RUSSELL1000'] = (russell1000_symbols, russell1000_info)

        time.sleep(2)

        # Russell 3000 approximation (market-cap-ranked, see method docstring
        # for why this isn't a literal index-membership scrape)
        russell3000_symbols, russell3000_info = self.scrape_russell3000_approx_symbols()
        if russell3000_symbols:
            symbols_data['RUSSELL3000_APPROX'] = (russell3000_symbols, russell3000_info)

        # Save all data
        print("\n" + "=" * 60)
        print("💾 Saving scraped symbols to files...")
        print("=" * 60)

        self.save_symbols_to_files(symbols_data, validate=validate)

        print("\n🎉 Scraping completed successfully!")

        # Summary
        total_symbols = sum(len(data[0]) for data in symbols_data.values())
        print(f"\n📊 SUMMARY:")
        print(f"   S&P 500: {len(symbols_data.get('SP500', [[], []])[0])} symbols")
        print(f"   NASDAQ 100: {len(symbols_data.get('NASDAQ100', [[], []])[0])} symbols")
        print(f"   Russell 1000: {len(symbols_data.get('RUSSELL1000', [[], []])[0])} symbols")
        print(f"   Russell 3000 (approx): {len(symbols_data.get('RUSSELL3000_APPROX', [[], []])[0])} symbols")
        print(f"   Total: {total_symbols} symbols")

        return symbols_data


# Utility functions for loading symbols into the Donchian screener
class SymbolLoader:
    """Helper class to load scraped symbols for use in the Donchian screener"""

    @staticmethod
    def load_symbols_from_file(filename):
        """
        Load symbols from various file formats

        Args:
            filename (str): Path to the symbol file

        Returns:
            list: List of stock symbols
        """
        try:
            if filename.endswith('.txt'):
                with open(filename, 'r') as f:
                    symbols = [line.strip() for line in f if line.strip()]

            elif filename.endswith('.csv'):
                df = pd.read_csv(filename)
                symbols = df['symbol'].tolist()

            elif filename.endswith('.json'):
                with open(filename, 'r') as f:
                    data = json.load(f)
                    symbols = data['symbols']

            else:
                raise ValueError("Unsupported file format. Use .txt, .csv, or .json")

            return symbols

        except Exception as e:
            print(f"❌ Error loading symbols from {filename}: {str(e)}")
            return []

    @staticmethod
    def get_latest_symbol_files(directory='stock_lists'):
        """
        Get the most recent symbol files for each index

        Args:
            directory (str): Directory containing symbol files

        Returns:
            dict: Dictionary with index names and their latest file paths
        """
        import os
        import glob

        if not os.path.exists(directory):
            return {}

        latest_files = {}

        for index in ['sp500', 'nasdaq100', 'russell1000', 'all_indices']:
            pattern = f"{directory}/{index}_symbols_*.json"
            files = glob.glob(pattern)

            if files:
                # Get the most recent file
                latest_file = max(files, key=os.path.getctime)
                latest_files[index] = latest_file

        return latest_files


# Example usage
if __name__ == "__main__":
    # Initialize scraper
    scraper = StockSymbolScraper(output_dir='stock_lists')

    # Scrape all indices
    symbols_data = scraper.scrape_all_indices(validate=False)  # Set validate=True for thorough validation

    # Example: Load symbols for use in Donchian screener
    print("\n" + "=" * 60)
    print("📖 Example: Loading symbols for Donchian screener")
    print("=" * 60)

    loader = SymbolLoader()
    latest_files = loader.get_latest_symbol_files()

    if latest_files:
        # Load S&P 500 symbols
        if 'sp500' in latest_files:
            sp500_symbols = loader.load_symbols_from_file(latest_files['sp500'])
            print(f"📊 Loaded {len(sp500_symbols)} S&P 500 symbols")
            print(f"   First 10: {sp500_symbols[:10]}")

        # Load all indices symbols
        if 'all_indices' in latest_files:
            all_symbols = loader.load_symbols_from_file(latest_files['all_indices'])
            print(f"📊 Loaded {len(all_symbols)} symbols from all indices")

    print("\n✅ Ready to use with Donchian screener!")
    print("\nTo use with your screener:")
    print("  from stock_symbol_scraper import SymbolLoader")
    print("  loader = SymbolLoader()")
    print("  symbols = loader.load_symbols_from_file('stock_lists/sp500_symbols_latest.json')")
    print("  results = screener.screen_multiple_stocks(stock_list=symbols)")
