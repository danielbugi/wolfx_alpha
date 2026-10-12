"""Small, REAL, end-to-end SEC earnings-catalyst experiment (isolated research run; read-only database session; the only network access is to sec.gov).

Chain: SEC submissions (filing identity + acceptance timestamp, existing EDGAR collector parser) + XBRL company facts (verbatim facts, point-in-time series) -> anomaly rules and
recurring-vs-one-time classification -> decision time (8-K Item 2.02 acceptance) -> reaction bar by session timing -> market-adjusted reaction, drift and relative volume -> combination with
Donchian breakouts, exit policies, market regime and relative volume -> chronological ML evaluation. Writes /out/sec_earnings_study.json and /out/sec_raw_manifest.json.

Requires the environment variable SEC_USER_AGENT = "<operator name> <real contact email>" (the SEC's fair-access policy; the existing EdgarConfig refuses placeholders). It is never printed or
stored. Raw SEC payloads are cached under /out/sec_cache (not committed) so a re-run makes no further requests.
"""
import gzip
import hashlib
import json
import os
import sys
import time
import urllib.error
import urllib.request
import warnings
from collections import Counter, defaultdict
from datetime import date, datetime, timezone

import numpy as np
import pandas as pd
import xgboost as xgb
from sklearn.metrics import roc_auc_score

warnings.filterwarnings("ignore")
sys.path[:0] = ["/app", "/app/mechanism", "/exp"]
from market_intelligence import edgar_collector as E  # noqa: E402
from market_intelligence import filing_contract as F  # noqa: E402
from ml_training.features import price_features as pf  # noqa: E402
from research import exit_policies as X  # noqa: E402
import _common as C  # noqa: E402
import sec_earnings_pipeline as P  # noqa: E402

T0 = time.time()
N_SYMBOLS = int(os.environ.get("SEC_N_SYMBOLS", "400"))
SINCE = date.fromisoformat(os.environ.get("SEC_SINCE", "2014-01-01"))
OUT, CACHE = "/out", "/out/sec_cache"
os.makedirs(CACHE, exist_ok=True)


def log(*a):
    print(f"[{time.time() - T0:5.0f}s]", *a, flush=True)


class Transport:
    """sec.gov access: rate-limited (existing RateLimiter), cached on disk, every payload hashed into a manifest. get_json also satisfies the collector's Transport protocol."""

    def __init__(self, user_agent, rps):
        self.ua, self.limiter, self.manifest, self.cached_hits, self.requests = user_agent, E.RateLimiter(rps, time.monotonic, time.sleep), [], 0, 0

    def get_json(self, url, headers=None):
        key = hashlib.sha256(url.encode()).hexdigest()
        path = os.path.join(CACHE, key + ".json")
        if os.path.exists(path):
            raw = open(path, "rb").read()
            self.cached_hits += 1
        else:
            raw = self._fetch(url)
            open(path, "wb").write(raw)
        self.manifest.append({"url": url, "sha256": hashlib.sha256(raw).hexdigest(), "bytes": len(raw), "at": datetime.now(timezone.utc).isoformat(timespec="seconds")})
        return json.loads(raw)

    def _fetch(self, url):
        for attempt in range(4):
            self.limiter.wait()
            self.requests += 1
            req = urllib.request.Request(url, headers={"User-Agent": self.ua, "Accept-Encoding": "gzip", "Accept": "application/json"})
            try:
                with urllib.request.urlopen(req, timeout=60) as r:
                    b = r.read()
                    return gzip.decompress(b) if r.headers.get("Content-Encoding") == "gzip" else b
            except urllib.error.HTTPError as e:
                if e.code in (429, 500, 502, 503, 504) and attempt < 3:
                    time.sleep(2 ** attempt * 2)
                    continue
                raise
        raise RuntimeError("unreachable")


ua = os.environ.get("SEC_USER_AGENT", "")
if not ua:
    print("SEC_USER_AGENT is required: '<operator name> <real contact email>' (SEC fair-access policy).", file=sys.stderr)
    sys.exit(2)
cfg = E.EdgarConfig(user_agent=ua, max_rps=5.0)            # raises on a placeholder / missing contact
tr = Transport(cfg.user_agent, cfg.max_rps)
del ua

conn = C.connect()
mk = C.market_panel(conn)
idx = {d.date(): float(v) for d, v in mk["idx"].items()}
regime = {d.date(): float(v) for d, v in mk["idx_above_200"].items() if v == v}
syms = [r[0] for r in pd.read_sql(
    "SELECT d.symbol FROM (SELECT symbol FROM ml_breakout_dataset_v2 GROUP BY 1 HAVING count(*) >= 20) d JOIN (SELECT symbol FROM stock_prices WHERE date >= current_date - 400 "
    "GROUP BY 1 HAVING avg(close * volume) >= 5e6) p USING (symbol) ORDER BY md5(d.symbol) LIMIT %s", conn, params=(N_SYMBOLS,)).itertuples(index=False)]
log("symbols selected", len(syms))

tickers = tr.get_json("https://www.sec.gov/files/company_tickers.json")
t2c = {str(v["ticker"]).replace("-", ".").upper(): int(v["cik_str"]) for v in tickers.values()}
now = datetime.now(timezone.utc)
quality, per_symbol, reject_reasons, record_counts = Counter(), {}, Counter(), Counter()
events, breakout_rows, errors = [], [], {}
disc = pd.read_sql("SELECT symbol, date FROM price_discontinuities", conn)
disc_by = defaultdict(list)
for s_, d_ in zip(disc.symbol, pd.to_datetime(disc.date)):
    disc_by[s_].append(np.datetime64(d_))
bo_all = pd.read_sql("SELECT symbol, date, direction, plan_r FROM ml_breakout_dataset_v2 WHERE feature_set_version = %s AND date >= %s ORDER BY symbol, date", conn, params=(pf.FEATURE_SET_VERSION, SINCE))
bo_all["date"] = pd.to_datetime(bo_all["date"])
POL = list(X.POLICIES)

for n, sym in enumerate(syms):
    cik = t2c.get(sym.upper())
    if cik is None:
        quality["symbol_not_in_sec_ticker_file"] += 1
        continue
    try:
        link = F.IdentifierLink(cik, sym, None, None, "reconstructed")      # today's SEC ticker file projected back: stated, not upgraded
        sub = tr.get_json(E.submissions_url(cik))
        forms = frozenset({"8-K", "10-Q", "10-K"})
        facts, rej, cnt = E.parse_submissions(sub, cik, now=now, forms=forms, since=SINCE, links=[link])
        for pg in (sub.get("filings") or {}).get("files") or []:             # older pages: same columns, parsed by the same function
            if str(pg.get("filingTo", "")) >= SINCE.isoformat():
                page = tr.get_json(f"{E.BASE_URL}/submissions/{pg['name']}")
                f2, r2, c2 = E.parse_submissions({"cik": cik, "filings": {"recent": page}}, cik, now=now, forms=forms, since=SINCE, links=[link])
                facts += f2
                rej += r2
                cnt.update(c2)
        quality["rejected_filing_records"] += len(rej)
        reject_reasons.update(r.reason.split("(")[0].strip()[:90] for r in rej)
        record_counts.update({k: v for k, v in cnt.items() if k in ("records", "skipped_form", "skipped_before_since", "duplicate_identical", "already_known")})
        cf = tr.get_json(f"{E.BASE_URL}/api/xbrl/companyfacts/CIK{cik:010d}.json")
    except Exception as e:  # noqa: BLE001
        errors[sym] = f"{type(e).__name__}: {str(e)[:120]}"
        continue
    px = pd.read_sql("SELECT date, open, high, low, close, volume FROM stock_prices WHERE symbol = %s ORDER BY date", conn, params=(sym,)).dropna(subset=["close"]).drop_duplicates("date").reset_index(drop=True)
    px["date"] = pd.to_datetime(px["date"])
    if len(px) < 300:
        quality["short_price_history"] += 1
        continue
    rows, q = P.build_events(sym, cik, [c.fact for c in facts], cf, px, idx, regime=regime)
    quality.update(q)
    events += rows
    per_symbol[sym] = {"filings": len(facts), "events": len(rows)}
    # ---- breakouts of this symbol, linked to the most recent earnings event (reaction date strictly before the breakout, within 10 sessions), run through every exit policy
    ev_dates = sorted((pd.Timestamp(r["reaction_date"]), r["nature"]) for r in rows)
    ind = pf.compute_indicators(px)
    atr, o, h, l, c = ind["atr"].to_numpy(), px["open"].to_numpy(float), px["high"].to_numpy(float), px["low"].to_numpy(float), px["close"].to_numpy(float)
    vv = px["volume"].to_numpy(float)
    dates = px["date"].to_numpy()
    dd = np.array(disc_by.get(sym, []), dtype="datetime64[ns]")
    for b in bo_all[bo_all.symbol == sym].itertuples(index=False):
        i = int(np.searchsorted(dates, np.datetime64(b.date)))
        if i >= len(dates) or dates[i] != np.datetime64(b.date) or i < 61 or i + 62 >= len(dates):
            continue
        if len(dd) and ((dd >= dates[max(0, i - 5)]) & (dd <= dates[min(len(dates) - 1, i + 62)])).any():
            continue
        prior = [(d_, nat) for d_, nat in ev_dates if d_ < b.date and (b.date - d_).days <= 15]
        cat = prior[-1][1] if prior else "no_recent_earnings"
        cat = "earnings_no_anomaly" if cat == "none" else cat
        relvol = float(vv[i] / np.nanmean(vv[i - 20:i])) if np.nanmean(vv[i - 20:i]) > 0 else float("nan")
        unit = X.STOP_ATR * float(atr[i])
        for pol in POL:
            t = X.simulate(pol, o, h, l, c, i, b.direction, float(atr[i]), max_hold=X.DEFAULT_MAX_HOLD, cost_bps=0.0)
            if t is not None:
                breakout_rows.append((sym, b.date, int(b.direction), cat, pol, t.r_gross, t.entry_price / unit, float(b.plan_r) if b.plan_r == b.plan_r else np.nan, relvol, regime.get(pd.Timestamp(dates[i - 1]).date(), np.nan)))
    if n % 50 == 0:
        log("symbols", n, "events", len(events), "requests", tr.requests, "cached", tr.cached_hits)

log("pipeline done: events", len(events), "breakout sims", len(breakout_rows), "errors", len(errors), "sec requests", tr.requests)
E_ = pd.DataFrame(events)
E_["reaction_date"] = pd.to_datetime(E_["reaction_date"])
E_["month"] = E_["reaction_date"].dt.to_period("M").astype(str)
res = {"design": {"symbols_requested": len(syms), "symbols_with_events": len(per_symbol), "events": int(len(E_)), "since": SINCE.isoformat(), "first_event": str(E_.reaction_date.min().date()),
                  "last_event": str(E_.reaction_date.max().date()), "sec_requests": tr.requests, "cache_hits": tr.cached_hits, "fetch_errors": dict(list(errors.items())[:20]), "n_fetch_errors": len(errors),
                  "sources": ["sec.gov company_tickers.json (ticker->CIK, projected back: reconstructed basis)", "data.sec.gov submissions (filing identity + acceptance timestamps)", "data.sec.gov XBRL companyfacts (verbatim facts with filing dates)"],
                  "thresholds": {k: getattr(__import__("market_intelligence.financial_anomaly", fromlist=["x"]), k) for k in ("REV_YOY_MIN", "REV_ACCEL_MIN", "NI_YOY_MIN", "OP_MARGIN_UP_MIN")}}}

# ------------------------------------------------------------------ extraction quality, timestamp integrity, coverage
n_per = max(1, quality["periodic_filings"])
res["quality"] = {
    "periodic_filings_seen": int(quality["periodic_filings"]), "with_xbrl_facts": int(n_per - quality["no_matching_xbrl_facts"]), "share_with_xbrl_facts": round(1 - quality["no_matching_xbrl_facts"] / n_per, 4),
    "missing_a_measure": int(quality["missing_measure"]), "no_prior_year_comparison": int(quality["no_prior_year_comparison"]), "events_used": int(quality["events"]),
    "dropped": {k: int(quality[k]) for k in ("no_reaction_bar", "outside_price_history", "illiquid_or_penny", "price_discontinuity", "no_matching_xbrl_facts")},
    "release_8k_matched_share": round(float((E_.release_source == "8k_item_2_02").mean()), 4),
    "timestamp_integrity": {"filing_date_inconsistent_with_acceptance": int(quality["filing_date_inconsistent"]), "timing_unknown_non_session_day": int(quality["timing_unknown_non_session_day"]),
                            "rejected_filing_records_without_explicit_utc_acceptance_or_other_defect": int(quality["rejected_filing_records"])},
    "rejected_record_reasons": dict(reject_reasons.most_common(8)), "submission_record_counters": dict(record_counts),
    "timing_mix": E_.timing.value_counts().to_dict(), "events_by_year": {int(k): int(v) for k, v in E_.year.value_counts().sort_index().items()},
    "anomaly_counts": {"by_nature": E_.nature.value_counts().to_dict(), "by_rule": dict(Counter(r for rs in E_.anomaly_rules for r in rs))},
}


def blk(df, col):
    return C.block_ci(df[col].to_numpy(), df["month"].to_numpy())


def block(df):
    return {"n": int(len(df)), "ar_reaction": blk(df, "ar_react"), "ar_drift_5d": blk(df, "ar_d5"), "ar_drift_20d": blk(df, "ar_d20"), "mean_relvol": round(float(df.relvol.mean()), 2) if len(df) else None}


res["reaction_by_nature"] = {k: block(g) for k, g in E_.groupby("nature")}
an = E_[E_.nature != "none"]
res["reaction_by_nature_and_timing"] = {f"{k[0]}|{k[1]}": block(g) for k, g in an.groupby(["nature", "timing"]) if len(g) >= 40}
res["relative_volume_split_anomalies"] = {"relvol_ge_2": block(an[an.relvol >= 2]), "relvol_lt_2": block(an[an.relvol < 2])}
res["regime_split_anomalies"] = {"index_uptrend": block(an[an.regime_up == 1]), "index_downtrend": block(an[an.regime_up == 0])}
res["stability_by_period"] = {nm: {k: block(g) for k, g in E_[sel].groupby("nature")} for nm, sel in (("2014_2021", E_.year <= 2021), ("2022_plus", E_.year >= 2022))}
res["by_year_recurring_operating"] = {int(y): {"n": int(len(g)), "mean_ar_react": round(float(g.ar_react.mean()), 4), "mean_ar_d20": round(float(g.ar_d20.mean()), 4)} for y, g in E_[E_.nature == "recurring_operating"].groupby("year")}
log("event statistics done")

# ------------------------------------------------------------------ catalysts x breakouts x exit policies
B = pd.DataFrame(breakout_rows, columns=["symbol", "date", "direction", "catalyst", "policy", "r_gross", "px_per_r", "plan_r", "relvol", "regime_up"])
B["month"] = B["date"].dt.to_period("M").astype(str)
B["r10"] = B["r_gross"] - 0.001 * B["px_per_r"]
base = B[B.policy == "A_atr_stop_fixed_3R"]
res["breakouts_by_catalyst"] = {}
for dn, nm in ((1, "bullish"), (-1, "bearish")):
    g = base[base.direction == dn]
    res["breakouts_by_catalyst"][nm] = {k: {"n": int(len(x)), "plan_r": C.block_ci(x.plan_r.dropna().to_numpy(), x.loc[x.plan_r.notna(), "month"].to_numpy()), "fixed_3R_net10": C.block_ci(x.r10.to_numpy(), x.month.to_numpy())} for k, x in g.groupby("catalyst")}
res["exit_policy_by_catalyst_net10"] = {}
for dn, nm in ((1, "bullish"), (-1, "bearish")):
    res["exit_policy_by_catalyst_net10"][nm] = {}
    for cat, x in B[B.direction == dn].groupby("catalyst"):
        if x.symbol.nunique() < 5 or len(x) < 5 * len(POL) * 10:
            continue
        res["exit_policy_by_catalyst_net10"][nm][cat] = {p: {"n": int(len(y)), **C.block_ci(y.r10.to_numpy(), y.month.to_numpy())} for p, y in x.groupby("policy")}
cm = base[(base.direction == 1)].copy()
cm["recurring"] = cm.catalyst == "recurring_operating"
cm["rv"] = cm.relvol >= 1.5
cm["up"] = cm.regime_up == 1
res["bullish_combinations_fixed3R_net10"] = {
    "all": C.block_ci(cm.r10.to_numpy(), cm.month.to_numpy()),
    "recurring_catalyst": C.block_ci(cm[cm.recurring].r10.to_numpy(), cm[cm.recurring].month.to_numpy()),
    "recurring_catalyst_and_relvol_ge_1_5": C.block_ci(cm[cm.recurring & cm.rv].r10.to_numpy(), cm[cm.recurring & cm.rv].month.to_numpy()),
    "recurring_catalyst_and_relvol_and_index_uptrend": C.block_ci(cm[cm.recurring & cm.rv & cm.up].r10.to_numpy(), cm[cm.recurring & cm.rv & cm.up].month.to_numpy()),
    "no_catalyst_relvol_ge_1_5": C.block_ci(cm[(cm.catalyst == "no_recent_earnings") & cm.rv].r10.to_numpy(), cm[(cm.catalyst == "no_recent_earnings") & cm.rv].month.to_numpy()),
}
log("breakout combinations done")

# ------------------------------------------------------------------ ML (chronological; baselines)
split, emb = pd.Timestamp("2022-01-01"), pd.Timestamp("2022-02-15")
feats = ["revenue_yoy", "revenue_acceleration", "net_income_yoy", "operating_income_yoy", "operating_margin_change", "net_margin_change", "vol20", "ret20_pre", "dollar_vol20"]
D_ = E_.copy()
for f_ in feats:
    D_[f_] = pd.to_numeric(D_[f_], errors="coerce")
D_["timing_amc"] = (D_.timing == "AMC").astype(float)
D_["regime"] = D_["regime_up"]
fs = feats + ["timing_amc", "regime"]
tr_, te_ = D_[D_.reaction_date < split], D_[D_.reaction_date >= emb]
thr = tr_["ar_react"].abs().quantile(0.9)
PAR = dict(n_estimators=250, max_depth=3, learning_rate=0.05, subsample=0.8, colsample_bytree=0.8, min_child_weight=50, reg_lambda=5.0, tree_method="hist", random_state=42, n_jobs=2)
ml = {"split": {"train_until": "2022-01-01", "test_from": "2022-02-15", "n_train": int(len(tr_)), "n_test": int(len(te_)), "big_move_threshold": round(float(thr), 4)}}
y1tr, y1te = (tr_["ar_react"].abs() >= thr).astype(int), (te_["ar_react"].abs() >= thr).astype(int)
m1 = xgb.XGBClassifier(**PAR).fit(tr_[fs], y1tr)
ml["event_significance"] = {"auc_model": round(float(roc_auc_score(y1te, m1.predict_proba(te_[fs])[:, 1])), 4), "auc_baseline_trailing_volatility": round(float(roc_auc_score(y1te, te_["vol20"])), 4),
                            "auc_baseline_abs_revenue_yoy": round(float(roc_auc_score(y1te, te_["revenue_yoy"].abs().fillna(0))), 4), "base_rate_test": round(float(y1te.mean()), 4)}
y2tr, y2te = (tr_["ar_react"] > 0).astype(int), (te_["ar_react"] > 0).astype(int)
m2 = xgb.XGBClassifier(**PAR).fit(tr_[fs], y2tr)
ml["reaction_direction"] = {"auc_model": round(float(roc_auc_score(y2te, m2.predict_proba(te_[fs])[:, 1])), 4), "auc_baseline_revenue_yoy": round(float(roc_auc_score(y2te, te_["revenue_yoy"].fillna(0))), 4),
                            "auc_baseline_operating_margin_change": round(float(roc_auc_score(y2te, te_["operating_margin_change"].fillna(0))), 4), "base_rate_test": round(float(y2te.mean()), 4)}
res["ml"] = ml

# ------------------------------------------------------------------ out-of-sample (chronological) tests of the combinations and of exit-policy selection
# Rules are fixed here, before the test period is looked at: train = signal dates before 2022-01-01, test = from 2022-02-15 (the same split as the ML above).
# Cells are NOT searched: the five combinations below are the ones the owner asked about; a cell that wins in train is not selected from a larger menu.
def side(x):
    trn, tst = x[x.date < split], x[x.date >= emb]
    return {"train": {"n": int(len(trn)), **C.block_ci(trn.r10.to_numpy(), trn.month.to_numpy())}, "test": {"n": int(len(tst)), **C.block_ci(tst.r10.to_numpy(), tst.month.to_numpy())}}


cells = {"all_bullish": cm, "recurring_catalyst": cm[cm.recurring], "recurring_catalyst_and_relvol_ge_1_5": cm[cm.recurring & cm.rv],
         "recurring_catalyst_and_relvol_and_index_uptrend": cm[cm.recurring & cm.rv & cm.up], "no_catalyst_relvol_ge_1_5": cm[(cm.catalyst == "no_recent_earnings") & cm.rv]}
res["bullish_combinations_chronological"] = {k: side(v) for k, v in cells.items()}
sel = {}
for cat in ("recurring_operating", "no_recent_earnings"):
    x = B[(B.direction == 1) & (B.catalyst == cat)]
    tr_x, te_x = x[x.date < split], x[x.date >= emb]
    means = tr_x.groupby("policy").r10.mean()
    best = str(means.idxmax())
    te_best, te_fixed = te_x[te_x.policy == best], te_x[te_x.policy == "A_atr_stop_fixed_3R"]
    sel[cat] = {"best_policy_on_train": best, "train_mean_by_policy": {k: round(float(v), 4) for k, v in means.items()},
                "test_best_policy": {"n": int(len(te_best)), **C.block_ci(te_best.r10.to_numpy(), te_best.month.to_numpy())},
                "test_fixed_3R": {"n": int(len(te_fixed)), **C.block_ci(te_fixed.r10.to_numpy(), te_fixed.month.to_numpy())}}
res["exit_policy_selection_out_of_sample"] = sel

# ------------------------------------------------------------------ the ML-ready outcome table: one row per event, point-in-time features + realised outcomes + identities; hash recorded
OUTCOME_COLS = ["symbol", "cik", "form", "period_end", "period_accession", "release_accession", "release_source", "decision_accepted_at", "timing", "reaction_date", "nature", "anomaly_rules",
                "revenue_yoy", "revenue_acceleration", "net_income_yoy", "operating_income_yoy", "operating_margin_change", "net_margin_change", "vol20", "ret20_pre", "dollar_vol20", "relvol",
                "regime_up", "ar_react", "ar_d5", "ar_d20", "source_url"]
O_ = E_[[c_ for c_ in OUTCOME_COLS if c_ in E_.columns]].copy()
O_["anomaly_rules"] = O_["anomaly_rules"].apply(lambda r: ";".join(r) if isinstance(r, (list, tuple, set)) else str(r))
O_["big_move"] = (O_["ar_react"].abs() >= thr).astype(int)
O_["split"] = np.where(pd.to_datetime(O_["reaction_date"]) < split, "train", np.where(pd.to_datetime(O_["reaction_date"]) >= emb, "test", "embargo"))
O_ = O_.sort_values(["reaction_date", "symbol", "period_accession"]).reset_index(drop=True)
csv_bytes = O_.to_csv(index=False, float_format="%.6g", lineterminator="\n").encode("utf-8")
import gzip as _gz
with open(f"{OUT}/sec_event_outcomes.csv.gz", "wb") as fh_:
    fh_.write(_gz.compress(csv_bytes, 9, mtime=0))
res["outcome_table"] = {"file": "sec_event_outcomes.csv.gz", "rows": int(len(O_)), "columns": list(O_.columns), "sha256_of_csv": __import__("hashlib").sha256(csv_bytes).hexdigest(),
                        "big_move_threshold_abs_market_adjusted_reaction": round(float(thr), 4), "split_counts": O_["split"].value_counts().to_dict(),
                        "label_note": "labels are realised outcomes on the reaction session (ar_react), +5 and +20 sessions after it (ar_d5, ar_d20); features are known at the release acceptance time"}
# ------------------------------------------------------------------ 2026-10-12 follow-up: walk-forward by year, timestamp placebo, variant-keyed outcomes, breakout-level ML table
# (a) Walk-forward by calendar year. Rule fixed in advance: a cell "beats the plain bullish breakout" in a year when both have >= 30 trades that year and its mean net R is higher.
def yearly(x):
    return {int(y): {"n": int(len(g)), "mean": round(float(g.r10.mean()), 4)} for y, g in x.groupby(x.date.dt.year)}


cells_y = {k: yearly(v) for k, v in cells.items()}
wins = {}
for k in cells:
    ys = [y for y in sorted(cells_y[k]) if cells_y[k][y]["n"] >= 30 and cells_y["all_bullish"].get(y, {}).get("n", 0) >= 30]
    wins[k] = {"years_evaluable": len(ys), "years_better_than_plain": int(sum(cells_y[k][y]["mean"] > cells_y["all_bullish"][y]["mean"] for y in ys)), "years": ys}
res["bullish_combinations_by_year"] = {"cells": cells_y, "wins_vs_plain": wins, "rule": "a year counts when the cell and the plain bullish breakout both have >= 30 trades; better = higher mean net R (fixed 3R, 10 bps)"}

# (b) Timestamp placebo. If the acceptance-time -> reaction-bar mapping is right, the information gap lands on the reaction bar and NOT on the bar before it.
tq = {}
for t_, g in E_.groupby("timing"):
    if len(g) < 30:
        continue
    tq[t_] = {"n": int(len(g)), "mean_abs_gap_on_reaction_bar": round(float(g.gap.abs().mean()), 4), "mean_abs_gap_on_bar_before": round(float(g.prev_gap.abs().mean()), 4),
              "mean_abs_ar_on_reaction_bar": round(float(g.ar_react.abs().mean()), 4), "mean_abs_ar_on_bar_before": round(float(g.prev_ar_react.abs().mean()), 4)}
lag = (pd.to_datetime(E_.periodic_filed) - pd.to_datetime(E_.release_accepted_at, utc=True).dt.tz_localize(None).dt.normalize()).dt.days[E_.release_source == "8k_item_2_02"]
res["timing_quality"] = {"by_timing_class": tq, "release_to_periodic_filing_lag_days": {"n": int(lag.notna().sum()), "p10": float(lag.quantile(0.1)), "median": float(lag.median()), "p90": float(lag.quantile(0.9)),
                                                                                       "share_released_same_day_or_before_periodic": round(float((lag >= 0).mean()), 4)},
                         "reading": "a correct mapping puts the large absolute gap on the reaction bar and a baseline-sized one on the bar before it (placebo); INTRADAY releases show their move in the bar's own range, not the gap"}

# (c) Strategy-specific outcome tracking, keyed by the registry identities (key, version, definition hash) so a result can be traced to one exact definition.
from research import strategy_variants as SV  # noqa: E402
spec_by_policy = {n_: s_ for n_, s_ in zip(list(X.POLICIES), SV.build_specs())}
vo = {}
for pol, g in B.groupby("policy"):
    sp = spec_by_policy[pol]
    vo[sp.key] = {"version": sp.version, "definition_hash": sp.definition_hash, "policy": pol, "net_bps_per_side_round_trip": 10,
                  "bullish": {"all": {"n": int((g.direction == 1).sum()), **C.block_ci(g[g.direction == 1].r10.to_numpy(), g[g.direction == 1].month.to_numpy())},
                              "by_year": yearly(g[g.direction == 1])},
                  "bearish": {"all": {"n": int((g.direction == -1).sum()), **C.block_ci(g[g.direction == -1].r10.to_numpy(), g[g.direction == -1].month.to_numpy())},
                              "by_year": yearly(g[g.direction == -1])},
                  "bullish_after_recurring_operating": {"n": int(((g.direction == 1) & (g.catalyst == "recurring_operating")).sum()),
                                                        **C.block_ci(g[(g.direction == 1) & (g.catalyst == "recurring_operating")].r10.to_numpy(), g[(g.direction == 1) & (g.catalyst == "recurring_operating")].month.to_numpy())}}
res["strategy_variant_outcomes"] = vo

# (d) Breakout-level ML-ready table: one row per (symbol, signal date, direction); features known at the signal close, labels = net R under each registered exit policy.
attrs = B[B.policy == "A_atr_stop_fixed_3R"][["symbol", "date", "direction", "catalyst", "plan_r", "relvol", "regime_up", "px_per_r"]]
lab = B.pivot_table(index=["symbol", "date", "direction"], columns="policy", values="r10", aggfunc="first")
lab.columns = ["r_net10__" + spec_by_policy[c_].key for c_ in lab.columns]
wide = attrs.merge(lab.reset_index(), on=["symbol", "date", "direction"], how="left")            # a merge, not a pivot over the attributes: a NaN relvol / plan_r must not drop the breakout
wide["split"] = np.where(wide["date"] < split, "train", np.where(wide["date"] >= emb, "test", "embargo"))
wide = wide.sort_values(["date", "symbol", "direction"]).reset_index(drop=True)
bo_bytes = wide.to_csv(index=False, float_format="%.5g", lineterminator="\n", date_format="%Y-%m-%d").encode("utf-8")
with open(f"{OUT}/sec_breakout_outcomes.csv.gz", "wb") as fh_:
    fh_.write(_gz.compress(bo_bytes, 9, mtime=0))
res["breakout_outcome_table"] = {"file": "sec_breakout_outcomes.csv.gz", "rows": int(len(wide)), "columns": list(wide.columns), "sha256_of_csv": __import__("hashlib").sha256(bo_bytes).hexdigest(),
                                 "split_counts": wide["split"].value_counts().to_dict(),
                                 "label_note": "r_net10__<variant key> = realised R net of 10 bps round trip under that registered exit policy; catalyst = class of the most recent earnings event within 15 days before the signal"}
log("follow-up sections done")
res["evidence_events_sample"] = [{k: r[k] for k in ("symbol", "cik", "period_accession", "form", "period_end", "release_accession", "release_source", "decision_accepted_at", "timing", "reaction_date", "nature", "anomaly_rules",
                                                    "revenue_yoy", "revenue_acceleration", "operating_margin_change", "net_income_yoy", "relvol", "ar_react", "ar_d20", "source_url", "periodic_url")}
                                 for r in sorted((r for r in events if r["nature"] != "none"), key=lambda r: r["reaction_date"], reverse=True)[:40]]
json.dump(res, open(f"{OUT}/sec_earnings_study.json", "w"), indent=1, default=str)
json.dump({"requests": tr.requests, "cache_hits": tr.cached_hits, "payloads": tr.manifest}, open(f"{OUT}/sec_raw_manifest.json", "w"))
log("WROTE sec_earnings_study.json and sec_raw_manifest.json")
