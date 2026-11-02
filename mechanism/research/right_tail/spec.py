"""The pre-declared definition of the asymmetric breakout study (v1). Its sha256 is the study's identity; a change is a new version, never an edit.
Everything a verdict depends on is fixed here BEFORE any result exists: entries, structural stops, exits, costs, the chronological split, the controls and the decision rule.
All thresholds are round a-priori numbers; none is fitted on the data.
"""
from __future__ import annotations

import copy
from typing import Any, Dict

from research.lab.manifest import canonical_hash

SPEC_V1: Dict[str, Any] = {
    "schema": "right_tail_study_v1",
    "key": "asymmetric_breakout",
    "version": "v1",
    "question": "Under which observable conditions do long breakouts produce unusually large REALISED R-multiples, net of costs, out of sample?",
    "evidence_class": "EXPLORATORY: 2017-2026 price history has been examined by earlier breakout studies, so no untouched historical holdout exists; only forward data can confirm",
    "direction": "long only (the thesis is right-tail upside; earlier studies found shorts negative and the short-side negative control failed)",
    "universe": {
        "source": "stock_prices (vendor back-adjusted OHLCV: splits, dividends and spin-offs folded into history; every price-derived value is REVISED, not point-in-time)",
        "min_history_bars": 260,
        "liquidity_at_signal": {"min_close": 5.0, "min_avg_dollar_volume_20": 5_000_000.0},
        "data_validity": "a signal is dropped when a flagged price discontinuity (price_discontinuities: split-like or >3.09x / <0.323x close jumps, non-positive prints) lies inside "
                         "[signal - 260 bars, exit bar]; the sensitivity keeps the ones whose discontinuity lies only on the trade path",
    },
    "signal_timing": "signal at the close of bar s; entry at the OPEN of bar s+1 (the scan runs after the close); a stop can be hit on the entry bar",
    "entries": {
        "D55": {"rule": "fresh 55-session Donchian breakout: close[s] > max(high[s-55..s-1]) and close[s-1] <= max(high[s-56..s-2])",
                "structural_stop": "lowest low of bars s-19..s (the 20-session swing low)"},
        "BASE": {"rule": "fresh breakout from a 40-session consolidation near highs: close[s] > max(high[s-40..s-1]) and close[s-1] <= max(high[s-41..s-2]); "
                         "base depth (max high - min low)/max high over s-40..s-1 <= 0.25; base top >= 0.85 x max(high[s-252..s-1])",
                 "structural_stop": "lowest low of the base, bars s-40..s-1"},
        "filters": {
            "RS": "126-session return of the stock at s in the top 20% of the liquid universe on that date (cross-sectional rank >= 0.80)",
            "VOL": "volume[s] >= 1.5 x mean(volume[s-50..s-1])",
            "REGIME": "equal-weight universe index (cumulated daily median return of liquid names) above its 200-session mean at s",
        },
        "sets": ["D55", "D55+RS", "D55+VOL", "D55+RS+VOL", "BASE", "BASE+RS", "BASE+VOL", "BASE+RS+VOL",
                 "D55+RS+VOL+REGIME", "BASE+RS+VOL+REGIME"],
    },
    "initial_risk": {
        "stop": "min(structural stop, entry - 1 x ATR14[s]) (at least one ATR of room, so noise cannot make R arbitrarily small)",
        "R": "1R = entry open - initial stop (the ACTUAL risk at entry)",
        "skip": "no trade when the entry opens at or below the stop, or when the risk exceeds 30% of the entry price (not a reasonable initial risk); counted",
    },
    "exits": {
        "X1_stop_only": "initial structural stop only; time exit at the close of bar 250 (about one year)",
        "X2_atr_trail": "chandelier: stop = max(initial stop, highest high since entry - 3 x ATR14[s]), ratchets up only; uses bars before the current one",
        "X3_weekly_trail": "stop = max(initial stop, lowest low of the 3 previous COMPLETED calendar weeks), updated at week boundaries, never loosened",
        "X4_trend_sma50": "exit at the next open after a close below the 50-session mean; the initial stop stays active intraday",
        "X5_time_60": "initial structural stop; time exit at the close of bar 60",
        "common": "every exit is capped at 250 bars; stop fills at the level, or at the open when the bar gaps through it; a position whose history ends before its exit is CENSORED (excluded, counted)",
    },
    "position_rule": "at most one open position per (entry set, exit, symbol); a signal on or before the previous exit bar is skipped",
    "costs_bps_round_trip": {"primary": 20.0, "sensitivity": [10.0, 40.0]},
    "split": {"basis": "signal date", "discovery": ["2017-10-01", "2023-01-01"], "validation_start": "2023-01-01",
              "purge": "a discovery position still open on 2023-01-01 is dropped from discovery", "note": "no parameter is tuned on either segment"},
    "controls": {
        "random_entry": "for every primary position one random session of the same symbol (liquid, at least 10 sessions from any primary signal), D55's stop rule, the same exit and costs",
        "parent": "D55 and BASE without filters (what the filters add)",
    },
    "portfolio": {"risk_per_position_pct_of_initial_equity": 0.5, "max_concurrent": 20, "order": "signal date, then higher RS rank, then symbol",
                  "marking": "daily close; drawdown reported in R and as % of initial equity at 0.5% risk"},
    "decision_rule": {
        "primary_cell": "BASE+RS+VOL, exit X4_trend_sma50, 20 bps, VALIDATION segment",
        "CONTINUE_requires_all": [
            "n_validation >= 150 positions",
            "validation net expectancy > 0 with the month-block bootstrap 95% interval entirely above 0",
            "validation expectancy minus the random-entry control > 0 with its 95% interval entirely above 0",
            "discovery net expectancy > 0",
            "share of positions realising >= 5R exceeds the random-entry control's share in both segments",
        ],
        "REVISE": "positive point expectancy in both segments but an interval that includes 0, or a tail-rate lift without a significant expectancy",
        "REJECT": "validation or discovery net expectancy <= 0 for the primary cell",
        "multiple_testing": "10 entry sets x 5 exits x 3 costs are reported; ONLY the primary cell decides. Other cells carry Bonferroni-widened intervals (50 cells) and are hypothesis-generating",
        "insufficient": "INSUFFICIENT_DATA when n_validation < 150",
    },
    "case_studies": "WDC, DOCN, SEZL are reported as illustrations of what the frozen rules did; they played no part in any definition",
}


def get_spec() -> Dict[str, Any]:
    return copy.deepcopy(SPEC_V1)


def spec_hash(s: Dict[str, Any] = SPEC_V1) -> str:
    return canonical_hash(s)
