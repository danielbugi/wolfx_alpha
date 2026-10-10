"""R1: the collector's `observe` step reuses a fully committed, verified snapshot (read-only) before it looks at current live-price freshness.

Proven, with the real writers in a throwaway schema:
  * a completed session is reused with ZERO writes (every evidence table is byte-identical before and after, and neither the Market Intelligence runner nor any write
    path is even called) when live prices change after capture (a late bar): historical validity at capture time is not undone by a later price change;
  * the strict first-time path is unchanged: a late bar BEFORE the snapshot exists keeps the session INCOMPLETE until a new matching scan exists;
  * a partial snapshot, an inconsistent one (wrong window, no scan before it, mixed runs, short relative-strength rows) fails closed, permanently, and is never written over;
  * without R1 (mutation) the same completed session turns into a false INCOMPLETE."""
from datetime import timedelta

import pytest

import forward_world as FW
from forward_collection import contract as C
from forward_collection import orchestrator as O
from forward_collection import steps as S
from market_intelligence import inputs

EVIDENCE = ("universe_snapshot", "market_snapshot", "sector_snapshot", "stock_relative_strength", "price_discontinuity_scan", "price_discontinuities", "forward_return_label")


@pytest.fixture
def world(monkeypatch):
    FW.scale_universe_minimums(monkeypatch)
    yield from FW.make_world(n_days=16, n_stocks=FW.SMALL_UNIVERSE)


def fingerprint(w):
    out = {}
    with w.connect() as c:
        cur = c.cursor()
        for t in EVIDENCE:
            cur.execute(f"SELECT count(*), md5(coalesce(string_agg(x::text, '|' ORDER BY x::text), '')) FROM {t} x")
            out[t] = cur.fetchone()
    return out


def collect(w, steps, k, which=0, apply=True):
    w.now = C.fire_instants_utc(w.sessions[k])[which] + timedelta(minutes=1)
    return O.run_session(w.connect, w.sessions[k], steps, apply=apply, grace_days=1, code_ref="t", clock=w.clock)


def observe(rep):
    return next(s for s in rep.steps if s.name == C.STEP_OBSERVE)


def late_bar(w, k):
    """A straggler: a bar dated the session for a symbol the table has not seen. It changes the whole-table price fingerprint, nothing else."""
    with w.connect() as c:
        c.cursor().execute("INSERT INTO stock_prices (symbol, date, open, high, low, close, volume) VALUES ('S9999', %s, 10, 11, 9, 10, 1000)", (w.sessions[k],))
        c.commit()


def forge(w, table, sql, params=()):
    """TEST ONLY (disposable schema): run a statement on an immutable table with its triggers briefly off, then ENABLE ALWAYS them again."""
    with w.connect() as c:
        cur = c.cursor()
        cur.execute("SELECT tgname FROM pg_trigger WHERE tgrelid = %s::regclass AND NOT tgisinternal", (table,))
        names = [r[0] for r in cur.fetchall()]
        cur.execute(f"ALTER TABLE {table} DISABLE TRIGGER USER")
        cur.execute(sql, params)
        for n in names:
            cur.execute(f'ALTER TABLE {table} ENABLE ALWAYS TRIGGER "{n}"')
        c.commit()


def completed_session(w):
    strat, steps = FW.start_world(w, with_capture=False)
    w.load_session(0)
    rep = collect(w, steps, 0)
    assert rep.verdict == C.COMPLETE, rep.to_dict()
    return steps


# ---------------------------------------------------------------------------------------------------- reuse
def test_a_completed_session_is_reused_with_zero_writes_after_a_late_price_change(world, monkeypatch):
    steps = completed_session(world)
    before = fingerprint(world)
    assert before["market_snapshot"][0] == 1 and before["stock_relative_strength"][0] > 0
    late_bar(world, 0)                                                                # live prices now differ from the scanned state
    with world.connect() as c:
        assert not inputs.discontinuity_scan_evidence(c, world.sessions[0])["ok"]      # the strict check WOULD refuse to create this snapshot today
    monkeypatch.setattr(S.mi_runner, "run", lambda *a, **k: (_ for _ in ()).throw(AssertionError("reuse must not call the runner (no recompute, no write)")))
    rep = collect(world, steps, 0, which=1)                                           # the 08:15 recovery fire
    assert rep.verdict == C.COMPLETE and rep.exit_code() == C.EXIT_COMPLETE, rep.to_dict()
    o = observe(rep)
    assert o.outcome == C.ALREADY and o.detail["reuse"] is True and o.detail["evidence_basis"] == "committed_snapshot_valid_at_capture"
    assert o.detail["live_scan_evidence_ok"] is False and o.detail["live_scan_evidence_reason"] == inputs.SCAN_INPUT_MISMATCH    # reported, not acted on
    after = dict(fingerprint(world))
    after.pop("stock_relative_strength"), before.pop("stock_relative_strength")        # (compared below with the others)
    assert after == before
    assert collect(world, steps, 0, which=1).verdict == C.COMPLETE                      # and again: idempotent


def test_a_completed_session_is_reused_byte_for_byte_every_evidence_table_unchanged(world, monkeypatch):
    steps = completed_session(world)
    before = fingerprint(world)
    late_bar(world, 0)
    monkeypatch.setattr(S.mi_runner, "run", lambda *a, **k: (_ for _ in ()).throw(AssertionError("no runner")))
    assert collect(world, steps, 0, which=1).verdict == C.COMPLETE
    assert fingerprint(world) == before


def test_a_dry_run_of_a_completed_session_reports_already_present_without_touching_anything(world, monkeypatch):
    steps = completed_session(world)
    before = fingerprint(world)
    late_bar(world, 0)
    monkeypatch.setattr(S.mi_runner, "run", lambda *a, **k: (_ for _ in ()).throw(AssertionError("no runner")))
    rep = collect(world, steps, 0, which=1, apply=False)
    assert rep.verdict == C.DRY_RUN and observe(rep).outcome == C.ALREADY
    assert fingerprint(world) == before


def test_without_reuse_the_same_completed_session_would_turn_into_a_false_incomplete(world, monkeypatch):
    """Mutation proof of the defect R1 fixes: with the reuse check disabled, a late bar after capture makes the recovery fire fail a session that is complete."""
    steps = completed_session(world)
    late_bar(world, 0)
    monkeypatch.setattr(S, "committed_snapshot", lambda *a, **k: {"status": S.SNAPSHOT_ABSENT, "problems": [], "detail": {}})
    rep = collect(world, steps, 0, which=1)
    o = observe(rep)
    assert rep.verdict != C.COMPLETE and o.outcome == C.FAILED and "scan_input_mismatch" in (o.error or "")


# ---------------------------------------------------------------------------------------------------- late insertion BEFORE creation: the strict path is unchanged
def test_a_late_bar_before_the_snapshot_exists_keeps_the_session_incomplete_until_a_matching_scan_exists(world):
    strat, steps = FW.start_world(world, with_capture=False)
    world.load_session(0)
    late_bar(world, 0)                                                                 # after the scan, before any snapshot
    for which in (0, 1):
        rep = collect(world, steps, 0, which=which)
        o = observe(rep)
        assert rep.verdict == C.INCOMPLETE and o.outcome == C.FAILED and "scan_input_mismatch" in (o.error or ""), rep.to_dict()
    assert world.count("market_snapshot") == 0 and world.count("stock_relative_strength") == 0
    world.record_scan(0)                                                               # a scan of the CURRENT prices
    assert collect(world, steps, 0, which=1).verdict == C.COMPLETE and world.count("market_snapshot") == 1


# ---------------------------------------------------------------------------------------------------- partial / inconsistent: fail closed, never written over
RS_GAPS = ["DELETE FROM stock_relative_strength WHERE horizon_sessions = 20",
           "DELETE FROM stock_relative_strength WHERE horizon_sessions = 5 AND symbol = 'S0001'",
           "DELETE FROM stock_relative_strength"]


def rs_rows(w):
    with w.connect() as c:
        cur = c.cursor()
        cur.execute("SELECT x::text FROM stock_relative_strength x ORDER BY x::text")
        return {r[0] for r in cur.fetchall()}


def snapshot_status(w, k=0):
    with w.connect() as c:
        out = S.committed_snapshot(c, w.sessions[k], S.FEATURE_SET_VERSION, C.decision_deadline(w.sessions[k], 1))
        c.rollback()
    return out["status"]


@pytest.mark.parametrize("sql", RS_GAPS)
def test_repair_completes_only_the_missing_rs_rows_leaves_every_committed_row_untouched_and_verifies(world, sql):
    """Owner-approved repair: universe/market/sector intact, only relative-strength rows missing, strict freshness holds (prices unchanged)."""
    steps = completed_session(world)
    forge(world, "stock_relative_strength", sql)
    full_rows = None
    with world.connect() as c:
        cur = c.cursor()
        cur.execute("SELECT count(*) FROM stock_relative_strength")
        remaining = cur.fetchone()[0]
    assert snapshot_status(world) == S.SNAPSHOT_REPAIRABLE
    kept = rs_rows(world)
    others_before = {t: v for t, v in fingerprint(world).items() if t != "stock_relative_strength"}
    rep = collect(world, steps, 0, which=1)
    assert rep.verdict == C.COMPLETE, [(x.name, x.outcome, x.error) for x in rep.steps]
    o = observe(rep)
    assert o.outcome == C.OK and o.detail["repair"] is True and o.detail["stock_rs_written"] > 0 and not o.detail["created"] and o.detail["sectors_written"] == 0
    after = rs_rows(world)
    assert kept <= after                                                                 # every row that existed is byte-identical
    assert len(after) == 3 * FW.SMALL_UNIVERSE and len(after) - len(kept) == o.detail["stock_rs_written"] and len(kept) == remaining
    assert {t: v for t, v in fingerprint(world).items() if t != "stock_relative_strength"} == others_before   # nothing else was written
    assert snapshot_status(world) == S.SNAPSHOT_VALID                                    # verified complete after the repair
    # idempotent: the next fire reuses the now-valid snapshot and writes nothing
    again = fingerprint(world)
    rep = collect(world, steps, 0, which=1)
    assert rep.verdict == C.COMPLETE and observe(rep).detail.get("reuse") is True and fingerprint(world) == again


@pytest.mark.parametrize("sql", RS_GAPS[:2])
def test_repair_is_refused_with_no_writes_when_live_prices_no_longer_match_the_scan(world, sql):
    steps = completed_session(world)
    forge(world, "stock_relative_strength", sql)
    late_bar(world, 0)                                                                   # the strict live-price freshness gate fails
    before = fingerprint(world)
    rep = collect(world, steps, 0, which=1)
    o = observe(rep)
    assert rep.verdict == C.INCOMPLETE and o.outcome == C.FAILED and "scan_input_mismatch" in (o.error or "")
    assert fingerprint(world) == before


def test_repair_is_refused_with_no_writes_when_the_recomputation_would_differ_from_the_stored_evidence(world):
    """Fresh scan, fresh prices, but the live universe grew after the committed snapshot: the stored rows cannot be completed truthfully."""
    steps = completed_session(world)
    forge(world, "stock_relative_strength", RS_GAPS[0])
    late_bar(world, 0)
    world.record_scan(0)                                                                 # the freshness gate now passes
    before = fingerprint(world)
    rep = collect(world, steps, 0, which=1)
    o = observe(rep)
    assert rep.verdict == C.INCOMPLETE and o.outcome == C.FAILED and o.detail["reason"] == C.REASON_REPAIR_REFUSED, [(x.name, x.outcome, x.error) for x in rep.steps]
    assert "differs from the stored" in (o.error or "")
    assert fingerprint(world) == before                                                  # rolled back: not one row was written


def test_repair_is_atomic_a_failure_after_some_inserts_leaves_nothing_behind(world, monkeypatch):
    steps = completed_session(world)
    forge(world, "stock_relative_strength", RS_GAPS[2])
    before = fingerprint(world)
    real = S.mi_runner.store.write_stock_rs

    def write_then_die(cur, *a, **k):
        real(cur, *a, **k)
        raise RuntimeError("simulated crash after the inserts")
    monkeypatch.setattr(S.mi_runner.store, "write_stock_rs", write_then_die)
    rep = collect(world, steps, 0, which=1)
    assert rep.verdict == C.INCOMPLETE and observe(rep).outcome == C.FAILED
    assert fingerprint(world) == before and world.count("stock_relative_strength") == 0
    monkeypatch.setattr(S.mi_runner.store, "write_stock_rs", real)                      # (not undo(): that would also drop the world's own patches)
    rep = collect(world, steps, 0, which=1)                                              # the retry then repairs
    assert rep.verdict == C.COMPLETE and world.count("stock_relative_strength") == 3 * FW.SMALL_UNIVERSE


def test_repair_is_never_attempted_on_an_inconsistent_or_sector_partial_snapshot(world, monkeypatch):
    steps = completed_session(world)
    forge(world, "stock_relative_strength", RS_GAPS[0])
    forge(world, "stock_relative_strength", "UPDATE stock_relative_strength SET run_content_hash = repeat('a', 64) WHERE horizon_sessions = 5")
    assert snapshot_status(world) == S.SNAPSHOT_INCONSISTENT                             # a gap PLUS a foreign run is not repairable
    monkeypatch.setattr(S.mi_runner, "run", lambda *a, **k: (_ for _ in ()).throw(AssertionError("no repair on inconsistent evidence")))
    before = fingerprint(world)
    rep = collect(world, steps, 0, which=1)
    assert rep.verdict == C.MISSED and fingerprint(world) == before


def test_a_snapshot_missing_its_sector_rows_is_partial_fails_closed_permanently_and_is_never_written_over(world, monkeypatch):
    steps = completed_session(world)
    forge(world, "sector_snapshot", "DELETE FROM sector_snapshot")
    before = fingerprint(world)
    monkeypatch.setattr(S.mi_runner, "run", lambda *a, **k: (_ for _ in ()).throw(AssertionError("a partial snapshot must not be recomputed or completed")))
    rep = collect(world, steps, 0, which=1)
    o = observe(rep)
    assert o.outcome == C.FAILED and o.detail["reason"] == C.REASON_SNAPSHOT_PARTIAL
    assert rep.verdict == C.MISSED and rep.exit_code() == C.EXIT_MISSED
    assert fingerprint(world) == before


@pytest.mark.parametrize("name,table,sql,params,needle", [
    ("two runs mixed", "stock_relative_strength", "UPDATE stock_relative_strength SET run_content_hash = repeat('a', 64) WHERE horizon_sessions = 5", (), "do not belong to one run"),
    ("no scan before the snapshot", "price_discontinuity_scan", "DELETE FROM price_discontinuity_scan", (), "no complete price-discontinuity scan exists"),
])
def test_an_inconsistent_snapshot_fails_closed_and_is_never_rewritten(world, monkeypatch, name, table, sql, params, needle):
    steps = completed_session(world)
    forge(world, table, sql, params)
    before = fingerprint(world)
    monkeypatch.setattr(S.mi_runner, "run", lambda *a, **k: (_ for _ in ()).throw(AssertionError("no recompute")))
    rep = collect(world, steps, 0, which=1)
    o = observe(rep)
    assert o.outcome == C.FAILED and o.detail["reason"] == C.REASON_SNAPSHOT_INCONSISTENT and needle in " ".join(o.detail["problems"]), (name, o.detail)
    assert rep.verdict == C.MISSED and fingerprint(world) == before


def test_a_snapshot_stamped_outside_the_allowed_window_or_before_its_scan_is_inconsistent(world, monkeypatch):
    steps = completed_session(world)
    s = world.sessions[0]
    monkeypatch.setattr(S.mi_runner, "run", lambda *a, **k: (_ for _ in ()).throw(AssertionError("no recompute")))
    forge(world, "price_discontinuity_scan", "UPDATE price_discontinuity_scan SET finished_at = %s", (world.at(s, 23, days_after=1),))       # the scan finished AFTER the snapshot
    rep = collect(world, steps, 0, which=1)
    assert observe(rep).detail["reason"] == C.REASON_SNAPSHOT_INCONSISTENT and "later than the first snapshot row" in " ".join(observe(rep).detail["problems"])
    forge(world, "price_discontinuity_scan", "UPDATE price_discontinuity_scan SET finished_at = %s", (world.at(s, 23, 30),))
    assert collect(world, steps, 0, which=1).verdict == C.COMPLETE                      # restored: valid again
    forge(world, "market_snapshot", "UPDATE market_snapshot SET captured_at = %s", (inputs.knowledge_cutoff(s) + timedelta(hours=1),))        # stamped after the cutoff
    rep = collect(world, steps, 0, which=1)
    assert observe(rep).detail["reason"] == C.REASON_SNAPSHOT_INCONSISTENT and "stamped outside the allowed window" in " ".join(observe(rep).detail["problems"])


def test_the_verdict_for_partial_and_inconsistent_is_never_complete_even_if_verify_would_pass_elsewhere(world):
    steps = completed_session(world)
    forge(world, "price_discontinuity_scan", "DELETE FROM price_discontinuity_scan")
    rep = collect(world, steps, 0, which=1)
    assert rep.verdict != C.COMPLETE and rep.exit_code() in C.ALERT_EXIT_CODES
