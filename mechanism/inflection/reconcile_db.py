#!/usr/bin/env python3
"""Read-only: reconcile two stored inflection event sets (default: SEC-sourced inflection_v1_sec vs yfinance-sourced inflection_v1) and print the structured result as JSON.

    python -m inflection.reconcile_db [--a inflection_v1_sec] [--b inflection_v1] [--examples 3]
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections import defaultdict
from typing import Any, Dict

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from inflection import config as C  # noqa: E402
from inflection import reconcile as R  # noqa: E402
from shared import db  # noqa: E402


def load(method_version: str):
    feats, avail, events = {}, {}, defaultdict(list)
    for r in db.execute_dict_query("SELECT symbol, quarter_end, metrics, available_at FROM fundamental_feature WHERE method_version = %s", (method_version,)):
        feats[(r["symbol"], r["quarter_end"])] = r["metrics"]
        avail[(r["symbol"], r["quarter_end"])] = r["available_at"]
    for r in db.execute_dict_query("SELECT symbol, quarter_end, event_type FROM fundamental_inflection_event WHERE method_version = %s", (method_version,)):
        events[(r["symbol"], r["quarter_end"])].append(r["event_type"])
    return feats, avail, events


def run(a: str, b: str, examples: int) -> Dict[str, Any]:
    fa, aa, ea = load(a)
    fb, ab, eb = load(b)
    out = R.reconcile(fa, fb, ea, eb, examples)
    out["availability_gap_days_a_minus_b"] = R.availability_gap(aa, ab)
    out["a"], out["b"] = a, b
    return out


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--a", default=C.METHOD_VERSION_SEC)
    p.add_argument("--b", default=C.METHOD_VERSION)
    p.add_argument("--examples", type=int, default=3)
    args = p.parse_args()
    print(json.dumps(run(args.a, args.b, args.examples), indent=1, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
