# mechanism/alerts/market_context.py
"""Reads what the market card needs from Postgres: index tiles (market_index_prices) and sector performance (the digest's own
analysed stocks + daily_fundamentals sectors, so breadth and sectors describe the SAME stocks). Missing data is reported as
'n/a' / omitted -- never filled in."""
from __future__ import annotations

from typing import Dict, List, Tuple

import pandas as pd

from alerts.market_card import CardData, SectorBar, Tile

SPARK_SESSIONS = 60
MIN_STOCKS_PER_SECTOR = 5                 # fewer than this is noise, not a sector reading
# (symbol, label, kind): kind decides the number format and how the delta is expressed
TILES = [("^GSPC", "S&P 500", "index"), ("^IXIC", "Nasdaq", "index"), ("^RUT", "Russell 2000", "index"),
         ("^DJI", "Dow Jones", "index"), ("^VIX", "VIX (volatility)", "level"), ("^TNX", "10-year yield", "yield")]
MACRO_TILES = [("GC=F", "Gold", "index"), ("CL=F", "Crude oil (WTI)", "level"), ("DX-Y.NYB", "Dollar index", "level"),
               ("BTC-USD", "Bitcoin", "price0")]


def _tile(symbol: str, label: str, kind: str, series: pd.DataFrame, session) -> Tile:
    s = series.sort_values("date")
    if len(s) < 2 or pd.Timestamp(s["date"].iloc[-1]).date() != pd.Timestamp(session).date():
        return Tile(label, "n/a")                                         # no bar for the session: say so, do not guess
    last, prev = float(s["close"].iloc[-1]), float(s["close"].iloc[-2])
    spark = [float(v) for v in s["close"].tail(SPARK_SESSIONS)]
    direction = (last > prev) - (last < prev)
    if kind == "yield":                                                   # stored in percent; a % change of a yield misleads
        return Tile(label, f"{last:.3f}%", f"{abs(last - prev) * 100:.1f} bps", direction, spark)
    value = f"{last:,.0f}" if kind == "price0" else f"{last:,.2f}" if kind == "index" else f"{last:.2f}"
    return Tile(label, value, f"{abs(last / prev - 1) * 100:.2f}%", direction, spark)


def load_tiles(db, session, tiles=TILES) -> List[Tile]:
    rows = db.execute_dict_query(
        "SELECT symbol, date, close FROM market_index_prices WHERE symbol = ANY(%s) AND date <= %s AND date > %s::date - 140 "
        "ORDER BY symbol, date", ([t[0] for t in tiles], session, session))
    df = pd.DataFrame(rows, columns=["symbol", "date", "close"])
    return [_tile(sym, label, kind, df[df["symbol"] == sym], session) for sym, label, kind in tiles]


def load_macro_tiles(db, session) -> List[Tile]:
    return load_tiles(db, session, MACRO_TILES)


def sector_bars(stocks: List[Dict], sector_of: Dict[str, str]) -> Tuple[List[SectorBar], int]:
    """Equal-weighted mean 1-day % per sector over the analysed stocks; (bars sorted best -> worst, unclassified count)."""
    by: Dict[str, List[float]] = {}
    unclassified = 0
    for r in stocks:
        sector = sector_of.get(r["symbol"])
        if not sector or sector == "Unknown":
            unclassified += 1
            continue
        by.setdefault(sector, []).append(r["ret1_pct"])
    bars = [SectorBar(name, sum(v) / len(v)) for name, v in by.items() if len(v) >= MIN_STOCKS_PER_SECTOR]
    return sorted(bars, key=lambda b: -b.change_pct), unclassified


def load_sectors(db, stocks: List[Dict]) -> Tuple[List[SectorBar], int]:
    rows = db.execute_dict_query(
        "SELECT DISTINCT ON (symbol) symbol, sector FROM daily_fundamentals WHERE sector IS NOT NULL ORDER BY symbol, date DESC")
    return sector_bars(stocks, {r["symbol"]: r["sector"] for r in rows})


def build_card_data(db, session, stocks: List[Dict], universe_n: int, breadth: Dict[str, int]) -> CardData:
    sectors, unclassified = load_sectors(db, stocks)
    return CardData(session=pd.Timestamp(session).date(), universe_n=universe_n, up_n=breadth["up"], down_n=breadth["down"],
                    tiles=load_tiles(db, session), sectors=sectors, unclassified_n=unclassified)
