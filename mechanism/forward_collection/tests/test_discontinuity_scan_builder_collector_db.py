"""The scan heartbeat end to end: the REAL dataset builder (`build_dataset.main`) writes it, the REAL collector refuses to write observed snapshots without it.

Builder: a complete `--replace --session S` run records exactly one append-only scan row; an identical retry records none; repeated rebuilds keep every
first-detected stamp; a session that is not the newest loaded bar, a price change during the run, a crash, a manual rebuild without --session and a partial run
never produce a COMPLETE row (the first three leave a coded FAILED row; none of them satisfies a reader); a refused scan rolls the prune back with it.
Collector: with step 10 skipped, failed, run on another price version, completed after the cutoff, or followed by a changed discontinuity table, `observe` fails
closed (the session is INCOMPLETE, exit 2, nothing written), and a later valid scan lets the next fire complete it without a duplicate row."""
import sys
from datetime import timedelta
from types import SimpleNamespace

import pytest

import forward_world as FW
from forward_collection import contract as C
from forward_collection import orchestrator as O
from market_intelligence import inputs

from ml_training.data_preparation import build_dataset as bd  # noqa: E402  (forward_world put ml_training/config and the repo root on sys.path)


@pytest.fixture
def world(monkeypatch):
    FW.scale_universe_minimums(monkeypatch)
    yield from FW.make_world(n_days=16, n_stocks=FW.SMALL_UNIVERSE)


def run_builder(world, monkeypatch, *argv):
    cfg = dict(world.args, options=f"-c search_path={world.schema}")
    monkeypatch.setattr(bd, "ml_config", SimpleNamespace(db_config={"host": cfg["host"], "port": cfg["port"], "database": cfg["dbname"], "user": cfg["user"],
                                                                     "password": cfg["password"], "options": cfg["options"]}))
    monkeypatch.setattr(sys, "argv", ["build_dataset.py", *argv])
    code = 0
    try:
        bd.main()
    except SystemExit as e:
        code = e.code
    return code


def scans(world, where="TRUE"):
    with world.connect() as c:
        cur = c.cursor()
        cur.execute(f"SELECT id, session_date, status, failure_reason, input_fingerprint, result_fingerprint, finished_at FROM price_discontinuity_scan WHERE {where} ORDER BY id")
        return cur.fetchall()


def disc(world):
    with world.connect() as c:
        cur = c.cursor()
        cur.execute("SELECT symbol, date, kind, detected_at FROM price_discontinuities ORDER BY symbol, date, kind")
        return cur.fetchall()


def inject_split(world, symbol="S0003", from_k=FW.WARMUP - 5, factor=10.0):
    """A price discontinuity: every bar of `symbol` from the index on is multiplied (an unadjusted split)."""
    d = world.days[from_k]
    with world.connect() as c:
        c.cursor().execute("UPDATE stock_prices SET open = open*%s, high = high*%s, low = low*%s, close = close*%s WHERE symbol = %s AND date >= %s",
                           (factor, factor, factor, factor, symbol, d))
        c.commit()


def load_all(world, upto=1, scan=False):
    for k in range(upto + 1):
        world.load_session(k, scan=scan)


# ---------------------------------------------------------------------------------------------------- builder
def test_a_complete_replace_run_records_one_scan_and_an_identical_retry_records_none(world, monkeypatch, capsys):
    load_all(world)
    s = world.sessions[1]
    assert run_builder(world, monkeypatch, "--replace", "--session", s.isoformat()) == 0
    rows = scans(world)
    assert len(rows) == 1 and rows[0][1] == s and rows[0][2] == "complete" and rows[0][3] is None
    assert run_builder(world, monkeypatch, "--replace", "--session", s.isoformat()) == 0
    assert len(scans(world)) == 1 and "already recorded for this exact input and result" in capsys.readouterr().out      # idempotent: the first completion time stands
    assert scans(world)[0][6] == rows[0][6]


def test_repeated_rebuilds_keep_the_first_detected_stamp_end_to_end(world, monkeypatch):
    load_all(world)
    inject_split(world)
    s = world.sessions[1]
    assert run_builder(world, monkeypatch, "--replace", "--session", s.isoformat()) == 0
    first = disc(world)
    assert first, "the injected split must be detected"
    for _ in range(2):
        assert run_builder(world, monkeypatch, "--replace", "--session", s.isoformat()) == 0
    assert disc(world) == first                                                     # same rows, same first-detected stamps
    assert len(scans(world, "status = 'complete'")) == 1


def test_a_session_that_is_not_the_newest_loaded_bar_is_refused_and_leaves_a_failed_row(world, monkeypatch, capsys):
    load_all(world)
    assert run_builder(world, monkeypatch, "--replace", "--session", world.sessions[0].isoformat()) == 3
    rows = scans(world)
    assert [(r[2], r[3]) for r in rows] == [("failed", "price_data_not_at_session")] and "refusing to scan" in capsys.readouterr().err
    assert disc(world) == []                                                         # it did no work


def test_a_manual_rebuild_without_a_session_records_no_evidence(world, monkeypatch, capsys):
    load_all(world)
    assert run_builder(world, monkeypatch, "--replace") == 0
    assert scans(world) == [] and "will NOT be recorded" in capsys.readouterr().out


def test_a_partial_run_records_no_evidence_and_prunes_nothing(world, monkeypatch, capsys):
    load_all(world)
    with world.connect() as c:
        c.cursor().execute("INSERT INTO price_discontinuities (symbol, date, kind, detected_at) VALUES ('ZZZZ', DATE '2026-01-05', 'jump_up', now())")
        c.commit()
    assert run_builder(world, monkeypatch, "--replace", "--limit", "5", "--session", world.sessions[1].isoformat()) == 0
    assert scans(world) == [] and "only used by a complete" in capsys.readouterr().out
    assert any(r[0] == "ZZZZ" for r in disc(world))


def test_prices_changing_during_the_run_are_refused_by_the_database_and_the_prune_rolls_back_with_it(world, monkeypatch, capsys):
    load_all(world)
    with world.connect() as c:                                                      # a stale row the complete run would prune
        c.cursor().execute("INSERT INTO price_discontinuities (symbol, date, kind, detected_at) VALUES ('ZZZZ', DATE '2026-01-05', 'jump_up', now())")
        c.commit()
    real_save = bd.save

    def save_then_a_vendor_correction(conn, ds, dc):
        real_save(conn, ds, dc)
        with world.connect() as c2:                                                  # the price updater corrects a bar while the scan is running
            c2.cursor().execute("UPDATE stock_prices SET close = close + 0.01 WHERE symbol = 'S0010' AND date = %s", (world.sessions[0],))
            c2.commit()
    monkeypatch.setattr(bd, "save", save_then_a_vendor_correction)
    assert run_builder(world, monkeypatch, "--replace", "--session", world.sessions[1].isoformat()) == 1
    assert [(r[2], r[3]) for r in scans(world)] == [("failed", "input_changed_during_scan")]
    assert "REFUSED by the database" in capsys.readouterr().err
    assert any(r[0] == "ZZZZ" for r in disc(world))                                  # the prune was part of the refused transaction
    monkeypatch.setattr(bd, "save", real_save)
    assert run_builder(world, monkeypatch, "--replace", "--session", world.sessions[1].isoformat()) == 0
    assert [r[2] for r in scans(world)] == ["failed", "complete"] and not any(r[0] == "ZZZZ" for r in disc(world))        # a clean retry completes and prunes


def test_a_crash_leaves_a_failed_row_and_no_complete_row(world, monkeypatch):
    load_all(world)

    def boom(*a, **k):
        raise RuntimeError("simulated crash")
    monkeypatch.setattr(bd, "process_symbol", boom)
    with pytest.raises(RuntimeError):
        run_builder(world, monkeypatch, "--replace", "--session", world.sessions[1].isoformat())
    assert [(r[2], r[3]) for r in scans(world)] == [("failed", "scan_exception")]


# ---------------------------------------------------------------------------------------------------- collector
def collect(w, steps, k, which=0):
    w.now = C.fire_instants_utc(w.sessions[k])[which] + timedelta(minutes=1)
    return O.run_session(w.connect, w.sessions[k], steps, apply=True, grace_days=1, code_ref="t", clock=w.clock)


def observe_step(rep):
    return next(s for s in rep.steps if s.name == C.STEP_OBSERVE)


def refused_with(rep, reason):
    o = observe_step(rep)
    return o.outcome == C.FAILED and reason in (o.error or "")


def test_with_step_10_skipped_observe_fails_closed_and_the_next_fire_completes_it_once_the_scan_exists(world):
    strat, steps = FW.start_world(world, with_capture=False)
    world.load_session(0, scan=False)                                                # the dataset step never recorded a scan
    rep = collect(world, steps, 0)
    assert refused_with(rep, "scan_missing") and rep.verdict == C.INCOMPLETE and rep.exit_code() in C.ALERT_EXIT_CODES
    assert world.count("market_snapshot") == 0 and world.count("stock_relative_strength") == 0
    world.record_scan(0)                                                             # a late, complete scan (still inside the overnight window)
    rep2 = collect(world, steps, 0, which=1)
    assert rep2.verdict == C.COMPLETE and world.count("market_snapshot") == 1
    assert collect(world, steps, 0, which=1).verdict == C.COMPLETE and world.count("market_snapshot") == 1        # idempotent, no duplicate


def test_a_failed_scan_is_not_a_heartbeat(world):
    strat, steps = FW.start_world(world, with_capture=False)
    world.load_session(0, scan=False)
    with world.connect() as c:
        c.cursor().execute("INSERT INTO price_discontinuity_scan (session_date, status, failure_reason, started_at, run_id, writer, code_ref) "
                           "VALUES (%s, 'failed', 'scan_exception', clock_timestamp(), 'r', 'w', 'c')", (world.sessions[0],))
        c.commit()
    rep = collect(world, steps, 0)
    assert refused_with(rep, "scan_failed") and rep.verdict != C.COMPLETE and world.count("market_snapshot") == 0


def test_a_scan_over_another_price_version_does_not_count(world):
    strat, steps = FW.start_world(world, with_capture=False)
    world.load_session(0)                                                            # scanned, then the vendor corrects an old bar
    with world.connect() as c:
        c.cursor().execute("UPDATE stock_prices SET close = close + 0.01 WHERE symbol = 'S0004' AND date = %s", (world.days[FW.WARMUP - 3],))
        c.commit()
    rep = collect(world, steps, 0)
    assert refused_with(rep, "scan_input_mismatch") and world.count("market_snapshot") == 0
    world.record_scan(0)                                                             # a new scan over the corrected prices
    assert collect(world, steps, 0, which=1).verdict == C.COMPLETE


def test_a_scan_completed_after_the_cutoff_never_satisfies_the_collector(world):
    strat, steps = FW.start_world(world, with_capture=False)
    world.load_session(0, scan=False)
    world.record_scan(0, finished=inputs.knowledge_cutoff(world.sessions[0]) + timedelta(hours=1))
    for which in (0, 1):
        rep = collect(world, steps, 0, which=which)
        assert refused_with(rep, "scan_after_cutoff") and rep.verdict != C.COMPLETE
    assert world.record_scan(0) is None                                              # a retry cannot launder it: identical evidence is not re-recorded
    assert refused_with(collect(world, steps, 0, which=1), "scan_after_cutoff") and world.count("market_snapshot") == 0


def test_a_discontinuity_table_changed_after_the_scan_fails_closed(world):
    strat, steps = FW.start_world(world, with_capture=False)
    world.load_session(0)
    with world.connect() as c:                                                       # a manual partial rebuild (or anything) touched the table after the scan
        c.cursor().execute("INSERT INTO price_discontinuities (symbol, date, kind, detected_at) VALUES ('S0002', %s, 'jump_up', (%s::timestamptz AT TIME ZONE current_setting('TimeZone')))",
                           (world.sessions[0] - timedelta(days=20), inputs.knowledge_cutoff(world.sessions[0]) - timedelta(hours=8)))
        c.commit()
    assert refused_with(collect(world, steps, 0), "scan_result_mismatch")
    world.record_scan(0)
    assert collect(world, steps, 0, which=1).verdict == C.COMPLETE


def test_a_provenance_failure_is_never_downgraded_to_success_on_the_dry_run_either(world):
    strat, steps = FW.start_world(world, with_capture=False)
    world.load_session(0, scan=False)
    world.now = C.fire_instants_utc(world.sessions[0])[0] + timedelta(minutes=1)
    rep = O.run_session(world.connect, world.sessions[0], steps, apply=False, grace_days=1, code_ref="t", clock=world.clock)
    assert observe_step(rep).outcome == C.FAILED and "scan_missing" in observe_step(rep).error
