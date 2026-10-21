"""Reproduction self-check for the Notebook 02 simulator (read-only; local CSV exports only).

Asserts the two engine-parity facts the research depends on:
  1. with the dataset rule (close entry, 3R target, no gap fills, no cost) the simulator reproduces
     ml_breakout_dataset_v2.r_single exactly;
  2. with the live rule (close entry, first target touched, stop at level, no cost) it replays every
     resolved signal_ledger row exactly (outcome and holding period).
usage (from this folder, after queries/export_nb01.sh and queries/export_nb02.sh):
    python selfcheck_nb02.py [sample_size]
"""
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import nb02_lib as LB  # noqa: E402


def main(n: int = 5000) -> int:
    P = LB.load_prices()
    B = LB.load_breakouts()
    B = B[B.r_single.notna()].sample(n, random_state=1).reset_index(drop=True)
    x = LB.simulate(P, B, entry="close", cost_bps=0, target_r=3.0, gap_fills=False)
    ok = x.exec_r.notna()
    d = (x.exec_r - B.r_single)[ok].abs()
    print(f"[1] dataset rule vs r_single: {ok.sum()} rows, max |diff| {d.max():.6f}")
    assert ok.sum() > 0.9 * n and d.max() < 1e-6, "simulator does not reproduce r_single"

    L = pd.read_csv(os.path.join(LB.D, "signal_ledger.csv"), parse_dates=["signal_date"])
    L = L[L.status.isin(["stopped", "target1", "target2", "target3", "expired"])].reset_index(drop=True)
    inp = pd.DataFrame({"symbol": L.symbol, "date": L.signal_date, "direction": L.direction, "atr_pct": L.atr / L.entry_price * 100})
    r = LB.simulate(P, inp, entry="close", cost_bps=0, gap_fills=False, first_touch_any_target=True, require_full=False)
    ok = r.exec_r.notna()
    same_r = ((r.exec_r - L.outcome_r)[ok].abs() < 1e-6).mean()
    same_b = (r.bars[ok] == L.bars_held[ok]).mean()
    print(f"[2] live rule replay: {ok.sum()} of {len(L)} resolved ledger rows, outcome identical {same_r:.2%}, bars identical {same_b:.2%}")
    assert ok.sum() == len(L) and same_r == 1.0 and same_b == 1.0, "simulator does not replay the live evaluator"
    print("OK")
    return 0


if __name__ == "__main__":
    sys.exit(main(int(sys.argv[1]) if len(sys.argv) > 1 else 5000))
