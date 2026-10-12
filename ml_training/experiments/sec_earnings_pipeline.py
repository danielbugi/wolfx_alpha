"""SEC earnings-catalyst pipeline for ONE company, as functions over data already fetched (no network, no database): filings + XBRL facts + prices -> events with anomaly class, decision
time, reaction and drift. Reuses the repository's own EDGAR modules (filing_contract facts, edgar_collector parsing, catalyst_timing, sec_financials, financial_anomaly); adds only the
price-reaction arithmetic, which is the research part.

Decision point: the acceptance timestamp of the 8-K Item 2.02 earnings release matched to the periodic report when there is one (`release_source` = 8k_item_2_02), otherwise the periodic
report's own acceptance (`periodic_filing`). The financial numbers come from the periodic report's XBRL facts, as known on its filing date; they were public at the release (same results).
"""
from __future__ import annotations

from collections import Counter
from datetime import date, datetime
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

import numpy as np
import pandas as pd

from market_intelligence import catalyst_timing as T
from market_intelligence import filing_contract as F
from market_intelligence import financial_anomaly as A
from market_intelligence import sec_financials as S

MIN_DOLLAR_VOLUME_20 = 5e6
MIN_PRICE = 2.0
SEC_ARCHIVE = "https://www.sec.gov/Archives/edgar/data"


def filing_url(cik: int, accession: str, primary_document: Optional[str]) -> str:
    base = f"{SEC_ARCHIVE}/{cik}/{accession.replace('-', '')}"
    return f"{base}/{primary_document}" if primary_document else base


def build_events(symbol: str, cik: int, sub_facts: Sequence[F.FilingFact], companyfacts: Mapping[str, Any], px: pd.DataFrame, idx: Mapping[date, float],
                 *, regime: Optional[Mapping[date, float]] = None) -> Tuple[List[Dict[str, Any]], Counter]:
    """(events, quality counters). `px` has columns date (datetime64), open, close, volume sorted by date; `idx` maps a date to the equal-weight index level;
    `regime` maps a date to 1.0/0.0 (index above its 200-day average) or is None."""
    q: Counter = Counter()
    facts = S.parse_companyfacts(companyfacts, cik)
    q["xbrl_facts"] = len(facts)
    sess = [d.date() for d in pd.to_datetime(px["date"])]
    o, c, v = (pd.to_numeric(px[k], errors="coerce").to_numpy(float) for k in ("open", "close", "volume"))
    eight_ks = [f for f in sub_facts if f.form_type == "8-K"]
    periodic = sorted((f for f in sub_facts if f.form_type in ("10-Q", "10-K")), key=lambda f: (f.accepted_at, f.accession))
    rows: List[Dict[str, Any]] = []
    for p in periodic:
        q["periodic_filings"] += 1
        if not T.filing_date_consistent(p):
            q["filing_date_inconsistent"] += 1
        qm = A.quarter_metrics(facts, p.accession, p.filing_date)
        if qm is None:
            q["no_matching_xbrl_facts"] += 1
            continue
        if qm.missing:
            q["missing_measure"] += 1
        if qm.metrics["revenue_prior_year"] is None:
            q["no_prior_year_comparison"] += 1
        rel = T.match_release(qm.period_end, p.filing_date, eight_ks)
        if rel is None:
            q["no_8k_release_matched"] += 1
        elif not T.filing_date_consistent(rel):
            q["filing_date_inconsistent"] += 1
        dec = rel or p
        rb = T.reaction_bar(sess, dec.accepted_at)
        if rb is None:
            q["no_reaction_bar"] += 1
            continue
        r, timing = rb
        if timing == "UNKNOWN":
            q["timing_unknown_non_session_day"] += 1
        if r < 61 or r + 22 >= len(c):
            q["outside_price_history"] += 1
            continue
        dv = float(np.nanmean(c[r - 20:r] * v[r - 20:r]))
        if not (np.isfinite(dv) and dv >= MIN_DOLLAR_VOLUME_20 and c[r - 1] >= MIN_PRICE):
            q["illiquid_or_penny"] += 1
            continue
        w = c[r - 1:r + 23]
        ratios = w[1:] / w[:-1]
        if not np.isfinite(ratios).all() or (ratios > 3).any() or (ratios < 1 / 3).any():
            q["price_discontinuity"] += 1
            continue

        def mk(i: int) -> float:
            return idx.get(sess[i], float("nan"))
        react = c[r] / c[r - 1] - 1
        prev_gap = o[r - 1] / c[r - 2] - 1                                    # placebo: the bar BEFORE the reaction bar
        prev_ar_react = (c[r - 1] / c[r - 2] - 1) - (mk(r - 1) / mk(r - 2) - 1)
        ar_react = react - (mk(r) / mk(r - 1) - 1)
        entry = o[r + 1]
        ar5 = (c[r + 5] / entry - 1) - (mk(r + 5) / mk(r) - 1)
        ar20 = (c[r + 20] / entry - 1) - (mk(r + 20) / mk(r) - 1)
        vol20 = float(np.nanstd(c[r - 20:r] / c[r - 21:r - 1] - 1))
        an = A.detect(qm)
        m = qm.metrics
        rows.append({
            "symbol": symbol, "cik": cik, "period_accession": p.accession, "form": p.form_type, "period_end": qm.period_end.isoformat(), "periodic_filed": p.filing_date.isoformat(),
            "periodic_accepted_at": p.accepted_at.isoformat(), "release_accession": rel.accession if rel else None, "release_accepted_at": rel.accepted_at.isoformat() if rel else None,
            "release_source": "8k_item_2_02" if rel else "periodic_filing", "decision_accepted_at": dec.accepted_at.isoformat(), "timing": timing,
            "reaction_date": sess[r].isoformat(), "source_url": filing_url(cik, dec.accession, dec.primary_document), "periodic_url": filing_url(cik, p.accession, p.primary_document),
            "anomaly_rules": list(an.rules) if an else [], "nature": an.nature if an else "none",
            "revenue_yoy": m["revenue_yoy"], "revenue_acceleration": m["revenue_acceleration"], "net_income_yoy": m["net_income_yoy"], "operating_income_yoy": m["operating_income_yoy"],
            "operating_margin_change": m["operating_margin_change"], "net_margin_change": m["net_margin_change"], "net_income_turnaround": m["net_income_turnaround"],
            "react": react, "prev_gap": prev_gap, "prev_ar_react": prev_ar_react, "gap": o[r] / c[r - 1] - 1, "ar_react": ar_react, "ar_d5": ar5, "ar_d20": ar20,
            "relvol": float(v[r] / np.nanmean(v[r - 20:r])) if np.nanmean(v[r - 20:r]) > 0 else float("nan"),
            "vol20": vol20, "ret20_pre": float(c[r - 1] / c[r - 21] - 1), "dollar_vol20": dv,
            "regime_up": (regime or {}).get(sess[r - 1], float("nan")), "year": sess[r].year,
        })
        q["events"] += 1
        if an:
            q["anomaly_events"] += 1
    return rows, q
