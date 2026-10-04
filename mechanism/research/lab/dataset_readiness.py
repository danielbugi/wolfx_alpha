"""Dataset readiness / provenance summary (pure: no database, clock, file or environment access; deterministic).

Answers, for one BUILT dataset, the question the owner has to ask before any model work is even discussed: *what is this dataset made of, and
may it be used for model research?* It reads only what the build already produced (rows, the independent audit, the input verification,
the code check) and the manifest; it recomputes nothing the contract owns.

`model_research_eligible` fails CLOSED: it is True only when every check below passes, and any missing / malformed input makes the
corresponding check fail. What it does NOT mean is the point of the module -- the document carries, verbatim, the distinction

    reproducible  !=  point-in-time correct  !=  vendor correct  !=  predictive edge

A dataset that builds, audits clean and reproduces bit-for-bit is only *reproducible*. Eligibility here means "the dataset's own hygiene
does not block starting research on it" -- never that it contains an edge, and never that a reconstructed history was anything but a
reconstruction. The thresholds are module constants on purpose: they are not CLI options, so a run cannot loosen them.
"""
from __future__ import annotations

from typing import Any, Dict, List, Mapping, Optional, Sequence

from research.lab import dataset_contract as C
from research.lab import manifest as M
from research.lab.manifest import canonical_hash

READINESS_SCHEMA = "lab_dataset_readiness_v1"
DISTINCTION = "reproducible != point-in-time correct != vendor correct != predictive edge"

# Declared, fixed, and printed in every readiness document (not options).
MIN_OBSERVED_COVERAGE = 0.90          # share of primary-horizon rows whose context value is an OBSERVED, in-time value
MAX_MISSING_LABEL_FRACTION = 0.05     # share of primary-horizon train+validation rows with no label at all
MIN_FINAL_LABELS = {"train": 300, "validation": 100, "test": 100}   # final-labelled primary-horizon rows per split (and never below min_sample)
THRESHOLDS = {"min_observed_coverage": MIN_OBSERVED_COVERAGE, "max_missing_label_fraction": MAX_MISSING_LABEL_FRACTION,
              "min_final_labels": dict(MIN_FINAL_LABELS)}

# context sources whose history can be reconstructed: (state column, provenance column)
PROVENANCE_SOURCES = {"market": ("market__state", "market__provenance"), "sector": ("sector__state", "sector__provenance"),
                      "stock_rs": ("rs__state", "rs__provenance")}
# sources with no provenance column: (state column, states that count as a covered, database-stamped value)
STATE_SOURCES = {"breadth": ("breadth__state", (C.OK,)), "catalyst": ("catalyst__state", (C.CATALYST_KNOWN, C.CATALYST_NONE_OBSERVED)),
                 "first_seen": ("first_seen__state", (C.OK,))}
OBSERVED, RECONSTRUCTED = "observed", "reconstructed"
TRUST_KEY = {"market": "market", "breadth": "market", "sector": "sector", "stock_rs": "stock_rs", "catalyst": "classifications", "first_seen": "first_seen"}

RESIDUAL_LIMITATIONS = (
    "Availability stamps of candidates, feature snapshots, market and sector snapshots (captured_at) and of labels (computed_at) are "
    "writer-settable defaults, not database-enforced: a writer with INSERT rights could have back-dated a row. The audit proves the dataset "
    "is consistent with the stamps; it cannot prove the stamps are honest.",
    "A query fingerprint proves the database still says what it said when the manifest was authored, not that the vendor was right.",
    "Observed history is only as long as live capture has run; anything earlier is reconstructed (today's sector map applied backwards) "
    "and is never point-in-time safe.",
    "The deadline model is daily (00:00 UTC of t0 + 1 + grace days); intraday decision times are not modelled.",
    "The universe is the manifest's explicit list; its survivorship (and, when derived from the database, that it is only the OBSERVED "
    "candidate universe) is the author's responsibility.",
    "Labels are fwd_v1 simple returns on stored prices; they inherit price restatement risk.",
    "The trading calendar travels in the manifest and is not re-derived from the database at build time.",
    "In-sample baselines describe the data; they are not evidence of predictive edge, and a reconstructed history is never evidence of one.",
)


def _hist(rows: Sequence[Mapping[str, Any]], col: str) -> Dict[str, int]:
    out: Dict[str, int] = {}
    for r in rows:
        v = r[col]
        out[str(v)] = out.get(str(v), 0) + 1
    return dict(sorted(out.items()))


def _frac(num: int, den: int) -> Optional[float]:
    return None if den == 0 else round(num / den, 6)


def _iso(d: Any) -> Optional[str]:
    return None if d is None else d.isoformat()


def _source_coverage(name: str, prim: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    if name in PROVENANCE_SOURCES:
        scol, pcol = PROVENANCE_SOURCES[name]
        ok = [r for r in prim if r[scol] == C.OK]
        observed = [r for r in ok if r[pcol] == OBSERVED]
        reconstructed = [r for r in ok if r[pcol] == RECONSTRUCTED]
        other = len(ok) - len(observed) - len(reconstructed)
    else:
        scol, good = STATE_SOURCES[name]
        observed = [r for r in prim if r[scol] in good]
        reconstructed, other = [], 0
    states = _hist(prim, scol)
    by_split = {s: _frac(sum(1 for r in observed if r["split"] == s), sum(1 for r in prim if r["split"] == s)) for s in M.SPLITS}
    return {
        "rows": len(prim), "states": states, "observed": len(observed), "observed_fraction": _frac(len(observed), len(prim)),
        "observed_fraction_by_split": by_split, "reconstructed_used": len(reconstructed), "unknown_provenance_used": other,
        "reconstructed_excluded": states.get(C.RECONSTRUCTED_EXCLUDED, 0),
        "late": states.get(C.LATE, 0) + states.get(C.SECTOR_NAME_LATE, 0),
        "absent": states.get(C.ABSENT, 0) + states.get(C.NO_SECTOR, 0) + states.get(C.SECTOR_ASOF_AFTER_T0, 0),
        "unavailable": states.get(C.UNAVAILABLE, 0), "unknown_availability": states.get(C.UNKNOWN_AVAILABILITY, 0),
        "earliest_observed_session": _iso(min((r["t0_session"] for r in observed), default=None)),
        "latest_observed_session": _iso(max((r["t0_session"] for r in observed), default=None)),
        "stamp_trust": C.AVAILABILITY_TRUST[TRUST_KEY[name]],
    }


def _earliest_trustworthy(cov: Mapping[str, Mapping[str, Any]], prim: Sequence[Mapping[str, Any]], enabled: Mapping[str, bool]) -> Dict[str, Any]:
    """The earliest session D such that, from D onward, EVERY enabled provenance-bearing source (market, sector, stock_rs) has an observed,
    in-time value in at least MIN_OBSERVED_COVERAGE of the primary-horizon rows. Observation merely *starting* on a date is not enough: a
    source that is mostly absent or late after that date does not make the history trustworthy from it."""
    names = [n for n in PROVENANCE_SOURCES if enabled.get(n)]
    firsts = {n: cov[n]["earliest_observed_session"] for n in names}
    basis = (f"the earliest session from which every enabled provenance-bearing context source (market, sector, stock_rs) has an observed, "
             f"in-time value in >= {MIN_OBSERVED_COVERAGE:.0%} of the rows from there on (and no earlier than the day every source has an observed value); the stamps of market/sector are writer-settable, "
             "so this is the earliest date the DATASET can vouch for, not a proof")
    out = {"per_source_first_observed": firsts, "all_sources": None, "observed_fraction_since": None, "rows_since": 0, "basis": basis, "reason": None}
    if not names:
        out["reason"] = "no enabled context source carries provenance"
        return out
    if not prim:
        out["reason"] = "no primary-horizon rows"
        return out
    by_day: Dict[Any, List[Mapping[str, Any]]] = {}
    for r in prim:
        by_day.setdefault(r["t0_session"], []).append(r)
    if any(v is None for v in firsts.values()):
        out["reason"] = "an enabled source has no observed value in any primary-horizon row: " + ", ".join(n for n, v in firsts.items() if v is None)
        return out
    floor = max(firsts.values())               # never earlier than the day every source has started being observed
    total, good, found = 0, {n: 0 for n in names}, None
    for day in sorted(by_day, reverse=True):
        if day.isoformat() < floor:
            break
        for r in by_day[day]:
            total += 1
            for n in names:
                good[n] += 1 if (r[PROVENANCE_SOURCES[n][0]] == C.OK and r[PROVENANCE_SOURCES[n][1]] == OBSERVED) else 0
        if all(good[n] / total >= MIN_OBSERVED_COVERAGE for n in names):
            found = (day, total, dict(good))
    if found is None:
        out["reason"] = f"no date from which every source stays >= {MIN_OBSERVED_COVERAGE:.0%} observed"
        return out
    day, n_rows, g = found
    out.update(all_sources=day.isoformat(), rows_since=n_rows, observed_fraction_since={n: _frac(g[n], n_rows) for n in names})
    return out


def _labels(prim: Sequence[Mapping[str, Any]], maturity: str) -> Dict[str, Any]:
    out: Dict[str, Any] = {"label_maturity_session": maturity, "by_split": {}}
    for s in M.SPLITS:
        rs = [r for r in prim if r["split"] == s]
        hist = _hist(rs, "label_status")
        final = hist.get("final", 0)
        out["by_split"][s] = {"rows": len(rs), "final": final, "void": hist.get("void", 0), "missing": hist.get(C.MISSING, 0),
                              "other": len(rs) - final - hist.get("void", 0) - hist.get(C.MISSING, 0), "final_fraction": _frac(final, len(rs))}
    fin = [r for r in prim if r["label_status"] == "final"]
    out["latest_final_horizon_session"] = _iso(max((r["horizon_session"] for r in fin), default=None))
    out["note"] = ("every row's label is final-mature at the manifest's maturity session or is dropped / counted missing; "
                   "'missing' = no label row exists for that horizon (never zero-filled)")
    return out


def _check(cid: str, passed: bool, detail: str) -> Dict[str, Any]:
    return {"id": cid, "passed": bool(passed), "detail": detail}


def assess_data(*, cfg: C.DatasetConfig, windows_train_end: str, maturity: str, prim: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    """The data-derived part of the verdict (checks 5-10), a pure function of the PRIMARY-horizon assembled rows and the config. `assess` (a built,
    audited dataset) and the Slice 6 status layer both call exactly this function, so the two can never define 'ready' differently."""
    enabled = cfg.enabled()
    cov = {n: _source_coverage(n, prim) for n in list(PROVENANCE_SOURCES) + list(STATE_SOURCES) if enabled.get(n)}
    earliest = _earliest_trustworthy(cov, prim, enabled)
    labels = _labels(prim, maturity)
    need = {s: max(MIN_FINAL_LABELS[s], cfg.min_sample) for s in M.SPLITS}
    sample = {s: {"final_labelled_rows": labels["by_split"][s]["final"], "required": need[s]} for s in M.SPLITS}
    sector_unsafe = sum(1 for r in prim if r["rs__state"] == C.OK and r["rs_sector_pit_safe"] is False) if enabled.get("stock_rs") else 0

    reconstructed_used = sum(c["reconstructed_used"] + c["unknown_provenance_used"] for c in cov.values())
    weak = {n: c["observed_fraction"] for n, c in cov.items() if n in PROVENANCE_SOURCES and (c["observed_fraction"] is None or c["observed_fraction"] < MIN_OBSERVED_COVERAGE)}
    tv = [r for r in prim if r["split"] in ("train", "validation")]
    missing_fraction = _frac(sum(1 for r in tv if r["label_status"] == C.MISSING), len(tv))
    short = {s: v for s, v in sample.items() if v["final_labelled_rows"] < v["required"]}
    checks = [
        _check("no_reconstructed_value_in_dataset", reconstructed_used == 0, f"{reconstructed_used} cells carry a reconstructed or unknown-provenance value"),
        _check("relative_strength_sector_pit_safe", sector_unsafe == 0, f"{sector_unsafe} relative-strength cells use a sector map that is not point-in-time safe"),
        _check("observed_coverage_sufficient", not weak, f"observed-and-in-time coverage must be >= {MIN_OBSERVED_COVERAGE:.0%} per context source; "
               + ("all pass" if not weak else f"below: {weak}")),
        _check("trusted_pit_date_determined", earliest["all_sources"] is not None and earliest["all_sources"] <= windows_train_end,
               "earliest trustworthy PIT date " + (f"is {earliest['all_sources']} (the train window ends {windows_train_end})" if earliest["all_sources"]
                                                    else f"is not determinable ({earliest['reason']})")
               + "; it must fall inside the train window"),
        _check("labels_present", missing_fraction is not None and missing_fraction <= MAX_MISSING_LABEL_FRACTION,
               f"missing-label fraction of train+validation primary-horizon rows {missing_fraction} (max {MAX_MISSING_LABEL_FRACTION})"),
        _check("final_label_samples_sufficient", not short, "final-labelled primary-horizon rows per split vs required: "
               + ", ".join(f"{s} {v['final_labelled_rows']}/{v['required']}" for s, v in sample.items())),
    ]
    return {"coverage": cov, "earliest": earliest, "labels": labels, "sample": sample, "checks": checks, "sector_unsafe_cells": sector_unsafe,
            "missing_label_fraction": missing_fraction}


def assess(*, manifest: M.Manifest, cfg: C.DatasetConfig, rows: Sequence[Mapping[str, Any]], audit_document: Mapping[str, Any],
           audit_hash: str, verification: C.InputVerification, code_check: Mapping[str, Any], dataset_hash: str, report_hash: str,
           inputs_hash: str, include_test: bool) -> Dict[str, Any]:
    doc = manifest.document
    prim = [r for r in rows if r["horizon_sessions"] == cfg.primary_horizon]
    data = assess_data(cfg=cfg, windows_train_end=doc["windows"]["train"][1], maturity=doc["label_maturity_session"], prim=prim)
    cov, earliest, labels, sample = data["coverage"], data["earliest"], data["labels"], data["sample"]
    verif_ok = bool(verification.ok) and not verification.by_kind("unverifiable")
    checks = [
        _check("audit_passed", audit_document.get("verdict") == "PASS" and not audit_document.get("fatal"),
               f"leakage audit verdict {audit_document.get('verdict')}"),
        _check("inputs_verified", verif_ok, "every manifest input hash recomputed and equal; none outside the verifiable vocabulary" if verif_ok
               else "an input hash failed verification or is unverifiable"),
        _check("code_identity_exact", code_check.get("status") == "match" and code_check.get("tree_clean") is True,
               f"code check '{code_check.get('status')}', clean tree {code_check.get('tree_clean')}"),
        _check("reconstructed_policy_is_exclude", cfg.reconstructed_policy == "exclude",
               f"reconstructed_policy is '{cfg.reconstructed_policy}' (research eligibility requires 'exclude')"),
        *data["checks"],
        _check("test_split_unevaluated", not include_test, "the test split has not been revealed by this build" if not include_test
               else "this build revealed the test split: it is spent for any further model selection"),
    ]
    eligible = all(c["passed"] for c in checks)
    out = {
        "schema": READINESS_SCHEMA,
        "distinction": DISTINCTION,
        "identity": {"dataset_name": doc["dataset_name"], "dataset_version": doc["dataset_version"], "manifest_hash": manifest.manifest_hash,
                     "dataset_hash": dataset_hash, "audit_hash": audit_hash, "report_hash": report_hash, "inputs_hash": inputs_hash,
                     "manifest_code_sha": doc["code_sha"], "running_code_sha": code_check.get("running_code_sha"),
                     "code_check": dict(code_check), "label_version": doc["label_version"],
                     "label_methodology_version": doc["label_methodology_version"], "knowledge_cutoff_at": doc["knowledge_cutoff_at"]},
        "rows": {"primary_horizon_rows": len(prim), "all_horizon_rows": len(rows), "primary_horizon": cfg.primary_horizon},
        "coverage": {"by_source": cov, "earliest_trustworthy_pit_date": earliest},
        "label_maturity": labels,
        "sample": sample,
        "pit_limitations": [x["note"] for x in audit_document.get("limitations", [])] + list(RESIDUAL_LIMITATIONS),
        "thresholds": THRESHOLDS,
        "eligibility": {"model_research_eligible": eligible, "checks": checks,
                        "blocking_reasons": [f"{c['id']}: {c['detail']}" for c in checks if not c["passed"]],
                        "predictive_edge_claim": "none",
                        "meaning": "eligible = this dataset's own hygiene does not block STARTING research on it; it is not evidence of an edge, "
                                   "of point-in-time correctness beyond what the audit proves, or of vendor correctness"},
    }
    out["readiness_hash"] = canonical_hash(out)
    return out


def verify_readiness_hash(doc: Mapping[str, Any]) -> bool:
    body = {k: v for k, v in doc.items() if k != "readiness_hash"}
    return canonical_hash(body) == doc.get("readiness_hash")


def render_text(d: Mapping[str, Any]) -> str:
    i, e = d["identity"], d["eligibility"]
    out = [f"DATASET READINESS  ({d['schema']})", "", f"dataset   {i['dataset_name']} {i['dataset_version']}   cutoff {i['knowledge_cutoff_at']}",
           f"manifest  {i['manifest_hash']}", f"dataset   {i['dataset_hash']}", f"audit     {i['audit_hash']}", f"report    {i['report_hash']}",
           f"inputs    {i['inputs_hash']}", f"readiness {d['readiness_hash']}",
           f"code sha  {i['running_code_sha']}  (manifest {i['manifest_code_sha']}; {i['code_check']['status']})", "",
           f"MODEL RESEARCH ELIGIBLE: {'YES' if e['model_research_eligible'] else 'NO'}   (predictive edge claimed: {e['predictive_edge_claim']})",
           f"  {d['distinction']}"]
    for c in e["checks"]:
        out.append(f"  [{'pass' if c['passed'] else 'FAIL'}] {c['id']}: {c['detail']}")
    out += ["", f"COVERAGE (primary horizon {d['rows']['primary_horizon']}, {d['rows']['primary_horizon_rows']} rows)",
            f"  {'source':<10} {'observed':>9} {'obs %':>7} {'recon used':>10} {'recon excl':>10} {'late':>6} {'absent':>7} {'unavail':>8} {'unk.avail':>9}  earliest observed"]
    for n, c in d["coverage"]["by_source"].items():
        pct = "n/a" if c["observed_fraction"] is None else f"{c['observed_fraction']:.1%}"
        out.append(f"  {n:<10} {c['observed']:>9} {pct:>7} {c['reconstructed_used']:>10} {c['reconstructed_excluded']:>10} {c['late']:>6} "
                   f"{c['absent']:>7} {c['unavailable']:>8} {c['unknown_availability']:>9}  {c['earliest_observed_session']}")
    t = d["coverage"]["earliest_trustworthy_pit_date"]
    since = t["observed_fraction_since"]
    out += ["", f"EARLIEST TRUSTWORTHY PIT DATE: {t['all_sources'] or 'not determinable'}"
            + (f"  ({t['rows_since']} rows from there; observed share " + ", ".join(f"{k} {v:.1%}" for k, v in since.items()) + ")" if t["all_sources"]
               else f"  ({t['reason']})"),
            "  first observed session per source: " + ", ".join(f"{k} {v}" for k, v in t["per_source_first_observed"].items()),
            "  " + t["basis"], "", f"LABEL MATURITY (maturity session {d['label_maturity']['label_maturity_session']})"]
    for s, v in d["label_maturity"]["by_split"].items():
        out.append(f"  {s:<10} rows {v['rows']:>6} final {v['final']:>6} void {v['void']:>5} missing {v['missing']:>5}   required final {d['sample'][s]['required']}")
    out += ["", "KNOWN POINT-IN-TIME LIMITATIONS"] + [f"  - {x}" for x in d["pit_limitations"]]
    return "\n".join(out) + "\n"
