"""Valid manifest / registration builders for the lab tests (explicit imports; never a conftest name)."""
from datetime import date, datetime, timedelta, timezone

from research.lab import manifest as M

NOW = datetime(2026, 10, 4, 12, tzinfo=timezone.utc)
SHA = "a" * 40
H = "b" * 64


def weekdays(start, n):
    out, d = [], start
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return tuple(out)


CAL = weekdays(date(2023, 1, 2), 800)
# train 0..199 | embargo 60 | validation 260..359 | embargo 60 | test 420..519 | maturity 519+60+3 = 582
TR, VA, TE = (CAL[0], CAL[199]), (CAL[260], CAL[359]), (CAL[420], CAL[519])
MAT = CAL[582]


def spec(**o):
    base = dict(dataset_name="ds", dataset_version="v1", code_sha=SHA, code_tree_clean=True, label_version="fwd_v1",
                label_methodology_version="fwd_v1.m1", label_horizons=(5, 20, 60), feature_versions={"mi_v2": "1"},
                universe_id="u", universe_hash=M.universe_hash(["A", "B"], "rule"),
                knowledge_cutoff_at=datetime(MAT.year, MAT.month, MAT.day, 12, tzinfo=timezone.utc), label_maturity_session=MAT,
                windows=M.Windows(TR, VA, TE), embargo_sessions=60, purge_sessions=0, calendar_source="cal", calendar=CAL,
                input_hashes={"labels": H}, config={})
    base.update(o)
    return M.DatasetSpec(**base)


def manifest(**o):
    return M.build_manifest(spec(**o), now=NOW)


def exp(m=None, **o):
    m = m or manifest()
    base = dict(experiment_name="e1", manifest=m, code_sha=SHA, code_tree_clean=True, feature_versions={"mi_v2": "1"},
                model_spec={"family": "baseline"}, search_budget=3, seed=7,
                evaluation_plan={"metrics": ["auc", "brier"], "primary_metric": "auc", "decision_rule": "auc > 0.55 on test"},
                hypothesis="h")
    base.update(o)
    return M.ExperimentSpec(**base)
