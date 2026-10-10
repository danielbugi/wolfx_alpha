"""The deterministic diagnostic report of one dataset build (pure).

Same manifest + same inputs => the same report document, byte for byte, hence the same `report_hash`. There is no timestamp, host, path or
random value in it; wall-clock and run identity belong to the registry rows that reference it. Missing or insufficient information is
reported as such (status + sample size), never as a numeric zero.
"""
from __future__ import annotations

import math
from typing import Any, Dict, List, Mapping, Sequence

from research.lab import dataset_baselines as B
from research.lab import dataset_contract as C
from research.lab import dataset_assemble as A
from research.lab import manifest as M
from research.lab.dataset_audit import Audit
from research.lab.manifest import canonical_hash

REPORT_SCHEMA = "lab_dataset_report_v1"
KEY_COLUMNS = ("observation_key", "strategy_key", "strategy_version", "symbol", "t0_session", "direction", "horizon_sessions",
               "horizon_session", "split")
STATIC_PIT_LIMITS = (
    "Availability stamps of candidate, feature, market, sector and universe snapshots are writer-settable (DEFAULT NOW()), not database-enforced.",
    "A mask removes values that arrived after the decision deadline; it cannot recover what would have been known at an intraday decision time.",
    "Labels are fwd_v1 directional simple returns on stored prices: they inherit the price history's own restatement risk (basis_adjusted rows are labelled).",
    "Sector and relative-strength context outside a live capture window is reconstructed from today's sector map and is never point-in-time safe.",
    "First-seen values are the first value this system observed, not necessarily the first value the source published.",
    "The universe is the manifest's own explicit list; survivorship of that list is the caller's responsibility and is not checked here.",
)


def _quantiles(v: Sequence[float], min_sample: int) -> Dict[str, Any]:
    n = len(v)
    if n < min_sample:
        return {"n": n, "status": "insufficient_sample", "min_sample": min_sample}
    s = sorted(v)
    def q(p: float) -> float:
        k = p * (n - 1)
        lo, hi = int(math.floor(k)), int(math.ceil(k))
        return B._r(s[lo] + (s[hi] - s[lo]) * (k - lo))
    return {"n": n, "status": "ok", "min": B._r(s[0]), "p05": q(0.05), "p25": q(0.25), "p50": q(0.5), "p75": q(0.75), "p95": q(0.95),
            "max": B._r(s[-1])}


def _walk_insufficient(o: Any, path: str, out: List[Dict[str, Any]]) -> None:
    if isinstance(o, Mapping):
        if o.get("status") == "insufficient_sample":
            out.append({"path": path, "n": o.get("n", [o.get("n_a"), o.get("n_b")]), "min_sample": o.get("min_sample")})
            return
        for k in sorted(o):
            _walk_insufficient(o[k], f"{path}.{k}" if path else str(k), out)


def build_report(manifest: M.Manifest, cfg: C.DatasetConfig, rows: Sequence[Mapping[str, Any]], dataset_hash: str, audit: Audit,
                 baselines: Mapping[str, Any]) -> Dict[str, Any]:
    d = manifest.document
    ms = cfg.min_sample
    n = len(rows)
    miss = {c: {"n_null": sum(1 for r in rows if r[c] is None), "n_rows": n} for c, _ in C.COLUMNS if c not in KEY_COLUMNS}
    outcome = {}
    for h in d["label_horizons"]:
        outcome[str(h)] = {}
        for s in M.SPLITS:
            sel = [r for r in rows if r["horizon_sessions"] == h and r["split"] == s]
            hist: Dict[str, int] = {}
            for r in sel:
                hist[str(r["label_status"])] = hist.get(str(r["label_status"]), 0) + 1
            outcome[str(h)][s] = {"n_rows": len(sel), "label_status": dict(sorted(hist.items()))}
    prim = [r for r in rows if r["horizon_sessions"] == cfg.primary_horizon]
    dist = {}
    for s in M.SPLITS:
        sel = [r for r in prim if r["split"] == s and r["label_status"] == "final" and r["directional_return"] is not None]
        dist[s] = {"directional_return": _quantiles([float(r["directional_return"]) for r in sel], ms)}
    dist["benchmark_state"] = {s: B._hist_of(prim, s, "benchmark_state", True) for s in M.SPLITS}
    dist["path_state"] = {s: B._hist_of(prim, s, "path_state", True) for s in M.SPLITS}
    dist["data_quality"] = {s: B._hist_of(prim, s, "data_quality", True) for s in M.SPLITS}
    warns: List[Dict[str, Any]] = []
    _walk_insufficient(baselines["baselines"], "baselines", warns)
    _walk_insufficient(dist, "label_distribution", warns)
    report: Dict[str, Any] = {
        "schema": REPORT_SCHEMA,
        "identity": {"manifest_hash": manifest.manifest_hash, "dataset_hash": dataset_hash, "code_sha": d["code_sha"],
                     "dataset_name": d["dataset_name"], "dataset_version": d["dataset_version"], "dataset_schema": C.DATASET_SCHEMA,
                     "contract_hash": C.schema_hash(), "baseline_version": baselines["version"], "audit_hash": audit.audit_hash},
        "methodology": {"label_version": d["label_version"], "label_methodology_version": d["label_methodology_version"],
                        "label_horizons": list(d["label_horizons"]), "primary_horizon": cfg.primary_horizon,
                        "feature_versions": dict(d["feature_versions"]), "strategy": {"key": cfg.strategy_key, "version": cfg.strategy_version},
                        "reconstructed_policy": cfg.reconstructed_policy, "availability_grace_days": cfg.availability_grace_days,
                        "min_sample": ms},
        "universe": {"universe_id": d["universe_id"], "universe_hash": d["universe_hash"], "rule": cfg.universe_rule,
                     "n_members": len(cfg.universe_members)},
        "time": {"knowledge_cutoff_at": d["knowledge_cutoff_at"], "label_maturity_session": d["label_maturity_session"],
                 "windows": d["windows"], "embargo_sessions": d["embargo_sessions"], "purge_sessions": d["purge_sessions"],
                 "calendar_source": d["calendar_source"], "calendar_hash": d["calendar_hash"], "calendar_span": d["calendar_span"]},
        "row_counts": audit.document["counts"],
        "missingness": miss,
        "outcome_distribution": outcome,
        "label_distribution": dist,
        "leakage_audit": audit.document,
        "baselines": dict(baselines),
        "known_pit_limitations": list(STATIC_PIT_LIMITS) + [x["note"] for x in audit.document["limitations"]],
        "insufficient_sample_warnings": warns,
    }
    report["report_hash"] = canonical_hash(report)
    return report


def verify_report_hash(report: Mapping[str, Any]) -> bool:
    body = {k: v for k, v in report.items() if k != "report_hash"}
    return canonical_hash(body) == report.get("report_hash")


def render_text(report: Mapping[str, Any]) -> str:
    i, t, m = report["identity"], report["time"], report["methodology"]
    out = [f"DATASET DIAGNOSTIC REPORT  ({report['schema']})", "",
           f"dataset   {i['dataset_name']} {i['dataset_version']}", f"manifest  {i['manifest_hash']}", f"dataset   {i['dataset_hash']}",
           f"report    {report['report_hash']}", f"audit     {i['audit_hash']}", f"code sha  {i['code_sha']}", "",
           f"label {m['label_version']} / {m['label_methodology_version']}  horizons {m['label_horizons']}  primary {m['primary_horizon']}",
           f"features  {m['feature_versions']}", f"strategy  {m['strategy']['key']} {m['strategy']['version']}",
           f"universe  {report['universe']['universe_id']}  ({report['universe']['n_members']} symbols, rule {report['universe']['rule']})",
           f"cutoff    {t['knowledge_cutoff_at']}   maturity session {t['label_maturity_session']}",
           f"windows   train {t['windows']['train']}  validation {t['windows']['validation']}  test {t['windows']['test']}",
           f"embargo {t['embargo_sessions']} sessions, purge {t['purge_sessions']}   calendar {t['calendar_source']} {t['calendar_span']}", "",
           report["leakage_audit"]["verdict"] and f"LEAKAGE AUDIT: {report['leakage_audit']['verdict']}"]
    c = report["row_counts"]
    out += [f"rows {c['rows_final']} (train {c['rows_by_split']['train']}, validation {c['rows_by_split']['validation']}, "
            f"test {c['rows_by_split']['test']}); purged {c['rows_purged']}; embargoed {c['rows_embargoed']}", "",
            "BASELINES (diagnostic only)", "  " + report["baselines"]["disclaimer"], f"  comparisons reported: {report['baselines']['n_comparisons']}", ""]
    for name, bl in report["baselines"]["baselines"].items():
        out.append(f"  [{name}] {bl['population']}")
        for split in M.SPLITS:
            if name == "null_unconditional":
                blocks = {"all": bl["by_split"][split]}
            else:
                blocks = bl["strata"].get(split, {})
            if blocks.get("status") == "withheld":
                out.append(f"    {split}: withheld")
                continue
            for lab, b in blocks.items():
                if b.get("status") == "withheld":
                    out.append(f"    {split}: withheld")
                    continue
                r = b["directional_return"]
                num = (f"mean {r['mean']:+.4%} hit {r['hit_rate']:.1%} (n {r['n']})" if r["status"] == "ok"
                       else f"insufficient sample (n {r['n']} < {r['min_sample']})")
                out.append(f"    {split:<10} {lab:<24} rows {b['n_rows']:>6} final {b['n_final']:>6}  {num}")
    out += ["", "KNOWN POINT-IN-TIME LIMITATIONS"] + [f"  - {x}" for x in report["known_pit_limitations"]]
    if report["insufficient_sample_warnings"]:
        out += ["", "INSUFFICIENT SAMPLE"] + [f"  - {w['path']} (n={w['n']}, needs {w['min_sample']})" for w in report["insufficient_sample_warnings"]]
    return "\n".join(str(x) for x in out) + "\n"
