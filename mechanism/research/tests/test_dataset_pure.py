"""Pure (database-free) tests of the dataset contract, fingerprints, input verification, baselines, report helpers and the textual guards
that keep the research harness read-only and clock/driver-free."""
import ast
import copy
import inspect
import math
import re
from datetime import date, datetime, timedelta, timezone

import pytest

import research.lab.dataset_assemble as A
import research.lab.dataset_audit as AU
import research.lab.dataset_authoring as AUTH
import research.lab.dataset_baselines as B
import research.lab.dataset_cli as CLI
import research.lab.dataset_contract as C
import research.lab.dataset_readiness as RY
import research.lab.dataset_reader as RD
import research.lab.dataset_report as RP
import research.lab.dataset_runner as R
from dataset_world import CAL, CUTOFF, HORIZONS, MAT, config, make_spec
from lab_samples import NOW
from research.lab import manifest as M

UTC = timezone.utc
MEMBERS = ("A", "B", "C")
CFG = C.parse_config(config(), HORIZONS)


def fake_fps():
    return {s: f"{i:064x}" for i, s in enumerate(C.enabled_db_sources(CFG), start=1)}


def authored(**over):
    hashes = C.compute_input_hashes(CFG, CAL, MEMBERS, fake_fps())
    return M.build_manifest(make_spec(hashes, config(), **over), now=NOW)


# ------------------------------------------------------------------ the point-in-time rule
def test_is_known_boundaries():
    t0 = CAL[300]
    deadline = C.decision_deadline(t0, 0)
    assert deadline == datetime(t0.year, t0.month, t0.day, tzinfo=UTC) + timedelta(days=1)
    assert C.is_known(deadline - timedelta(microseconds=1), t0, 0, CUTOFF)
    assert not C.is_known(deadline, t0, 0, CUTOFF)                       # strictly before the deadline
    assert not C.is_known(None, t0, 0, CUTOFF)                           # unknown availability is never "known"
    assert C.is_known(deadline, t0, 1, CUTOFF)                           # the grace extends the deadline by whole days
    assert not C.is_known(CUTOFF + timedelta(microseconds=1), CAL[500], 5, CUTOFF)   # nothing after the knowledge cutoff, ever
    assert C.is_known(CUTOFF, CAL[581], 5, CUTOFF)
    assert C.effective_deadline(CAL[581], 5, CUTOFF) == CUTOFF


def test_event_availability_by_grade_never_invents_a_time():
    k, i = datetime(2023, 1, 1, tzinfo=UTC), datetime(2023, 1, 2, tzinfo=UTC)
    assert C.event_available_at("A", k, i) == k and C.event_available_at("B", k, i) == k
    assert C.event_available_at("C", k, i) == i
    assert C.event_available_at("X", k, i) is None and C.event_available_at("Z", k, i) is None
    assert C.event_available_at("A", None, i) is None


# ------------------------------------------------------------------ config parsing is strict and complete
def test_valid_config_parses_to_the_declared_sources():
    assert CFG.universe_members == MEMBERS and CFG.primary_horizon == 20
    assert set(C.enabled_db_sources(CFG)) == {"candidates", "labels", "market", "sector", "stock_rs", "events", "classifications", "first_seen"}
    assert set(C.required_input_keys(CFG)) == set(C.CRYPTO_INPUTS) | {C.DB_INPUTS[s] for s in C.enabled_db_sources(CFG)}


@pytest.mark.parametrize("edit,needle", [
    (lambda c: c.pop("schema"), "config.schema"), (lambda c: c.pop("strategy"), "config.strategy"),
    (lambda c: c.update(universe_members=["B", "A"]), "universe_members"), (lambda c: c.update(universe_members=["A", "A"]), "universe_members"),
    (lambda c: c.update(universe_members=[]), "universe_members"), (lambda c: c.pop("universe_rule"), "universe_rule"),
    (lambda c: c.pop("availability_grace_days"), "availability_grace_days"), (lambda c: c.update(availability_grace_days=-1), "availability_grace_days"),
    (lambda c: c.update(availability_grace_days=True), "availability_grace_days"), (lambda c: c.update(min_sample=1), "min_sample"),
    (lambda c: c.update(primary_horizon=7), "primary_horizon"), (lambda c: c.update(reconstructed_policy="latest"), "reconstructed_policy"),
    (lambda c: c.update(market={}), "config.market"), (lambda c: c.update(stock_rs={"model_version": "x"}), "config.stock_rs"),
    (lambda c: c.update(catalyst={"lookback_days": 0, "classifier": "a", "classifier_version": "b"}), "config.catalyst"),
    (lambda c: c.update(first_seen=[{"source": "s", "dataset": "d", "fields": []}]), "config.first_seen[0]"),
    (lambda c: c.update(first_seen=[{"source": "s", "dataset": "d", "fields": ["f"]}] * 2), "twice"),
])
def test_config_problems_are_reported(edit, needle):
    cfg = copy.deepcopy(config())
    edit(cfg)
    with pytest.raises(M.LabError) as e:
        C.parse_config(cfg, HORIZONS)
    assert any(needle in p for p in e.value.problems), e.value.problems


def test_config_reports_every_problem_at_once():
    cfg = copy.deepcopy(config())
    cfg.pop("min_sample")
    cfg["primary_horizon"] = 7
    cfg["reconstructed_policy"] = "latest"
    with pytest.raises(M.LabError) as e:
        C.parse_config(cfg, HORIZONS)
    assert len(e.value.problems) == 3


def test_embargo_must_cover_the_longest_horizon_plus_purge():
    assert authored().required_gap() == 60
    assert M.required_gap(HORIZONS, 10) == 70
    with pytest.raises(M.LabError):
        authored(embargo_sessions=59)
    with pytest.raises(M.LabError):
        authored(purge_sessions=10)                       # 60 + 10 > embargo 60
    with pytest.raises(M.LabError):
        authored(purge_sessions=10, embargo_sessions=70)  # the declared windows are only 60 sessions apart: they must really be separated


# ------------------------------------------------------------------ canonical values
def test_cells_are_encoded_strictly():
    assert C.iso_ts(datetime(2023, 1, 1, 3, tzinfo=timezone(timedelta(hours=3)))) == C.iso_ts(datetime(2023, 1, 1, tzinfo=UTC))
    for bad in (True, "1", float("nan"), float("inf"), object()):
        with pytest.raises(M.LabError):
            C.encode_cell(bad, "float", "x")
    assert C.encode_cell(None, "float", "x") is None


# ------------------------------------------------------------------ query fingerprints
def _market_row(day, stamp, score=0.5):
    vals = {"session_date": day, "provenance": "observed", "feature_set_version": "mi_v2", "regime_model_version": "m", "rs_model_version": "r",
            "regime_state": "RISK_ON", "regime_score": score, "regime_strength": 0.4, "regime_components": {"a": {"present": True, "value": 1.0}},
            "content_hash": "h" * 64, "captured_at": stamp}
    return {c: vals[c] for c in C.SOURCE_COLUMNS["market"]}


def test_fingerprint_is_order_independent_cutoff_bounded_and_value_sensitive():
    rows = [_market_row(CAL[i], datetime(2023, 1, 1, tzinfo=UTC) + timedelta(days=i)) for i in range(5)]
    base = C.fingerprint("market", rows, CUTOFF)
    assert C.fingerprint("market", list(reversed(rows)), CUTOFF) == base
    late = _market_row(CAL[9], CUTOFF + timedelta(seconds=1))
    assert C.fingerprint("market", rows + [late], CUTOFF) == base               # a row appended after the cutoff cannot change it
    assert C.fingerprint("market", rows + [late], CUTOFF + timedelta(days=1)) != base
    assert C.fingerprint("market", rows, CUTOFF + timedelta(days=1)) != base       # the cutoff is part of the identity
    changed = [dict(rows[0], regime_score=0.5000001)] + rows[1:]
    assert C.fingerprint("market", changed, CUTOFF) != base
    assert C.fingerprint("market", rows[:-1], CUTOFF) != base
    assert C.fingerprint("market", [dict(r, regime_score=None) for r in rows], CUTOFF) != base
    assert C.fingerprint("market", [], CUTOFF) != base


# ------------------------------------------------------------------ input verification
def _verify(m, fps=None, calendar=CAL, members=MEMBERS):
    return C.verify_inputs(m.document, CFG, calendar, members, fake_fps() if fps is None else fps)


def test_every_required_input_is_verified_and_classified():
    v = _verify(authored())
    assert v.ok and not v.failures()
    assert {c.key for c in v.by_kind("cryptographic")} == set(C.CRYPTO_INPUTS)
    assert {c.key for c in v.by_kind("database_fingerprint")} == {C.DB_INPUTS[s] for s in C.enabled_db_sources(CFG)}
    assert [c.key for c in v.checks] == sorted(set(c.key for c in v.checks), key=[c.key for c in v.checks].index)


def test_a_changed_fingerprint_is_a_mismatch_and_a_missing_one_is_missing():
    m = authored()
    fps = fake_fps()
    fps["labels"] = "0" * 64
    v = _verify(m, fps)
    assert not v.ok and [(c.key, c.status) for c in v.failures()] == [("db.forward_return_label", "MISMATCH")]
    fps = fake_fps()
    del fps["market"]
    v = _verify(m, fps)
    assert not v.ok and [(c.key, c.status) for c in v.failures()] == [("db.market_snapshot", "MISMATCH")]    # no actual => cannot match


def test_changed_calendar_or_universe_fail_the_cryptographic_checks():
    m = authored()
    v = _verify(m, calendar=CAL[:-1])
    assert {c.key for c in v.failures()} >= {"calendar.sessions", "manifest.calendar_hash"}
    v = _verify(m, members=("A", "B"))
    assert {c.key for c in v.failures()} >= {"universe.members", "manifest.universe_hash"}


def test_missing_declared_hash_fails_and_unknown_keys_are_visible_not_trusted():
    hashes = C.compute_input_hashes(CFG, CAL, MEMBERS, fake_fps())
    short = {k: v for k, v in hashes.items() if k != "db.stock_relative_strength"}
    v = _verify(M.build_manifest(make_spec(short, config()), now=NOW))
    assert [(c.key, c.status) for c in v.failures()] == [("db.stock_relative_strength", "MISSING")]
    extra = dict(hashes, **{"vendor.something": "e" * 64})
    v = _verify(M.build_manifest(make_spec(extra, config()), now=NOW))
    assert v.ok and [(c.key, c.kind, c.status) for c in v.checks if c.key == "vendor.something"] == [("vendor.something", "unverifiable", "unverifiable")]


def test_input_hashes_need_a_fingerprint_for_every_enabled_source():
    fps = fake_fps()
    del fps["events"]
    with pytest.raises(M.LabError, match="events"):
        C.compute_input_hashes(CFG, CAL, MEMBERS, fps)


def test_dataset_schema_hash_is_stable_and_bound_to_the_columns():
    assert C.schema_hash() == C.schema_hash() and len(C.schema_hash()) == 64
    assert len(C.COLUMN_NAMES) == len(set(C.COLUMN_NAMES))
    assert {"observation_key", "split", "label_status", "directional_return", "regime_state", "rs_percentile", "catalyst__state"} <= set(C.COLUMN_NAMES)


def test_revalidating_a_stored_manifest_requires_its_own_hash_and_calendar():
    m = authored()
    created = datetime(2026, 1, 1, tzinfo=UTC)
    assert C.revalidate_manifest(m.document, m.manifest_hash, CAL, created).manifest_hash == m.manifest_hash
    with pytest.raises(M.LabError):
        C.revalidate_manifest(m.document, "0" * 64, CAL, created)
    with pytest.raises(M.LabError):
        C.revalidate_manifest(m.document, m.manifest_hash, CAL[:-1], created)
    doc = copy.deepcopy(m.document)
    doc["embargo_sessions"] = 61
    with pytest.raises(M.LabError):
        C.revalidate_manifest(doc, m.manifest_hash, CAL, created)


# ------------------------------------------------------------------ baselines
def _row(i, split, ret, *, status="final", regime="RISK_ON", rs=50.0, catalyst="none_observed", h=20, benchmark="ok"):
    r = A._empty_row()
    r.update({"observation_key": f"k{i}", "split": split, "horizon_sessions": h, "label_status": status, "directional_return": ret,
              "directional_excess_return": None if ret is None else ret - 0.01, "benchmark_state": benchmark, "market__state": "ok",
              "regime_state": regime, "rs__state": "ok", "rs_percentile": rs, "catalyst__state": catalyst, "t0_session": CAL[i], "symbol": "A",
              "direction": 1, "tracked_intent": i % 3 != 0, "signal_type": "bullish_breakout" if i % 3 else "near_bullish"})
    return r


def _rows(n_per=30):
    rows = []
    for si, split in enumerate(M.SPLITS):
        for i in range(n_per):
            rows.append(_row(si * 100 + i, split, ((i % 11) - 5) / 100, regime=("RISK_ON", "RISK_OFF")[i % 2], rs=(i * 3) % 100,
                             catalyst=("catalyst_known", "none_observed")[i % 2]))
    return rows


def test_summaries_below_the_minimum_carry_a_count_not_a_number():
    s = B.summarize([0.1, 0.2], 10, with_hit=True)
    assert s == {"n": 2, "status": "insufficient_sample", "min_sample": 10}
    assert B.summarize([], 10, with_hit=True)["n"] == 0
    ok = B.summarize([1.0, 2.0, 3.0, 4.0], 4, with_hit=True)
    assert ok["status"] == "ok" and ok["mean"] == 2.5 and ok["median"] == 2.5 and ok["hit_rate"] == 1.0
    assert ok["sd"] == pytest.approx(math.sqrt(5 / 3), abs=1e-9)
    lo, hi = ok["hit_rate_ci95_wilson"]
    assert 0 < lo < 1.0 and hi == pytest.approx(1.0) and ok["t_vs_zero"] > 0
    flat = B.summarize([1.0] * 5, 4, with_hit=False)
    assert flat["sd"] == 0 and flat["t_vs_zero"] is None and "hit_rate" not in flat


def test_wilson_and_welch_known_values():
    lo, hi = B.wilson(5, 10)
    assert lo == pytest.approx(0.2366, abs=1e-3) and hi == pytest.approx(0.7634, abs=1e-3)
    w = B.welch([1, 2, 3, 4], [2, 3, 4, 5], 4)
    assert w["status"] == "ok" and w["mean_difference"] == -1.0 and w["welch_df"] == pytest.approx(6.0)
    assert B.welch([1.0], [2.0, 3.0], 4) == {"n_a": 1, "n_b": 2, "status": "insufficient_sample", "min_sample": 4}
    assert "p_value" not in w


def test_block_counts_every_label_state_and_only_averages_what_is_defined():
    rows = [_row(i, "train", 0.01 * i) for i in range(12)] + [_row(50, "train", None, status="void"), _row(51, "train", None, status=C.MISSING)]
    rows.append(_row(52, "train", 0.02, benchmark="not_evaluated"))
    b = B.block(rows, 5)
    assert (b["n_rows"], b["n_final"], b["n_void"], b["n_label_missing"]) == (15, 13, 1, 1)
    assert b["directional_return"]["n"] == 13 and b["directional_excess_return"]["n"] == 12      # the benchmark-less row is excluded, not zeroed


def test_strata_and_buckets():
    r = _row(1, "train", 0.1)
    assert B.regime_stratum(r) == "RISK_ON" and B.rs_stratum(r) == "rs_pct_33_67" and B.catalyst_stratum(r) == "none_observed"
    assert B.rs_stratum(_row(1, "train", 0.1, rs=0.0)) == "rs_pct_0_33" and B.rs_stratum(_row(1, "train", 0.1, rs=100.0)) == "rs_pct_67_100"
    masked = dict(r, market__state="late", regime_state=None, rs__state="absent", rs_percentile=None)
    assert B.regime_stratum(masked) == "<late>" and B.rs_stratum(masked) == "<absent>"
    with pytest.raises(M.LabError):
        B.rs_stratum(_row(1, "train", 0.1, rs=101.0))
    assert B.rs_stratum(dict(r, rs_percentile=None)) == "<rs_percentile_null>"


def test_run_baselines_is_deterministic_and_withholds_the_test_split():
    rows = _rows()
    a = B.run_baselines(rows, CFG)
    assert B.run_baselines(list(reversed(rows)), CFG) == a
    assert a["version"] == B.BASELINE_VERSION and a["disclaimer"] == B.DISCLAIMER and a["n_comparisons"] > 0
    assert set(a["baselines"]) == {"null_unconditional", "donchian_signal", "donchian_x_regime", "donchian_x_rs", "donchian_x_catalyst"}
    assert a["baselines"]["null_unconditional"]["by_split"]["test"] == {"status": "withheld"}
    assert a["baselines"]["donchian_x_regime"]["strata"]["test"]["status"] == "withheld"
    expected = sum(1 for r in rows if r["split"] == "train" and r["tracked_intent"] and r["regime_state"] == "RISK_ON")
    assert expected > 0 and a["baselines"]["donchian_x_regime"]["strata"]["train"]["RISK_ON"]["n_rows"] == expected
    assert sum(b["n_rows"] for b in a["baselines"]["donchian_x_regime"]["strata"]["train"].values()) == sum(
        1 for r in rows if r["split"] == "train" and r["tracked_intent"])
    ctr = a["baselines"]["donchian_signal"]["strata"]["train"]
    assert set(ctr) == {"tracked", "control"} and ctr["tracked"]["n_rows"] + ctr["control"]["n_rows"] == 30
    full = B.run_baselines(rows, CFG, include_test=True)
    assert full["baselines"]["null_unconditional"]["by_split"]["test"]["n_rows"] == 30
    assert full["baselines"]["null_unconditional"]["by_split"]["train"] == a["baselines"]["null_unconditional"]["by_split"]["train"]


def test_a_split_without_final_labels_reports_zero_samples_and_no_return():
    rows = [_row(i, "train", None, status="void") for i in range(40)] + [_row(100 + i, "validation", 0.01) for i in range(40)]
    a = B.run_baselines(rows, CFG)
    train = a["baselines"]["null_unconditional"]["by_split"]["train"]
    assert train["n_rows"] == 40 and train["n_final"] == 0 and train["n_void"] == 40
    assert train["directional_return"] == {"n": 0, "status": "insufficient_sample", "min_sample": CFG.min_sample}
    assert a["baselines"]["null_unconditional"]["by_split"]["test"] == {"status": "withheld"}


# ------------------------------------------------------------------ report helpers
def test_quantiles_and_insufficient_walk():
    assert RP._quantiles([1.0], 10) == {"n": 1, "status": "insufficient_sample", "min_sample": 10}
    q = RP._quantiles([float(i) for i in range(11)], 10)
    assert (q["min"], q["p50"], q["max"], q["n"]) == (0.0, 5.0, 10.0, 11)
    out = []
    RP._walk_insufficient({"x": {"y": {"n": 3, "status": "insufficient_sample", "min_sample": 10}}, "z": {"status": "ok"}}, "root", out)
    assert out == [{"path": "root.x.y", "n": 3, "min_sample": 10}]


def test_report_hash_verification_helpers():
    body = {"schema": RP.REPORT_SCHEMA, "a": 1}
    body["report_hash"] = M.canonical_hash(dict(body))
    assert RP.verify_report_hash(body)
    assert not RP.verify_report_hash(dict(body, a=2))
    assert not RP.verify_report_hash({"schema": RP.REPORT_SCHEMA, "a": 1})


# ------------------------------------------------------------------ textual guards
PURE = (C, A, AU, B, RP, AUTH, RY)


def code_only(module):
    """The module's executable text: docstrings and comments removed, so a guard cannot be fooled by (or trip over) prose."""
    tree = ast.parse(inspect.getsource(module))
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.ClassDef, ast.AsyncFunctionDef)) and node.body                 and isinstance(node.body[0], ast.Expr) and isinstance(node.body[0].value, ast.Constant) and isinstance(node.body[0].value.value, str):
            node.body[0] = ast.Pass()
    return ast.unparse(tree)


@pytest.mark.parametrize("module", PURE, ids=lambda m: m.__name__.rsplit(".", 1)[-1])
def test_pure_dataset_modules_read_no_clock_driver_environment_or_model_library(module):
    src = code_only(module)
    for w in ("psycopg2", "datetime.now", "date.today", "utcnow", "time.time(", "sklearn", "xgboost", "torch", "os.environ", "open(", "subprocess",
              "random", "numpy", "pandas"):
        assert w not in src, (module.__name__, w)


def test_the_reader_issues_select_statements_only():
    src = code_only(RD)
    assert not re.search(r"\b(INSERT|UPDATE|DELETE|ALTER|CREATE|DROP|TRUNCATE|GRANT|REVOKE|COPY|VACUUM)\b", src)
    sql = re.findall(r"SELECT ", src)
    assert len(sql) >= 8
    assert "commit(" not in src and "set_session(readonly=True)" in src


def test_the_runner_writes_only_through_the_registry():
    src = code_only(R)
    assert "psycopg2" not in src and ".execute(" not in src and "commit(" not in src
    assert "RS.insert_manifest" not in src                      # a manifest is registered by its author, never by a build
    for w in ("os.environ", "datetime.now", "utcnow", "time.time(", "sklearn", "xgboost", "torch"):
        assert w not in src, w
    assert R.DIAGNOSTIC_METRICS == ("n_rows", "n_final")


def test_the_harness_modules_do_not_reference_production_or_hatches():
    for module in PURE + (RD, R):
        src = code_only(module)
        assert not re.search(r"research_maintenance_|maintenance_ticket|research_capture_set_state|research_audit_append_only|research_guard_immutable", src)
        assert "116.220" not in src and "PROD_SENDING_ENABLED" not in src and "TELEGRAM" not in src
        assert not re.search(r"research\.labels|research import labels", src)


def _function_sources(module):
    tree = ast.parse(inspect.getsource(module))
    return {n.name: ast.unparse(n) for n in tree.body if isinstance(n, ast.FunctionDef)}


def test_the_owner_cli_writes_to_the_database_only_through_the_one_explicit_register_path():
    src = code_only(CLI)
    assert not re.search(r"(INSERT|UPDATE|DELETE|ALTER|CREATE|DROP|TRUNCATE|GRANT|REVOKE|COPY|VACUUM)", src), "the CLI issues no write SQL of its own"
    assert ".execute(" in src and src.count("RS.insert_") == 2 and "RS.insert_" in _function_sources(CLI)["_register"]
    funcs = _function_sources(CLI)
    assert all("RS.insert_" not in body for name, body in funcs.items() if name != "_register")
    assert all("record_validation" not in body and "record_test" not in body for name, body in funcs.items() if name != "_register")
    assert [n for n, body in funcs.items() if "readonly=False" in body or "connect(a, readonly=False)" in body] == ["_register"]
    assert "conn.set_session(readonly=True)" in src and "os.environ.get(a.password_env)" in src and src.count("os.environ") == 1
    for w in ("datetime.now", "date.today", "utcnow", "time.time(", "sklearn", "xgboost", "torch", "random", "numpy", "pandas", "TELEGRAM", "PROD_SENDING_ENABLED",
              "116.220", "shell=True", "--password\""):
        assert w not in src, w
    assert not re.search(r"research_maintenance_|maintenance_ticket|research_capture_set_state|research_audit_append_only|research_guard_immutable", src)


def test_the_owner_cli_reimplements_none_of_the_shared_contracts():
    src = code_only(CLI)
    for w in ("hashlib", "sha256", "def is_known", "def dataset_hash", "def revalidate", "def assemble", "def audit_dataset", "def run_baselines", "def build_report"):
        assert w not in src, w
    for needed in ("R.build_from_manifest", "R.verify_manifest", "R.author_input_hashes", "AUTH.parse_manifest_file", "READY.assess"):
        assert needed in src, needed
