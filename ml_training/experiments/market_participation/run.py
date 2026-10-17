"""    DB_HOST=... DB_PORT=... DB_NAME=... DB_USER=... DB_PASSWORD=... python -m market_participation.run --out DIR [--start 2018-01-01] [--code-ref SHA]

READ-ONLY: reuses the harness's read-only session (refused unless the server says read-only) and writes only files in --out. Not scheduled, not imported by any service."""
from __future__ import annotations

import argparse
import json
import os
import sys

_EXP = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _EXP not in sys.path:
    sys.path.insert(0, _EXP)
from bw_harness import io_read as IO  # noqa: E402
from market_participation import participation as P  # noqa: E402


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--start", default="2018-01-01")
    ap.add_argument("--code-ref", default="unknown")
    a = ap.parse_args(argv)
    os.makedirs(a.out, exist_ok=True)
    conn = IO.connect_readonly()
    fp = IO.input_fingerprint(conn)
    prices = IO.load_prices(conn)
    df = P.session_table(prices, start=a.start)
    path = os.path.join(a.out, "breakout_participation.csv")
    df.to_csv(path, index=False, float_format="%.10g", lineterminator="\n", date_format="%Y-%m-%d")
    manifest = {"version": P.VERSION, "provenance": P.PROVENANCE, "universe": P.UNIVERSE_NOTE, "code_ref": a.code_ref, "start": a.start, "table_sha256": P.table_hash(df), "rows": int(len(df)),
                "inputs": fp, "summary": P.summary(df), "boundary": "read-only research; descriptive counts; no claim that participation predicts returns"}
    IO.write_json(manifest, os.path.join(a.out, "manifest.json"))
    print(json.dumps({"table_sha256": manifest["table_sha256"], "rows": manifest["rows"]}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
