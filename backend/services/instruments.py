# File: backend/services/instruments.py
"""
The macro instruments the dashboard's Markets pages show, grouped, with display names and units. Prices come from
market_index_prices, filled daily by mechanism/data_updaters/market_index_updater.py -- whose MARKET_INDEX_SYMBOLS list
must contain every symbol here (backend/tests/test_instruments.py checks that, so the two lists can't drift apart).
An instrument whose history hasn't been fetched yet is listed with has_data = false, never with made-up numbers.
"""
from typing import Dict, List, NamedTuple, Optional


class Instrument(NamedTuple):
    symbol: str
    name: str
    group: str
    unit: str = ""  # shown after the price, e.g. "/oz"


GROUPS: List[Dict[str, str]] = [
    {"key": "indices", "label": "Indices"},
    {"key": "index_futures", "label": "Index futures"},
    {"key": "metals", "label": "Metals"},
    {"key": "energy", "label": "Energy"},
    {"key": "agriculture", "label": "Agriculture"},
    {"key": "currencies", "label": "Currencies"},
    {"key": "crypto", "label": "Crypto"},
    {"key": "rates_vol", "label": "Rates & volatility"},
]

INSTRUMENTS: List[Instrument] = [
    Instrument("^GSPC", "S&P 500", "indices"),
    Instrument("^IXIC", "Nasdaq Composite", "indices"),
    Instrument("^DJI", "Dow Jones", "indices"),
    Instrument("^RUT", "Russell 2000", "indices"),
    Instrument("ES=F", "S&P 500 futures", "index_futures"),
    Instrument("NQ=F", "Nasdaq 100 futures", "index_futures"),
    Instrument("YM=F", "Dow futures", "index_futures"),
    Instrument("RTY=F", "Russell 2000 futures", "index_futures"),
    Instrument("GC=F", "Gold", "metals", "/oz"),
    Instrument("SI=F", "Silver", "metals", "/oz"),
    Instrument("PL=F", "Platinum", "metals", "/oz"),
    Instrument("PA=F", "Palladium", "metals", "/oz"),
    Instrument("HG=F", "Copper", "metals", "/lb"),
    Instrument("CL=F", "Crude oil (WTI)", "energy", "/bbl"),
    Instrument("BZ=F", "Brent crude", "energy", "/bbl"),
    Instrument("NG=F", "Natural gas", "energy", "/MMBtu"),
    Instrument("RB=F", "Gasoline (RBOB)", "energy", "/gal"),
    Instrument("HO=F", "Heating oil", "energy", "/gal"),
    Instrument("ZC=F", "Corn", "agriculture", "¢/bu"),
    Instrument("ZW=F", "Wheat", "agriculture", "¢/bu"),
    Instrument("ZS=F", "Soybeans", "agriculture", "¢/bu"),
    Instrument("KC=F", "Coffee", "agriculture", "¢/lb"),
    Instrument("SB=F", "Sugar", "agriculture", "¢/lb"),
    Instrument("CC=F", "Cocoa", "agriculture", "/t"),
    Instrument("CT=F", "Cotton", "agriculture", "¢/lb"),
    Instrument("LE=F", "Live cattle", "agriculture", "¢/lb"),
    Instrument("DX-Y.NYB", "US Dollar Index", "currencies"),
    Instrument("EURUSD=X", "EUR / USD", "currencies"),
    Instrument("GBPUSD=X", "GBP / USD", "currencies"),
    Instrument("JPY=X", "USD / JPY", "currencies"),
    Instrument("ILS=X", "USD / ILS", "currencies"),
    Instrument("BTC-USD", "Bitcoin", "crypto"),
    Instrument("ETH-USD", "Ethereum", "crypto"),
    Instrument("SOL-USD", "Solana", "crypto"),
    Instrument("^VIX", "VIX (volatility)", "rates_vol"),
    Instrument("^FVX", "5-year Treasury yield", "rates_vol", "%"),
    Instrument("^TNX", "10-year Treasury yield", "rates_vol", "%"),
    Instrument("^TYX", "30-year Treasury yield", "rates_vol", "%"),
]

BY_SYMBOL: Dict[str, Instrument] = {i.symbol: i for i in INSTRUMENTS}


def find(symbol: str) -> Optional[Instrument]:
    return BY_SYMBOL.get(symbol.upper()) or BY_SYMBOL.get(symbol)
