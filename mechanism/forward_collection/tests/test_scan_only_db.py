"""R2: `build_dataset.py --scan-only --session S`, run for real (the actual `main`) against a throwaway schema.

Proven: the ML dataset is never read, rebuilt, truncated or altered (a trigger on the dataset table refuses ANY DML and TRUNCATE for the whole test); the detection
set equals the full builder's; original first-detected timestamps are preserved; scan history is append-only and idempotent across both modes; the scan is
recomputed from the price state it started with and a price change during the run is refused (and the prune rolls back with it); the target session must be the
latest completed market session; straggler bars between two scans produce a second matching scan and never alter the first; the collector accepts the scan-only
evidence end to end. Nothing here runs a pipeline."""
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


def run_builder(world, monkeypatch, *argv, latest=None):
    cfg = dict(world.args, options=f"-c search_path={world.schema}")
    monkeypatch.setattr(bd, "ml_config", SimpleNamespace(db_config={"host": cfg["host"], "port": cfg["port"], "database": cfg["dbname"], "user": cfg["user"],
                                                                     "password": cfg["password"], "options": cfg["options"]}))
    if latest is not None:
        monkeypatch.setattr(bd, "latest_completed_session", (lambda: latest) if not isinstance(latest, Exception) else (lambda: (_ for _ in ()).throw(latest)))
    monkeypatch.setattr(sys, "argv", ["build_dataset.py", *argv])
    try:
        bd.main()
        return 0
    except SystemExit as e:
        return e.code


def scans(world, where="TRUE"):
    with world.connect() as c:
        cur = c.cursor()
        cur.execute(f"SELECT id, session_date, status, failure_reason, input_fingerprint, result_fingerprint, finished_at, code_ref FROM price_discontinuity_scan "
                    f"WHERE {where} ORDER BY id")
        return cur.fetchall()


def disc(world):
    with world.connect() as c:
        cur = c.cursor()
        cur.execute("SELECT symbol, date, kind, detected_at FROM price_discontinuities ORDER BY symbol, date, kind")
        return cur.fetchall()


def inject_split(world, symbol="S0003", from_k=FW.WARMUP - 5, factor=10.0):
    d = world.days[from_k]
    with world.connect() as c:
        c.cursor().execute("UPDATE stock_prices SET open = open*%s, high = high*%s, low = low*%s, close = close*%s WHERE symbol = %s AND date >= %s",
                           (factor, factor, factor, factor, symbol, d))
        c.commit()


def load_all(world, upto=1):
    for k in range(upto + 1):
        world.load_session(k, scan=False)


def guard_dataset(world):
    """Seed the ML dataset with sentinel rows, then make the table REFUSE every kind of write for the rest of the test: any attempt by the code under test to
    insert, update, delete or truncate it fails loudly. Returns a fingerprint function of the table's content."""
    with world.connect() as c:
        cur = c.cursor()
        cur.execute("INSERT INTO ml_breakout_dataset_v2 (symbol, date, direction, feature_set_version, features) VALUES "
                    "('S0001', DATE '2026-01-05', 1, 'sentinel', '{}'::jsonb), ('S0002', DATE '2026-01-06', -1, 'sentinel', '{}'::jsonb)")
        cur.execute("CREATE FUNCTION zz_dataset_guard() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'the ML dataset must not be touched by --scan-only (%)', TG_OP; END $$")
        cur.execute("CREATE TRIGGER zz_dataset_row BEFORE INSERT OR UPDATE OR DELETE ON ml_breakout_dataset_v2 FOR EACH ROW EXECUTE FUNCTION zz_dataset_guard()")
        cur.execute("CREATE TRIGGER zz_dataset_trunc BEFORE TRUNCATE ON ml_breakout_dataset_v2 FOR EACH STATEMENT EXECUTE FUNCTION zz_dataset_guard()")
        c.commit()

    def fingerprint():
        with world.connect() as c2:
            cur2 = c2.cursor()
            cur2.execute("SELECT count(*), md5(coalesce(string_agg(t::text, '|' ORDER BY symbol, date), '')) FROM ml_breakout_dataset_v2 t")
            return cur2.fetchone()
    return fingerprint


# ---------------------------------------------------------------------------------------------------- the dataset is untouched
def test_scan_only_never_reads_writes_or_truncates_the_ml_dataset(world, monkeypatch, capsys):
    load_all(world)
    fp = guard_dataset(world)
    before = fp()
    monkeypatch.setattr(bd, "load_sectors", lambda conn: (_ for _ in ()).throw(AssertionError("scan-only must not read the dataset inputs")))
    monkeypatch.setattr(bd, "process_symbol", lambda *a, **k: (_ for _ in ()).throw(AssertionError("scan-only must not build dataset rows")))
    s = world.sessions[1]
    assert run_builder(world, monkeypatch, "--scan-only", "--session", s.isoformat(), latest=s) == 0
    assert fp() == before and before[0] == 2                                        # same rows, same content, no DML reached the table (the trigger would have raised)
    rows = scans(world)
    assert len(rows) == 1 and rows[0][2] == "complete" and rows[0][1] == s and rows[0][7].endswith("#scan_only@v1")
    out = capsys.readouterr().out
    assert "Done (scan-only)" in out and "Cleared ml_breakout_dataset_v2" not in out and "the ML dataset was not touched" in out


def test_scan_only_cannot_be_combined_with_a_dataset_rebuild_and_needs_an_explicit_session(world, monkeypatch):
    load_all(world)
    s = world.sessions[1]
    for argv in (["--scan-only"], ["--scan-only", "--replace", "--session", s.isoformat()], ["--scan-only", "--test", "S0001", "--session", s.isoformat()],
                 ["--scan-only", "--limit", "5", "--session", s.isoformat()]):
        assert run_builder(world, monkeypatch, *argv, latest=s) == 2                 # argparse error
    assert scans(world) == []


# ---------------------------------------------------------------------------------------------------- same detection, preserved first detection, idempotency
def test_scan_only_detects_exactly_what_the_full_builder_detects(world):
    load_all(world)
    inject_split(world)
    inject_split(world, symbol="S0007", from_k=FW.WARMUP - 9, factor=0.1)
    with world.connect() as c:
        px = bd.load_prices(c, [f"S{i:04d}" for i in range(FW.SMALL_UNIVERSE)])
    full, only = [], []
    for sym, p in px.groupby("symbol", sort=False):
        _ds, d = bd.process_symbol(sym, p, None, bd.pd.Timestamp("2018-01-01"))
        if len(d):
            full.append(d)
        o = bd.symbol_discontinuities(sym, p)
        if len(o):
            only.append(o)
    f = bd.pd.concat(full, ignore_index=True) if full else bd.pd.DataFrame()
    o = bd.pd.concat(only, ignore_index=True) if only else bd.pd.DataFrame()
    assert len(f) >= 2 and f.reset_index(drop=True).equals(o.reset_index(drop=True))


def test_scan_only_preserves_original_first_detected_stamps_and_is_idempotent_across_both_modes(world, monkeypatch, capsys):
    load_all(world)
    inject_split(world)
    s = world.sessions[1]
    assert run_builder(world, monkeypatch, "--replace", "--session", s.isoformat()) == 0          # the pipeline's own full run: stamps are born here
    first = disc(world)
    assert first
    full_scan = scans(world)
    assert len(full_scan) == 1
    for _ in range(2):
        assert run_builder(world, monkeypatch, "--scan-only", "--session", s.isoformat(), latest=s) == 0
    assert disc(world) == first                                                       # same rows, same first-detected stamps
    assert len(scans(world)) == 1                                                      # identical input and result: the same evidence, no new row
    assert "already recorded for this exact input and result" in capsys.readouterr().out
    assert scans(world)[0][6] == full_scan[0][6]                                       # the FIRST completion time stands


def test_scan_only_prunes_only_what_a_complete_scan_no_longer_detects(world, monkeypatch):
    load_all(world)
    with world.connect() as c:
        c.cursor().execute("INSERT INTO price_discontinuities (symbol, date, kind, detected_at) VALUES ('ZZZZ', DATE '2026-01-05', 'jump_up', now())")
        c.commit()
    s = world.sessions[1]
    assert run_builder(world, monkeypatch, "--scan-only", "--session", s.isoformat(), latest=s) == 0
    assert not any(r[0] == "ZZZZ" for r in disc(world))


# ---------------------------------------------------------------------------------------------------- concurrent price change, late bars, session guards
def test_a_price_change_during_the_scan_is_refused_failed_row_and_the_prune_rolls_back(world, monkeypatch, capsys):
    load_all(world)
    with world.connect() as c:
        c.cursor().execute("INSERT INTO price_discontinuities (symbol, date, kind, detected_at) VALUES ('ZZZZ', DATE '2026-01-05', 'jump_up', now())")
        c.commit()
    s = world.sessions[1]
    real_load = bd.load_prices
    calls = {"n": 0}

    def load_then_a_vendor_correction(conn, symbols):
        out = real_load(conn, symbols)
        calls["n"] += 1
        if calls["n"] == 1:
            with world.connect() as c2:                                                # a writer changes a bar while the scan is reading
                c2.cursor().execute("UPDATE stock_prices SET close = close + 0.01 WHERE symbol = 'S0010' AND date = %s", (world.sessions[0],))
                c2.commit()
        return out
    monkeypatch.setattr(bd, "load_prices", load_then_a_vendor_correction)
    assert run_builder(world, monkeypatch, "--scan-only", "--session", s.isoformat(), latest=s) == 1
    assert [(r[2], r[3]) for r in scans(world)] == [("failed", "input_changed_during_scan")]
    assert "REFUSED by the database" in capsys.readouterr().err
    assert any(r[0] == "ZZZZ" for r in disc(world))                                    # the prune was part of the refused transaction
    monkeypatch.setattr(bd, "load_prices", real_load)
    assert run_builder(world, monkeypatch, "--scan-only", "--session", s.isoformat(), latest=s) == 0       # a clean retry completes
    assert [r[2] for r in scans(world)] == ["failed", "complete"]


def test_a_straggler_bar_between_two_scans_makes_a_second_matching_scan_and_never_alters_the_first(world, monkeypatch):
    load_all(world)
    s = world.sessions[1]
    assert run_builder(world, monkeypatch, "--scan-only", "--session", s.isoformat(), latest=s) == 0
    first = scans(world)[0]
    with world.connect() as c:                                                          # a late bar for a symbol new to the table, dated the scan session
        c.cursor().execute("INSERT INTO stock_prices (symbol, date, open, high, low, close, volume) VALUES ('S9999', %s, 10, 11, 9, 10, 1000)", (s,))
        c.commit()
    with world.connect() as c:
        ev = inputs.discontinuity_scan_evidence(c, s)
    assert not ev["ok"] and ev["reason"] == inputs.SCAN_INPUT_MISMATCH                  # stale: the strict fingerprint rule is unchanged
    assert run_builder(world, monkeypatch, "--scan-only", "--session", s.isoformat(), latest=s) == 0
    rows = scans(world)
    assert len(rows) == 2 and rows[0] == first and rows[1][4] != first[4]               # history is append-only: the first row is untouched
    with world.connect() as c:
        assert inputs.discontinuity_scan_evidence(c, s)["detail"]["scan_id"] == rows[1][0]      # the scan of the CURRENT prices is the one that matches


def test_the_target_session_must_be_the_latest_completed_market_session(world, monkeypatch, capsys):
    load_all(world)
    s0, s1 = world.sessions[0], world.sessions[1]
    assert run_builder(world, monkeypatch, "--scan-only", "--session", s0.isoformat(), latest=s1) == 3
    assert [(r[2], r[3]) for r in scans(world)] == [("failed", "session_not_latest_completed")]
    assert "not the latest completed market session" in capsys.readouterr().err
    assert run_builder(world, monkeypatch, "--scan-only", "--session", "latest-completed", latest=s1) == 0           # 'latest-completed' resolves from the calendar
    assert [r[1] for r in scans(world, "status = 'complete'")] == [s1]


def test_a_session_whose_bar_is_not_the_newest_loaded_is_refused_even_if_the_calendar_names_it(world, monkeypatch):
    load_all(world)
    s0 = world.sessions[0]                                                              # the calendar says s0, but the s1 bar is already stored: the detector reads all of it
    assert run_builder(world, monkeypatch, "--scan-only", "--session", s0.isoformat(), latest=s0) == 3
    assert [(r[2], r[3]) for r in scans(world)] == [("failed", "price_data_not_at_session")]


def test_without_a_trustworthy_calendar_nothing_is_scanned_and_nothing_is_recorded(world, monkeypatch, capsys):
    load_all(world)
    s = world.sessions[1]
    assert run_builder(world, monkeypatch, "--scan-only", "--session", s.isoformat(), latest=RuntimeError("alpaca down")) == 5
    assert scans(world) == [] and "no trustworthy market calendar" in capsys.readouterr().err


# ---------------------------------------------------------------------------------------------------- end to end with the collector
def test_the_collector_accepts_scan_only_evidence_and_the_first_time_path_stays_strict(world, monkeypatch):
    strat, steps = FW.start_world(world, with_capture=False)
    world.load_session(0, scan=False)
    s = world.sessions[0]
    world.now = C.fire_instants_utc(s)[0] + timedelta(minutes=1)
    rep = O.run_session(world.connect, s, steps, apply=True, grace_days=1, code_ref="t", clock=world.clock)
    assert rep.verdict != C.COMPLETE and any(x.name == C.STEP_OBSERVE and x.outcome == C.FAILED and "scan_missing" in (x.error or "") for x in rep.steps)
    assert world.count("market_snapshot") == 0
    assert run_builder(world, monkeypatch, "--scan-only", "--session", s.isoformat(), latest=s) == 0
    with world.connect() as c:                                                           # the real scan stamps completion with the REAL clock; the world's clock
        c.cursor().execute("ALTER TABLE price_discontinuity_scan DISABLE TRIGGER USER")  # is simulated, so move it inside the window (test-only forging, disposable schema)
        c.cursor().execute("UPDATE price_discontinuity_scan SET finished_at = %s", (inputs.knowledge_cutoff(s) - timedelta(hours=1),))
        for n in FW.SCAN_TRIGGERS:
            c.cursor().execute(f'ALTER TABLE price_discontinuity_scan ENABLE ALWAYS TRIGGER "{n}"')
        c.commit()
    world.now = C.fire_instants_utc(s)[1] + timedelta(minutes=1)
    assert O.run_session(world.connect, s, steps, apply=True, grace_days=1, code_ref="t", clock=world.clock).verdict == C.COMPLETE
