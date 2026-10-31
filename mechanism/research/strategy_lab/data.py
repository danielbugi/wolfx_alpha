"""Reads a hashed research extract (prices + earnings) into arrays. The extract is two gzip CSV files plus `extract_meta.json` carrying their sha256; a file whose hash
differs from the meta is REFUSED, so a result always names the exact bytes it was computed from (`data_hash`).

prices.csv.gz    symbol,date,open,high,low,close,volume          sorted by symbol, date
earnings.csv.gz  symbol,report_date,eps_estimate,eps_actual,surprise_pct
"""
from __future__ import annotations

import csv
import gzip
import hashlib
import json
import os
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Dict, List, Mapping, Optional

import numpy as np


class DataError(ValueError):
    pass


@dataclass(frozen=True)
class Bars:
    dates: List[date]
    o: np.ndarray
    h: np.ndarray
    l: np.ndarray
    c: np.ndarray
    v: np.ndarray


@dataclass(frozen=True)
class Report:
    report_date: date
    eps_estimate: Optional[float]
    eps_actual: Optional[float]
    surprise_pct: Optional[float]


@dataclass
class Panel:
    bars: Dict[str, Bars]
    reports: Dict[str, List[Report]]
    data_hash: str
    meta: Mapping[str, Any] = field(default_factory=dict)


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def combined_hash(prices_sha: str, earnings_sha: str) -> str:
    return hashlib.sha256(f"prices:{prices_sha}|earnings:{earnings_sha}".encode()).hexdigest()


def _f(s: str) -> Optional[float]:
    return float(s) if s not in ("", None) else None


def load_extract(directory: str) -> Panel:
    meta_path = os.path.join(directory, "extract_meta.json")
    if not os.path.exists(meta_path):
        raise DataError(f"{meta_path} is missing")
    meta = json.load(open(meta_path, encoding="utf-8"))
    paths = {k: os.path.join(directory, f"{k}.csv.gz") for k in ("prices", "earnings")}
    shas = {k: sha256_file(p) for k, p in paths.items()}
    for k in paths:
        if shas[k] != meta.get(f"{k}_sha256"):
            raise DataError(f"{k}.csv.gz does not match the sha256 in extract_meta.json (extract altered or incomplete); refused")

    raw: Dict[str, Dict[str, list]] = {}
    with gzip.open(paths["prices"], "rt", newline="") as fh:
        rd = csv.reader(fh)
        header = next(rd)
        if header != ["symbol", "date", "open", "high", "low", "close", "volume"]:
            raise DataError(f"unexpected prices header {header}")
        for sym, d, o, h, l, c, v in rd:
            if c == "" or o == "" or h == "" or l == "":
                continue
            b = raw.setdefault(sym, {"d": [], "o": [], "h": [], "l": [], "c": [], "v": []})
            b["d"].append(date.fromisoformat(d))
            b["o"].append(float(o)); b["h"].append(float(h)); b["l"].append(float(l)); b["c"].append(float(c)); b["v"].append(float(v) if v != "" else 0.0)
    bars: Dict[str, Bars] = {}
    for sym, b in raw.items():
        order = np.argsort(np.array(b["d"], dtype="datetime64[D]"), kind="stable")
        ds = [b["d"][i] for i in order]
        if any(ds[i] >= ds[i + 1] for i in range(len(ds) - 1)):
            continue                                                       # duplicated or unordered dates: the symbol is unusable, never repaired
        bars[sym] = Bars(ds, *(np.array(b[k], dtype=float)[order] for k in ("o", "h", "l", "c", "v")))

    reports: Dict[str, List[Report]] = {}
    with gzip.open(paths["earnings"], "rt", newline="") as fh:
        rd = csv.reader(fh)
        header = next(rd)
        if header != ["symbol", "report_date", "eps_estimate", "eps_actual", "surprise_pct"]:
            raise DataError(f"unexpected earnings header {header}")
        for sym, d, est, act, sur in rd:
            reports.setdefault(sym, []).append(Report(date.fromisoformat(d), _f(est), _f(act), _f(sur)))
    for sym in reports:
        reports[sym].sort(key=lambda r: r.report_date)
    return Panel(bars, reports, combined_hash(shas["prices"], shas["earnings"]), meta)
