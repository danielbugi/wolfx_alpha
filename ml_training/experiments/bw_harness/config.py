"""One frozen configuration. Every choice that could be tuned after seeing a result is fixed HERE, and the hash of this object is written beside every output. Changing any value changes the hash, so a
result can always be tied to the exact configuration that produced it. Values below were fixed before any hypothesis was evaluated on the harness dataset (docs/research/BIG_WINNER_DISCOVERY.md section 5)."""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, Tuple


@dataclass(frozen=True)
class Config:
    # ---- canonical label (docs/research/BIG_WINNER_DISCOVERY.md section 4.1)
    entry: str = "next_session_open"
    unit_atr: float = 2.0                                  # 1R = 2 x ATR14 of the signal bar
    horizons: Tuple[int, ...] = (20, 60, 120)
    tiers_r: Tuple[float, ...] = (3.0, 5.0, 10.0)          # separate thresholds, never merged
    primary_horizon: int = 120
    primary_tier_r: float = 5.0                            # BW5 = MFE_R(120) >= 5
    secondary_tier_r: float = 10.0                         # BW10
    return_label_long_pct: float = 50.0                    # return-based label: MFE >= +50% (long)
    return_label_short_pct: float = 40.0                   # ... or a favourable fall of >= 40% (short)
    vol_neutral_threshold: float = 3.0                     # vol-neutral label: MFE% / (sigma60 * sqrt(H) * 100) >= 3, sigma60 = std of daily log returns over the 60 bars ending at the signal bar
    persistence_fraction: float = 0.5                      # persistence: close at the horizon >= half of the peak
    # ---- population
    min_dollar_volume_20: float = 1_000_000.0
    min_history_bars: int = 253
    episode_gap_bars: int = 120                            # a kept signal starts an episode; signals within 120 bars of the previous KEPT signal of the same symbol and direction belong to it
    # ---- chronological segments (signal dates); embargo/purge drop signals whose 120-bar window reaches the next segment
    train_start: str = "2018-01-01"
    train_end: str = "2021-06-30"
    validation_start: str = "2022-01-01"
    validation_end: str = "2023-06-30"
    test_start: str = "2024-01-01"
    # ---- hypotheses
    rs_windows: Tuple[int, int] = (60, 120)
    rs_top_pct: float = 0.80                               # H1 treated: relative-strength percentile >= 0.80 on BOTH windows
    rs_control_below_pct: float = 0.60                     # H1 controls: percentile < 0.60 on BOTH windows (bottom three quintiles)
    breadth_ma_bars: int = 50
    index_ma_bars: int = 200
    breadth_top_tercile_q: float = 2.0 / 3.0               # H2: breadth >= the TRAIN 2/3 quantile (frozen on train dates)
    near_high_pct: float = -5.0                            # H3 treated: pct_from_52w_high >= -5
    far_high_pct: float = -20.0                            # H3 controls: pct_from_52w_high <= -20
    squeeze_pctile_max: float = 20.0                       # H4 treated: chan_squeeze_pctile <= 20; controls: > 20
    sector_top_n: int = 3                                  # H5: sector 60-day relative-strength rank <= 3 of 11 ...
    group_breakouts_min: int = 3                           # ... OR >= 3 other same-sector same-direction breakouts in the previous 10 sessions
    group_lookback_sessions: int = 10
    relvol_top_q: float = 0.80                             # H0 negative control: vol_ratio_50 >= TRAIN 0.80 quantile; controls < TRAIN 0.60 quantile
    relvol_control_below_q: float = 0.60
    # ---- matching
    controls_per_treated: int = 3
    match_window_sessions: int = 5
    atr_pct_bins: int = 10                                 # ATR% deciles (edges frozen on train, within direction)
    dollar_vol_bins: int = 3
    ret60_bins: int = 3
    # ---- inference
    n_boot: int = 1000
    n_perm: int = 2000
    seed: int = 20261017
    alpha: float = 0.05
    min_rr: float = 1.25                                   # support threshold on the rate ratio (treated vs matched controls)
    min_years_same_sign: int = 6
    min_sets_per_year: int = 500
    years_considered: Tuple[int, ...] = (2018, 2019, 2020, 2021, 2022, 2023, 2024, 2025)
    # ---- survivorship sensitivity grids
    surv_phi_treated: Tuple[float, ...] = (0.0, 0.02, 0.05)
    surv_delta: Tuple[float, ...] = (0.0, 0.02, 0.05, 0.10, 0.20)
    surv_missing_winner_rate_long: Tuple[float, ...] = (0.0, 0.05)
    surv_missing_winner_rate_short: Tuple[float, ...] = (0.0, 0.5)
    version: str = "bw_harness_v1"

    def to_dict(self) -> Dict[str, Any]:
        return json.loads(json.dumps(asdict(self), sort_keys=True))

    def hash(self) -> str:
        return hashlib.sha256(json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":")).encode()).hexdigest()


DEFAULT = Config()
