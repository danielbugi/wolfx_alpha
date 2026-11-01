"""Whole-universe orchestration over in-memory inputs (the same structures the database loaders and the research extracts produce). PURE (numpy).

    Inputs   quarterly rows by symbol, earnings report dates by symbol (reports with a reported EPS), prices by symbol, sector by symbol, a valuation store
    Output   feature records and event records for every symbol, as of one date.
"""
from __future__ import annotations

import csv
import gzip
import json
import os
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

import numpy as np

from inflection import config as C
from inflection import context as X
from inflection import pipeline as P


@dataclass
class Inputs:
    quarterly: Dict[str, List[Dict[str, Any]]]
    report_dates: Dict[str, List[date]]
    prices: Dict[str, X.Prices]
    sector_of: Dict[str, str]
    valuation: Optional[X.ValuationStore] = None
    meta: Mapping[str, Any] = field(default_factory=dict)
    surprises: Dict[str, List[Tuple[date, float]]] = field(default_factory=dict)       # (report date, surprise %), research only
    pit_items: Dict[str, List[Dict[str, Any]]] = field(default_factory=dict)           # symbol -> filing-version-aware items (pit.pit_items); when set, they REPLACE `quarterly`


def run_universe(inp: Inputs, asof: date, symbols: Optional[Sequence[str]] = None, sector_index: Optional[X.SectorIndex] = None) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    idx = sector_index if sector_index is not None else (X.SectorIndex(inp.prices, inp.sector_of) if inp.prices else None)
    feats: List[Dict[str, Any]] = []
    events: List[Dict[str, Any]] = []
    for sym in sorted(symbols if symbols is not None else (inp.pit_items or inp.quarterly)):
        if inp.pit_items:
            items = inp.pit_items.get(sym) or []
            if items:
                f, e = P.build_symbol_pit(sym, items, inp.sector_of.get(sym), inp.prices.get(sym), idx, inp.valuation, asof, C.METHOD_VERSION_SEC_PIT)
                feats += f
                events += e
            continue
        rows = inp.quarterly.get(sym) or []
        if not rows:
            continue
        f, e = P.build_symbol(sym, rows, inp.report_dates.get(sym, []), inp.sector_of.get(sym), inp.prices.get(sym), idx, inp.valuation, asof)
        feats += f
        events += e
    return feats, events


# ------------------------------------------------------------------------------------------------------------------------------------------------------------ research loaders
def _f(s: str) -> Optional[float]:
    return float(s) if s not in ("", None) else None


def load_fundamentals_extract(directory: str) -> Tuple[Dict[str, List[Dict[str, Any]]], List[Dict[str, Any]], Dict[str, str], Mapping[str, Any]]:
    """quarterly rows by symbol, valuation rows, sector by symbol and the meta of an extract written by research.strategy_lab.extract_fundamentals (sha256 verified)."""
    from research.strategy_lab.data import sha256_file
    meta = json.load(open(os.path.join(directory, "fundamentals_meta.json"), encoding="utf-8"))
    for k, name in (("quarterly", "quarterly.csv.gz"), ("valuation", "valuation.csv.gz")):
        if sha256_file(os.path.join(directory, name)) != meta[f"{k}_sha256"]:
            raise ValueError(f"{name} does not match the sha256 in fundamentals_meta.json; refused")
    quarterly: Dict[str, List[Dict[str, Any]]] = {}
    with gzip.open(os.path.join(directory, "quarterly.csv.gz"), "rt", newline="") as fh:
        for r in csv.DictReader(fh):
            quarterly.setdefault(r["symbol"], []).append({
                "quarter": date.fromisoformat(r["quarter"]), "fiscal_year": int(r["fiscal_year"]) if r["fiscal_year"] else None,
                "fiscal_quarter": int(r["fiscal_quarter"]) if r["fiscal_quarter"] else None, "revenue": _f(r["revenue"]), "operating_income": _f(r["operating_income"]),
                "net_income": _f(r["net_income"]), "operating_cash_flow": _f(r["operating_cash_flow"]), "free_cash_flow": _f(r["free_cash_flow"]), "updated_at": r["updated_at"] or None})
    valuation: List[Dict[str, Any]] = []
    sector_of: Dict[str, str] = {}
    with gzip.open(os.path.join(directory, "valuation.csv.gz"), "rt", newline="") as fh:
        for r in csv.DictReader(fh):
            valuation.append({"symbol": r["symbol"], "date": date.fromisoformat(r["date"]), "ps_ratio": _f(r["ps_ratio"]), "pe_ratio": _f(r["pe_ratio"]), "market_cap": _f(r["market_cap"]),
                              "sector": r["sector"] or None})
            if r["sector"]:
                sector_of[r["symbol"]] = r["sector"]
    return quarterly, valuation, sector_of, meta


def load_sec_facts_extract(directory: str):
    """(raw facts by symbol, acceptance dates by accession, meta) from an extract written by research.strategy_lab.extract_sec_facts (sha256 verified)."""
    from data_updaters import sec_statements as SS
    from research.strategy_lab.data import sha256_file
    meta = json.load(open(os.path.join(directory, "sec_meta.json"), encoding="utf-8"))
    for k, name in (("facts", "sec_facts.csv.gz"), ("filings", "sec_filings.csv.gz")):
        if sha256_file(os.path.join(directory, name)) != meta[f"{k}_sha256"]:
            raise ValueError(f"{name} does not match the sha256 in sec_meta.json; refused")
    facts: Dict[str, List[Any]] = {}
    with gzip.open(os.path.join(directory, "sec_facts.csv.gz"), "rt", newline="") as fh:
        for r in csv.DictReader(fh):
            facts.setdefault(r["symbol"], []).append(SS.RawFact(r["metric"], r["tag"], r["unit"], float(r["value"]), date.fromisoformat(r["period_start"]), date.fromisoformat(r["period_end"]),
                                                                int(r["fy"]) if r["fy"] else None, r["fp"] or None, r["form"], r["accession"], date.fromisoformat(r["filed_date"])))
    accepted: Dict[str, date] = {}
    with gzip.open(os.path.join(directory, "sec_filings.csv.gz"), "rt", newline="") as fh:
        for r in csv.DictReader(fh):
            accepted[r["accession"]] = date.fromisoformat(r["accepted_date_et"])
    return facts, accepted, meta


def load_inputs(prices_dir: str, fundamentals_dir: str, sec_dir: Optional[str] = None) -> Inputs:
    """Real research inputs: the strategy-lab price/earnings extract plus the fundamentals extract. With `sec_dir`, the fundamentals come from the SEC raw facts instead, resolved filing-version-aware
    (as published at each quarter's first-public date): the point-in-time input a confirmatory test needs."""
    from research.strategy_lab.data import load_extract
    panel = load_extract(prices_dir)
    quarterly, valuation, sector_of, fmeta = load_fundamentals_extract(fundamentals_dir)
    prices = {s: X.Prices(b.dates, b.c, b.o, b.v) for s, b in panel.bars.items()}
    surprises = {s: [(r.report_date, r.surprise_pct) for r in rs if r.surprise_pct is not None] for s, rs in panel.reports.items()}
    reports = {s: [r.report_date for r in rs if r.eps_actual is not None] for s, rs in panel.reports.items()}
    inp = Inputs(quarterly, reports, prices, sector_of, X.ValuationStore(valuation), {"prices_data_hash": panel.data_hash, "fundamentals_meta": dict(fmeta)}, surprises)
    if sec_dir:
        from inflection import pit as PIT
        facts, accepted, smeta = load_sec_facts_extract(sec_dir)
        inp.pit_items = {sym: PIT.pit_items(fs, accepted, sector_of.get(sym)) for sym, fs in facts.items()}
        inp.quarterly = {}
        inp.meta = {**inp.meta, "sec_meta": dict(smeta), "point_in_time": True}
    return inp
