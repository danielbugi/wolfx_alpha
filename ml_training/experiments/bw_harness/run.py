"""Command line entry point and the pure pipeline.

    DB_HOST=... DB_PORT=... DB_NAME=... DB_USER=... DB_PASSWORD=... python -m bw_harness.run --out DIR [--realised strategy_signal_outcomes.csv.gz] [--code-ref SHA]

READ-ONLY: the database session is opened read-only (and refused otherwise); the only files written are in --out. Nothing here is scheduled or imported by any service. No model is trained.

`run_pipeline` is pure (frames in, files out) so the whole thing is tested on synthetic data: the same inputs and configuration always produce the same dataset hash, results hash and manifest.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from typing import Any, Dict, Mapping, Optional

import numpy as np
import pandas as pd

from . import features as F
from . import hypotheses as H
from . import io_read as IO
from . import provenance as P
from . import quality as Q
from . import splits as S
from . import survivorship as SV
from .config import DEFAULT, Config


def reconcile_signals(table_keys: pd.DataFrame, redetected: pd.DataFrame) -> Dict[str, Any]:
    a = set(zip(table_keys["symbol"], pd.to_datetime(table_keys["date"]), table_keys["direction"]))
    b = set(zip(redetected["symbol"], pd.to_datetime(redetected["date"]), redetected["direction"]))
    return {"table_signals": len(a), "redetected_signals": len(b), "in_both": len(a & b), "only_in_table": len(a - b), "only_redetected": len(b - a),
            "agreement": round(len(a & b) / max(1, len(a | b)), 6)}


def run_pipeline(prices: Mapping[str, pd.DataFrame], keys: pd.DataFrame, sector_snaps: Optional[pd.DataFrame], realised: Optional[pd.DataFrame], cfg: Config, out_dir: str,
                 reconciliation: Optional[Dict[str, Any]] = None, fingerprints: Optional[Dict[str, Any]] = None, code_ref: str = "unknown", forward: Optional[Dict[str, Any]] = None,
                 write_data: bool = True) -> Dict[str, Any]:
    os.makedirs(out_dir, exist_ok=True)
    df = F.build_dataset(prices, keys, sector_snaps, cfg)
    df = IO.merge_realised(df, realised)
    df["window_end_date"] = pd.to_datetime(df["window_end_date"])
    missing = P.check_dataset(df.columns)
    if missing:
        raise SystemExit(f"columns without provenance: {missing}")
    purge = S.purge_check(df, cfg)
    df, thr, edges = H.prepare(df, cfg)
    missing = P.check_dataset(df.columns)
    if missing:
        raise SystemExit(f"columns without provenance: {missing}")
    results = H.run_all(df, thr, cfg)
    surv = SV.analyse(results, cfg)
    quality = Q.report(df, cfg, reconciliation or {})
    quality["purge_check"] = purge
    quality["survivorship_empirical"] = SV.empirical_diagnostics(df)
    prov = IO.provenance_manifest(df)
    ds_info: Dict[str, Any] = {"rows": int(len(df)), "columns": int(df.shape[1])}
    if write_data:
        ds_info = IO.write_dataset(df, os.path.join(out_dir, "dataset.csv.gz"))
    else:                                                                              # hash without keeping the file (tests)
        import io as _io
        h = hashlib.sha256()
        cols = sorted(df.columns, key=lambda c: (0 if c in ("symbol", "signal_date", "direction") else 1, c))
        for i in range(0, len(df), 50000):
            buf = _io.StringIO()
            df.iloc[i:i + 50000][cols].to_csv(buf, index=False, header=(i == 0), float_format="%.10g", lineterminator="\n", date_format="%Y-%m-%d")
            h.update(buf.getvalue().encode("utf-8"))
        ds_info["sha256_uncompressed"] = h.hexdigest()
    body = {"config": cfg.to_dict(), "config_hash": cfg.hash(), "frozen_thresholds_train": thr, "frozen_matching_bins_train": edges, "hypothesis_results": results,
            "survivorship_sensitivity": surv, "data_quality": quality, "forward_report": forward}
    results_hash = IO.write_json(body, os.path.join(out_dir, "results.json"))
    prov_hash = IO.write_json(prov, os.path.join(out_dir, "provenance.json"))
    manifest = {"harness_version": cfg.version, "code_ref": code_ref, "config_hash": cfg.hash(), "dataset": ds_info, "results_sha256": results_hash, "provenance_sha256": prov_hash,
                "inputs": fingerprints or {}, "sample_counts": {"signals": int(len(df)), "eligible": int(df["eligible"].sum()),
                                                                 "independent_episodes_labelled": int((df["is_episode_first"] & df["eligible"] & (df["status_120"] == "ok")).sum())},
                "seeds": {"seed": cfg.seed, "n_boot": cfg.n_boot, "n_perm": cfg.n_perm},
                "boundary": "read-only research; hypothesis-generation or replication evidence on history that was examined before; no untouched historical holdout; no model trained"}
    IO.write_json(manifest, os.path.join(out_dir, "manifest.json"))
    return {"manifest": manifest, "results": results, "dataset": df}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--realised", default=None, help="strategy_signal_outcomes.csv.gz (realised net R of the five variants, 391 symbols)")
    ap.add_argument("--code-ref", default="unknown")
    ap.add_argument("--skip-reconcile", action="store_true")
    ap.add_argument("--forward-only", action="store_true")
    ap.add_argument("--n-boot", type=int, default=None)
    ap.add_argument("--n-perm", type=int, default=None)
    args = ap.parse_args(argv)
    cfg = DEFAULT
    if args.n_boot or args.n_perm:
        from dataclasses import replace
        cfg = replace(cfg, n_boot=args.n_boot or cfg.n_boot, n_perm=args.n_perm or cfg.n_perm)
    conn = IO.connect_readonly()
    os.makedirs(args.out, exist_ok=True)
    from . import forward as FW
    fwd = FW.run_forward_report(conn, cfg)
    if args.forward_only:
        IO.write_json(fwd, os.path.join(args.out, "forward_report.json"))
        print("forward report written")
        return 0
    fp = IO.input_fingerprint(conn)
    prices = IO.load_prices(conn)
    keys = IO.load_signal_keys(conn)
    snaps = IO.load_sector_snaps(conn)
    recon = None if args.skip_reconcile else reconcile_signals(keys, F.detect_signals(prices))
    realised = IO.load_realised(args.realised) if args.realised else None
    out = run_pipeline(prices, keys, snaps, realised, cfg, args.out, recon, fp, args.code_ref, fwd)
    print(json.dumps({"dataset_sha256": out["manifest"]["dataset"].get("sha256_uncompressed"), "results_sha256": out["manifest"]["results_sha256"], "config_hash": cfg.hash()}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
