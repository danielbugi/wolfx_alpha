"""The corrected post-cycle checkpoint assertions (ops/checkpoint), run against a throwaway schema with the real writers.

What the 2026-10-10 Checkpoint D got wrong and these tests pin: `tiingo_meta` is a documented DIAGNOSTIC sector chain (authoritative `yfinance_info` coverage is required
separately); canonical symbols are validated against the actual price universe (the slash symbol BRK/A is canonical, a vendor request form is not); late price rows are detected in
ONE explicit timezone whatever the connection's TimeZone; the price and result fingerprints are checked independently; and a stale scan is a Verdict B fact only, never a Verdict A
failure. Verdict A (forward research) and Verdict B (collector readiness) are separate files and separate outputs."""
import re
import subprocess
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

import forward_world as FW

REPO = Path(__file__).resolve().parents[3]
CHK = REPO / "ops" / "checkpoint"
UTC = timezone.utc
STAMP_FROM, STAMP_TO = "2026-06-09 03:00:00", "2026-06-09 03:30:00"       # the original discontinuity stamps' window (naive, the discontinuity writer's own frame)
REJECTED = 2


@pytest.fixture
def world(monkeypatch):
    FW.scale_universe_minimums(monkeypatch)
    for w in FW.make_world(n_days=16, n_stocks=FW.SMALL_UNIVERSE):
        cur = w.admin.cursor()                                                       # migration 31 (sector history) is not part of the shared world: apply it here
        cur.execute(f'SET search_path TO "{w.schema}"')
        cur.execute((REPO / "mechanism" / "add_sector_history_tables.sql").read_text(encoding="utf-8"))
        w.admin.commit()
        yield w


def params(world, k=0, strat_id=1):
    s = world.sessions[k]
    return {"SESSION": s.isoformat(), "FLAG_TIME": "2026-06-01 00:00:00+00", "BOUNDARY": world.sessions[0].isoformat(), "STRATEGY_ID": str(strat_id),
            "PIPELINE_START": datetime.combine(s, datetime.min.time(), UTC).replace(hour=20).isoformat(), "LEGACY_COUNT": "3", "LEGACY_FROM": STAMP_FROM, "LEGACY_TO": STAMP_TO,
            "LEGACY_STAMP_MD5": "", "PRICE_WRITER_TZ": "UTC"}


def run_sql(world, file, p, tz=None):
    text = (CHK / file).read_text(encoding="utf-8")
    for k, v in p.items():
        text = text.replace("{{" + k + "}}", v)
    assert "{{" not in text, "an unsubstituted placeholder"
    text = "\n".join(ln for ln in text.splitlines() if not ln.lstrip().startswith("\\"))
    out = []
    with world.connect() as c:
        cur = c.cursor()
        if tz:
            cur.execute(f"SET TIME ZONE '{tz}'")
        cur.execute("SET default_transaction_read_only = on")
        for stmt in re.split(r";\s*\n", text):
            if not stmt.strip() or all(ln.lstrip().startswith("--") or not ln.strip() for ln in stmt.splitlines()):
                continue
            cur.execute(stmt)
            out += [r[0] for r in cur.fetchall()]
        c.rollback()
    return out


def lines(out, verdict):
    d = {}
    for ln in out:
        m = re.match(r"ASSERT\|" + verdict + r"\|([^|]*)\|(.*)$", ln)
        if m:
            d[m.group(1)] = m.group(2)
    return d


def status(v):
    return v.split(" ")[0].split("(")[0]


def seed_sectors(world, *, yfinance=True, tiingo=3, extra=()):
    """Authoritative yfinance_info observations for the whole universe (+ the canonical slash symbol), a few tiingo_meta diagnostic ones, and polls linked to them."""
    syms = list(world.syms) + ["BRK/A"]
    s = world.sessions[0]
    with world.connect() as c:
        cur = c.cursor()
        cur.execute("INSERT INTO stock_prices (symbol, date, open, high, low, close, volume) VALUES ('BRK/A', %s, 10, 11, 9, 10, 1000)", (s,))
        n = 0
        for src, group in (("yfinance_info", syms if yfinance else []), ("tiingo_meta", world.syms[:tiingo])):
            for sym in group + [e for e in extra if src == "yfinance_info"]:
                cur.execute(
                    "INSERT INTO sector_observation (symbol, source, seq, sector, sector_raw, no_sector_reason, change_kind, source_asof, provenance, raw_payload_hash, raw_payload, "
                    "run_id, writer, code_ref, prev_value_hash) VALUES (%s, %s, 1, 'Technology', 'Technology', NULL, 'first', NULL, 'observed_forward', %s, NULL, 'run-1', 'w', 'c', NULL) RETURNING id",
                    (sym, src, "a" * 64))
                oid = cur.fetchone()[0]
                cur.execute("INSERT INTO sector_poll (run_id, symbol, source, response_state, chain_effect, response_sector, raw_payload_hash, observation_id, writer, code_ref) "
                            "VALUES ('run-1', %s, %s, 'sector', 'created_observation', 'Technology', %s, %s, 'w', 'c')", (sym, src, "a" * 64, oid))
                n += 1
        c.commit()
    return n


def seed_capture(world, strat):
    """The REAL capture hook: breakouts the guards rejected (kept as evidence, no `final`), near misses (never tracked), no ledger rows."""
    from research import observer
    k = 0
    cands = world.candidates_for(k, 8)
    for i, c in enumerate(cands):
        if i >= REJECTED:
            c["signal_type"] = "near_bullish"
    finals = [dict(c, combined_score=66.0, ml_prediction_available=True, ml_momentum_probability=0.61, ml_confidence="medium", ml_model_version="m_test")
              for i, c in enumerate(cands) if i >= REJECTED]
    decisions = {c["symbol"]: (["illiquid_dollar_volume"] if i < REJECTED else []) for i, c in enumerate(cands)}
    world.now = world.at(world.sessions[k], 21, 30)
    observer.capture_session(candidates=cands, final_signals=finals, guard_decisions=decisions, guards_evaluated=True, session_date=world.days[FW.WARMUP + k],
                             strategy=strat, connect=world.connect)
    world.settle_capture_run(world.sessions[k], world.at(world.sessions[k], 21, 40))


def seed_discontinuities(world):
    with world.connect() as c:
        cur = c.cursor()
        for i, sym in enumerate(("S0001", "S0002", "S0003")):
            cur.execute("INSERT INTO price_discontinuities (symbol, date, kind, prev_close, close, ratio, detected_at) VALUES (%s, DATE '2026-05-01', 'jump_up', 10, 20, 2, %s)",
                        (sym, f"2026-06-09 03:1{i}:00"))
        cur.execute("SELECT md5(string_agg(symbol || '|' || date || '|' || kind || '|' || detected_at::text, ',' ORDER BY symbol, date, kind)) FROM price_discontinuities")
        md5 = cur.fetchone()[0]
        c.commit()
    return md5


def normalise_price_stamps(world):
    """The simulated world writes bars 'now' (real clock); the real price writer stamps UTC-naive times. Put every bar before the simulated scan (23:30 UTC on the session day)."""
    with world.connect() as c:
        c.cursor().execute("UPDATE stock_prices SET created_at = TIMESTAMP '2026-06-09 12:00:00', updated_at = TIMESTAMP '2026-06-09 12:00:00'")
        c.commit()


def late_bar(world, created="2026-06-10 02:47:09", symbol="S9999"):
    """A straggler dated the session, created (UTC-naive) AFTER the scan."""
    with world.connect() as c:
        c.cursor().execute("INSERT INTO stock_prices (symbol, date, open, high, low, close, volume, created_at, updated_at) VALUES (%s, %s, 10, 11, 9, 10, 1000, %s, %s)",
                           (symbol, world.sessions[0], created, created))
        c.commit()


def build(world, **kw):
    strat, steps = FW.start_world(world, with_capture=True)
    world.load_session(0)
    seed_capture(world, strat)
    seed_sectors(world, **kw)
    md5 = seed_discontinuities(world)
    normalise_price_stamps(world)
    world.record_scan(0)                                                         # the scan covers the discontinuity rows and the (normalised) prices as they are now
    p = params(world, strat_id=strat.id)
    p["LEGACY_STAMP_MD5"] = md5
    return strat, p


# ---------------------------------------------------------------------------------------------------- a clean night
def test_a_clean_night_passes_verdict_a_and_is_ready_for_verdict_b(world):
    strat, p = build(world)
    a = lines(run_sql(world, "assert_forward_research.sql", p), "A")
    b = lines(run_sql(world, "assert_collector_readiness.sql", p), "B")
    bad = {k: v for k, v in a.items() if status(v) in ("FAIL",)}
    assert not bad, bad
    review = {k: v for k, v in a.items() if status(v) == "REVIEW"}
    assert set(review) <= {"ledger_rows_for_session=0 with_observation_id=0 with_snapshot_id=0", "runtime_role_privileges_on_scan_and_sector_tables_select_insert_only",
                           "sector_helper_grants_runtime_only (app yes, public no, admin group no)",
                           "share_classes_present_under_their_canonical_symbol (BRK.B, BF.B, BRK/A)"}, review       # no ledger rows, no database roles and only BRK/A in the throwaway schema
    for name in ("authoritative_yfinance_info_observations_written", "hash_chains_contiguous_and_linked_per_symbol_and_source", "capture_run_complete_for_enabled_strategy_and_session",
                 "complete_scan_exists_for_session", "original_first_detected_stamps_preserved", "guard_rejected_candidates_are_kept_as_evidence_but_never_signals",
                 "every_observation_and_poll_symbol_is_a_symbol_of_the_price_universe (canonical, slash symbols such as BRK/A included)"):
        assert status(a[name]) == "PASS", (name, a[name])
    assert all(status(v) in ("PASS", "INFO") for v in b.values()), b
    funnel = next(k for k in a if k.startswith("funnel session="))
    assert "guard_rejected=2" in funnel and "near_misses=6" in funnel and "tracked_intent=0" in funnel


# ---------------------------------------------------------------------------------------------------- the sector corrections
def test_tiingo_meta_is_an_accepted_diagnostic_chain_and_authoritative_coverage_is_required_separately(world):
    strat, p = build(world, tiingo=3)
    a = lines(run_sql(world, "assert_forward_research.sql", p), "A")
    assert status(a["observation_sources_are_known (yfinance_info authoritative, tiingo_meta documented diagnostic)"]) == "PASS"
    assert any(k.startswith("diagnostic_chains sources=tiingo_meta:3") for k in a)
    with world.connect() as c:                                                   # 20 authoritative observations vanish (forged: the chain is immutable) while diagnostics stay
        cur = c.cursor()
        cur.execute("ALTER TABLE sector_poll DISABLE TRIGGER USER")
        cur.execute("ALTER TABLE sector_observation DISABLE TRIGGER USER")
        cur.execute("DELETE FROM sector_poll WHERE source = 'yfinance_info' AND symbol >= 'S0060' AND symbol < 'S9'")
        cur.execute("DELETE FROM sector_observation WHERE source = 'yfinance_info' AND symbol >= 'S0060' AND symbol < 'S9'")
        for t in ("sector_poll", "sector_observation"):
            cur.execute("SELECT tgname FROM pg_trigger WHERE tgrelid = %s::regclass AND NOT tgisinternal", (t,))
            for (n,) in cur.fetchall():
                cur.execute(f'ALTER TABLE {t} ENABLE ALWAYS TRIGGER "{n}"')
        c.commit()
    a = lines(run_sql(world, "assert_forward_research.sql", p), "A")
    assert status(a["authoritative_coverage_of_the_fundamentals_universe (distinct yfinance_info symbols >= 90% of the newest fundamentals date)"]) == "FAIL"
    assert status(a["authoritative_yfinance_info_observations_written"]) == "PASS"        # present, but not enough: coverage is its own assertion


def test_without_any_authoritative_observation_polls_and_diagnostics_do_not_count_as_sector_recording(world):
    strat, p = build(world, yfinance=False, tiingo=5)
    a = lines(run_sql(world, "assert_forward_research.sql", p), "A")
    assert status(a["authoritative_yfinance_info_observations_written"]) == "FAIL"
    assert status(a["authoritative_coverage_of_the_fundamentals_universe (distinct yfinance_info symbols >= 90% of the newest fundamentals date)"]) == "FAIL"


def test_canonical_symbols_are_checked_against_the_actual_universe_slash_symbols_pass_vendor_forms_fail(world):
    strat, p = build(world)
    a = lines(run_sql(world, "assert_forward_research.sql", p), "A")
    key = "every_observation_and_poll_symbol_is_a_symbol_of_the_price_universe (canonical, slash symbols such as BRK/A included)"
    assert status(a[key]) == "PASS"                                              # BRK/A is in stock_prices exactly as written
    with world.connect() as c:
        cur = c.cursor()
        cur.execute("INSERT INTO sector_observation (symbol, source, seq, sector, sector_raw, no_sector_reason, change_kind, source_asof, provenance, raw_payload_hash, raw_payload, run_id, "
                    "writer, code_ref, prev_value_hash) VALUES ('BRK-B', 'yfinance_info', 1, 'Financial Services', 'Financial Services', NULL, 'first', NULL, 'observed_forward', %s, NULL, 'run-1', 'w', 'c', NULL)",
                    ("b" * 64,))
        c.commit()
    a = lines(run_sql(world, "assert_forward_research.sql", p), "A")
    assert status(a[key]) == "FAIL" and "BRK-B" in a[key]
    assert status(a["no_vendor_request_form_stored_as_a_symbol (BRK-B, BF-B, BRK-A)"]) == "FAIL"


def test_a_corrupted_hash_chain_fails_verdict_a_and_leaves_verdict_b_alone(world):
    strat, p = build(world)
    b_before = lines(run_sql(world, "assert_collector_readiness.sql", p), "B")
    with world.connect() as c:
        cur = c.cursor()
        cur.execute("ALTER TABLE sector_observation DISABLE TRIGGER USER")
        cur.execute("UPDATE sector_observation SET value_hash = repeat('f', 64) WHERE symbol = 'S0005' AND source = 'yfinance_info'")
        cur.execute("SELECT tgname FROM pg_trigger WHERE tgrelid = 'sector_observation'::regclass AND NOT tgisinternal")
        for (n,) in cur.fetchall():
            cur.execute(f'ALTER TABLE sector_observation ENABLE ALWAYS TRIGGER "{n}"')
        c.commit()
    a = lines(run_sql(world, "assert_forward_research.sql", p), "A")
    assert status(a["every_stored_value_hash_equals_the_database_recomputed_hash"]) == "FAIL"
    assert lines(run_sql(world, "assert_collector_readiness.sql", p), "B") == b_before


# ---------------------------------------------------------------------------------------------------- the fingerprint / late-ingestion corrections
def test_a_late_price_row_after_the_scan_is_a_verdict_b_failure_only_and_verdict_a_is_identical(world):
    strat, p = build(world)
    a_before = lines(run_sql(world, "assert_forward_research.sql", p), "A")
    late_bar(world)
    a_after = lines(run_sql(world, "assert_forward_research.sql", p), "A")
    b = lines(run_sql(world, "assert_collector_readiness.sql", p), "B")
    assert a_after == a_before                                                   # a fingerprint mismatch cannot contaminate the forward-research verdict
    assert status(b["price_fingerprint_fresh (a complete scan matches the prices as stored now)"]) == "FAIL"
    late = next(v for k, v in b.items() if k.startswith("price_rows_created_or_changed_after_the_first_complete_scan="))
    assert "FAIL" in late
    assert next(k for k in b if k.startswith("price_rows_created_or_changed_after_the_first_complete_scan=")).endswith("symbols=S9999")
    assert status(b["a_complete_scan_finished_after_the_last_price_write (the evidence covers the final price state)"]) == "FAIL"
    assert status(b["result_fingerprint_fresh (a complete scan matches the discontinuity table as stored now)"]) == "PASS"          # checked independently


def test_the_price_and_result_fingerprints_are_judged_independently(world):
    strat, p = build(world)
    with world.connect() as c:                                                   # the discontinuity table changes, the prices do not
        c.cursor().execute("INSERT INTO price_discontinuities (symbol, date, kind, detected_at) VALUES ('S0009', DATE '2026-05-02', 'jump_up', TIMESTAMP '2026-06-10 01:00:00')")
        c.commit()
    b = lines(run_sql(world, "assert_collector_readiness.sql", p), "B")
    assert status(b["price_fingerprint_fresh (a complete scan matches the prices as stored now)"]) == "PASS"
    assert status(b["result_fingerprint_fresh (a complete scan matches the discontinuity table as stored now)"]) == "FAIL"
    assert status(b["one_complete_scan_matches_both_fingerprints_and_finished_before_the_cutoff"]) == "FAIL"


@pytest.mark.parametrize("tz", ["UTC", "Asia/Jerusalem", "America/New_York", "Pacific/Kiritimati"])
def test_late_rows_are_detected_identically_in_any_connection_timezone(world, tz):
    strat, p = build(world)
    late_bar(world, created="2026-06-10 02:47:09")
    out = run_sql(world, "assert_collector_readiness.sql", p, tz=tz)
    b = lines(out, "B")
    late_key = next(k for k in b if k.startswith("price_rows_created_or_changed_after_the_first_complete_scan="))
    assert late_key.endswith("=1 symbols=S9999") and "FAIL" in b[late_key], (tz, late_key)
    assert out == run_sql(world, "assert_collector_readiness.sql", p, tz="UTC")           # byte-identical to the UTC run: no dependence on the session zone


def test_a_row_created_before_the_scan_in_the_writers_frame_is_not_late_even_if_it_looks_later_in_another_zone(world):
    strat, p = build(world)
    late_bar(world, created="2026-06-09 22:00:00")                                 # 22:00 UTC: BEFORE the 23:30 UTC scan (22:00 would look 'after' if misread as a +03:00 local... it is not)
    b = lines(run_sql(world, "assert_collector_readiness.sql", p, tz="Asia/Jerusalem"), "B")
    late_key = next(k for k in b if k.startswith("price_rows_created_or_changed_after_the_first_complete_scan="))
    assert late_key.endswith("=0 symbols=none") and status(b[late_key]) == "PASS"
    assert status(b["price_fingerprint_fresh (a complete scan matches the prices as stored now)"]) == "FAIL"        # the row still changed the fingerprint: only the TIMING check is clean


# ---------------------------------------------------------------------------------------------------- candidate guard semantics
def test_a_rejected_candidate_that_is_tracked_or_ledgered_or_a_near_miss_that_is_tracked_fails(world):
    strat, p = build(world)
    key = "guard_rejected_candidates_are_kept_as_evidence_but_never_signals"
    assert status(lines(run_sql(world, "assert_forward_research.sql", p), "A")[key]) == "PASS"
    with world.connect() as c:
        cur = c.cursor()
        cur.execute("SELECT tgname FROM pg_trigger WHERE tgrelid = 'candidate_observation'::regclass AND NOT tgisinternal")
        names = [r[0] for r in cur.fetchall()]
        cur.execute("ALTER TABLE candidate_observation DISABLE TRIGGER USER")
        cur.execute("UPDATE candidate_observation SET tracked_intent = true WHERE id = (SELECT id FROM candidate_observation WHERE passed_guard IS FALSE ORDER BY id LIMIT 1)")
        cur.execute("UPDATE candidate_observation SET tracked_intent = true WHERE id = (SELECT id FROM candidate_observation WHERE NOT triggered ORDER BY id LIMIT 1)")
        for n in names:
            cur.execute(f'ALTER TABLE candidate_observation ENABLE ALWAYS TRIGGER "{n}"')
        c.commit()
    a = lines(run_sql(world, "assert_forward_research.sql", p), "A")
    assert status(a[key]) == "FAIL" and status(a["near_misses_are_never_tracked_signals"]) == "FAIL"
    assert status(a["every_tracked_intent_is_a_new_ledger_row_or_an_already_open_position (position invariant)"]) == "FAIL"        # tracked but no ledger row and no earlier position


# ---------------------------------------------------------------------------------------------------- the runner
def test_the_runner_keeps_the_two_verdicts_apart_and_has_no_unsubstituted_placeholders():
    text = (CHK / "checkpoint.sh").read_text(encoding="utf-8")
    assert "VERDICT_A|" in text and "VERDICT_B|" in text and "NOT_READY" in text
    assert "--apply" not in "\n".join(ln for ln in text.splitlines() if not ln.lstrip().startswith("#")) and "default_transaction_read_only=on" in text
    placeholders = set(re.findall(r"\{\{([A-Z_]+)\}\}", (CHK / "assert_forward_research.sql").read_text(encoding="utf-8") + (CHK / "assert_collector_readiness.sql").read_text(encoding="utf-8")))
    for ph in placeholders:
        assert "{{" + ph + "}}" in text or ph in text.replace("CHECKPOINT_", ""), ph               # the runner substitutes every placeholder the SQL declares
    bash = shutil.which("bash")
    if bash:
        r = subprocess.run([bash, "-n", (CHK / "checkpoint.sh").as_posix()], capture_output=True, text=True)
        assert r.returncode == 0, r.stderr
