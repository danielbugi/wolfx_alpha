"""First-detection provenance of `price_discontinuities`, against real Postgres in a throwaway schema.

A discontinuity has two times: `date` (it happened) and `detected_at` (this system first saw it). Only the second says what a live run could have known.
Proven here: the dataset builder keeps `detected_at` across repeated rebuilds (the nightly `--replace` used to delete and re-create every row, so every row was
re-stamped after every session and every `observed` run was refused); a genuinely new discontinuity gets a genuine stamp; a legacy row whose provenance was
overwritten is never given an earlier (fabricated) stamp; the reader compares the DETECTION instant to the session's knowledge cutoff, never the event date to
the session date; and an untrustworthy stamp fails closed."""
import inspect
import os
import sys
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

import mi_fixtures  # noqa: F401  (path setup)
from mi_runner_fixtures import EARLY, ROOT, T, record_scan_on, runner_env  # noqa: F401
from market_intelligence import inputs, runner

sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "ml_training", "config"))
from ml_training.data_preparation import build_dataset as bd  # noqa: E402

UTC = timezone.utc
CUTOFF_T = datetime(2026, 10, 1, 16, 0, tzinfo=UTC)         # noon New York (EDT) on the day after T = 2026-09-30


def _put(conn, symbol, event, kind, detected_utc, ratio=None):
    """Insert a row stamped with an exact UTC instant, expressed in the database session's own TimeZone (how the writer's clock_timestamp() stores it)."""
    conn.cursor().execute(
        "INSERT INTO price_discontinuities (symbol, date, kind, prev_close, close, ratio, detected_at) "
        "VALUES (%s, %s, %s, 100, 150, %s, (%s::timestamptz AT TIME ZONE current_setting('TimeZone')))",
        (symbol, event, kind, ratio, detected_utc))


def _stamp(conn, symbol, event, kind="jump_up"):
    cur = conn.cursor()
    cur.execute("SELECT detected_at FROM price_discontinuities WHERE symbol=%s AND date=%s AND kind=%s", (symbol, event, kind))
    row = cur.fetchone()
    return row[0] if row else None


@pytest.fixture
def clean(runner_env):
    def wipe():
        with runner_env.connect() as c:
            c.cursor().execute("DELETE FROM price_discontinuities")
            c.commit()
    wipe()
    yield runner_env
    wipe()


def _classify(env, session=T):
    with env.connect() as c:
        return inputs.load_discontinuities(c, session)[1:]


def _observed(env):
    return runner.run(env.connect, T, provenance="observed", apply=False, feature_set_version=env.fsv(), code_ref="t")


# ------------------------------------------------------------------ the knowledge cutoff
@pytest.mark.parametrize("session,expected", [
    (date(2026, 9, 30), datetime(2026, 10, 1, 16, 0, tzinfo=UTC)),       # EDT
    (date(2026, 12, 7), datetime(2026, 12, 8, 17, 0, tzinfo=UTC)),       # EST
    (date(2026, 11, 2), datetime(2026, 11, 3, 17, 0, tzinfo=UTC)),       # the day after US DST ended on Nov 1: noon is EST
    (date(2026, 10, 30), datetime(2026, 10, 31, 16, 0, tzinfo=UTC)),     # Friday session: the cutoff is the next calendar day, a Saturday
])
def test_the_knowledge_cutoff_is_noon_new_york_on_the_next_calendar_day(session, expected):
    assert inputs.knowledge_cutoff(session) == expected


def test_the_cutoff_follows_the_overnight_cycle_and_precedes_the_next_one_in_every_regime():
    """Every collector fire of a session is before its cutoff; the next evening's pipeline start (23:45 Asia/Jerusalem) is after it."""
    jer = ZoneInfo("Asia/Jerusalem")
    for s in [date(2026, 3, 16), date(2026, 7, 6), date(2026, 10, 26), date(2026, 12, 7)]:
        cut = inputs.knowledge_cutoff(s)
        nxt = s + timedelta(days=1)
        for hh, mm in ((6, 45), (8, 15)):
            assert datetime.combine(nxt, time(hh, mm), jer).astimezone(UTC) < cut, (s, hh)
        assert datetime.combine(nxt, time(23, 45), jer).astimezone(UTC) > cut, s


# ------------------------------------------------------------------ the reader: detection instant vs the cutoff
def test_a_discontinuity_found_in_the_overnight_cycle_that_loaded_the_session_was_known_to_the_run(clean):
    with clean.connect() as c:
        _put(c, "S0001", T, "jump_up", datetime(2026, 10, 1, 0, 30, tzinfo=UTC))           # the session's own event, detected 00:30Z by the nightly rebuild
        _put(c, "S0002", date(2026, 9, 10), "jump_down", datetime(2026, 9, 14, 1, 0, tzinfo=UTC))
        c.commit()
    assert _classify(clean) == (0, 0)
    with clean.connect() as c:                                                              # the builder's scan evidence for the table as it now is
        record_scan_on(c.cursor(), T)
        c.commit()
    assert _observed(clean).applied is False                                              # not refused


def test_a_discontinuity_first_detected_after_the_cutoff_is_retroactive_whatever_its_event_date(clean):
    """Late discovery: an OLD event (a month before the session) that this system only found after the cutoff is retroactive; so is a recent one."""
    with clean.connect() as c:
        _put(c, "S0001", date(2026, 8, 20), "jump_up", CUTOFF_T + timedelta(hours=9))
        _put(c, "S0002", date(2026, 9, 29), "jump_up", CUTOFF_T + timedelta(days=3))
        _put(c, "S0003", date(2026, 9, 1), "jump_down", datetime(2026, 9, 2, 3, 0, tzinfo=UTC))      # known long before: not late
        c.commit()
    assert _classify(clean) == (2, 0)
    with pytest.raises(runner.ProvenanceRefused, match="detected after the session"):
        _observed(clean)
    rep = runner.run(clean.connect, T, provenance="reconstructed", apply=False, feature_set_version=clean.fsv(), code_ref="t")
    assert any("detected after the session" in w for w in rep.warnings)


def test_the_event_date_alone_never_decides_it(clean):
    with clean.connect() as c:
        _put(c, "S0001", T, "jump_up", CUTOFF_T - timedelta(hours=14))                       # event ON the session date, detected overnight: known
        _put(c, "S0002", date(2026, 7, 1), "jump_up", CUTOFF_T + timedelta(minutes=1))       # event three months ago, detected a minute after the cutoff: late
        c.commit()
    assert _classify(clean) == (1, 0)


def test_the_session_boundary_is_exact_the_cutoff_instant_is_known_one_second_later_is_not(clean):
    with clean.connect() as c:
        _put(c, "S0001", date(2026, 9, 20), "jump_up", CUTOFF_T)
        _put(c, "S0002", date(2026, 9, 20), "jump_up", CUTOFF_T + timedelta(seconds=1))
        c.commit()
    assert _classify(clean) == (1, 0)
    with clean.connect() as c:
        cur = c.cursor()
        cur.execute("SELECT symbol FROM price_discontinuities WHERE (detected_at AT TIME ZONE current_setting('TimeZone')) > %s", (CUTOFF_T,))
        assert [r[0] for r in cur.fetchall()] == ["S0002"]


def test_eligibility_is_per_session(clean):
    """A row first seen on 2026-09-28 is retroactive for 2026-09-25 but known by 2026-09-30."""
    with clean.connect() as c:
        _put(c, "S0001", date(2026, 9, 24), "jump_up", datetime(2026, 9, 28, 1, 0, tzinfo=UTC))
        c.commit()
    assert _classify(clean, EARLY) == (1, 0)
    assert _classify(clean, T) == (0, 0)


def test_a_stamp_in_the_future_of_the_database_clock_is_untrustworthy_and_fails_closed(clean):
    with clean.connect() as c:
        _put(c, "S0001", date(2026, 9, 20), "jump_up", datetime.now(UTC) + timedelta(days=2))
        c.commit()
    assert _classify(clean) == (0, 1)
    with pytest.raises(runner.ProvenanceRefused, match="no trustworthy detection time"):
        _observed(clean)
    rep = runner.run(clean.connect, T, provenance="reconstructed", apply=False, feature_set_version=clean.fsv(), code_ref="t")
    assert any("no trustworthy detection time" in w for w in rep.warnings)


def test_a_missing_stamp_cannot_exist_the_column_is_not_null(clean):
    import psycopg2
    with clean.connect() as c:
        with pytest.raises(psycopg2.errors.NotNullViolation):
            c.cursor().execute("INSERT INTO price_discontinuities (symbol, date, kind, detected_at) VALUES ('S0001', %s, 'jump_up', NULL)",
                               (date(2026, 9, 20),))


# ------------------------------------------------------------------ the writer: repeated rebuilds keep first detection
ROW = ("S0001", date(2026, 9, 20), "jump_up", 100.0, 150.0, 1.5)


def _upsert(env, rows):
    with env.connect() as c:
        bd.upsert_discontinuities(c.cursor(), rows)
        c.commit()


def _db_now(env):
    with env.connect() as c:
        cur = c.cursor()
        cur.execute("SELECT clock_timestamp() AT TIME ZONE current_setting('TimeZone')")
        return cur.fetchone()[0]


def test_a_new_discontinuity_gets_a_genuine_first_detected_stamp(clean):
    before = _db_now(clean)
    _upsert(clean, [ROW])
    after = _db_now(clean)
    with clean.connect() as c:
        assert before <= _stamp(c, "S0001", ROW[1]) <= after


def test_repeated_rebuilds_keep_the_original_first_detected_stamp_and_are_idempotent(clean):
    with clean.connect() as c:
        _put(c, "S0001", ROW[1], "jump_up", datetime(2026, 9, 21, 1, 0, tzinfo=UTC), ratio=1.5)
        c.commit()
        original = _stamp(c, "S0001", ROW[1])
    for _ in range(4):                                                                    # four nightly rebuilds that re-detect the same fact
        _upsert(clean, [ROW])
    with clean.connect() as c:
        assert _stamp(c, "S0001", ROW[1]) == original
        cur = c.cursor()
        cur.execute("SELECT count(*) FROM price_discontinuities")
        assert cur.fetchone()[0] == 1
    assert _classify(clean) == (0, 0)                                                      # and the row stays eligible, which is the whole point


def test_adjusted_price_jitter_does_not_move_the_stamp_but_the_values_are_refreshed(clean):
    with clean.connect() as c:
        _put(c, "S0001", ROW[1], "jump_up", datetime(2026, 9, 21, 1, 0, tzinfo=UTC), ratio=1.5)
        c.commit()
        original = _stamp(c, "S0001", ROW[1])
    _upsert(clean, [("S0001", ROW[1], "jump_up", 99.99999, 149.99999, 1.5 * (1 + 1e-9))])     # a dividend re-adjusted both closes
    with clean.connect() as c:
        assert _stamp(c, "S0001", ROW[1]) == original
        cur = c.cursor()
        cur.execute("SELECT close FROM price_discontinuities")
        assert float(cur.fetchone()[0]) == pytest.approx(149.99999)


def test_a_materially_restated_discontinuity_is_a_new_fact_with_a_new_stamp(clean):
    with clean.connect() as c:
        _put(c, "S0001", ROW[1], "jump_up", datetime(2026, 9, 21, 1, 0, tzinfo=UTC), ratio=1.5)
        _put(c, "S0002", ROW[1], "jump_up", datetime(2026, 9, 21, 1, 0, tzinfo=UTC), ratio=None)
        c.commit()
        original = _stamp(c, "S0001", ROW[1])
    before = _db_now(clean)
    _upsert(clean, [("S0001", ROW[1], "jump_up", 100.0, 160.0, 1.6), ("S0002", ROW[1], "jump_up", 100.0, 150.0, 1.5)])
    with clean.connect() as c:
        assert _stamp(c, "S0001", ROW[1]) >= before > original                              # restated: known only from now on
        assert _stamp(c, "S0002", ROW[1]) >= before                                         # a ratio that was unknown became known: same


def test_a_legacy_row_with_overwritten_provenance_is_never_given_an_earlier_stamp(clean):
    """Pre-fix rebuilds stamped every row at the rebuild. That stamp is a late upper bound on the true first detection: the fix keeps it exactly, so the row
    stays 'late' for every session it already was late for and is never backdated to look known."""
    legacy = datetime(2026, 10, 2, 0, 15, tzinfo=UTC)                                      # a bulk re-stamp after T's cutoff
    with clean.connect() as c:
        _put(c, "S0001", date(2026, 9, 10), "jump_up", legacy, ratio=1.5)
        c.commit()
        original = _stamp(c, "S0001", date(2026, 9, 10))
    for _ in range(3):
        _upsert(clean, [("S0001", date(2026, 9, 10), "jump_up", 100.0, 150.0, 1.5)])
    with clean.connect() as c:
        assert _stamp(c, "S0001", date(2026, 9, 10)) == original
    assert _classify(clean, T) == (1, 0)                                                   # still retroactive for the session it was already late for


def test_a_later_rebuild_can_never_make_a_historical_session_eligible(clean):
    with clean.connect() as c:
        _put(c, "S0001", date(2026, 9, 25), "jump_up", CUTOFF_T + timedelta(days=2), ratio=1.5)
        c.commit()
    for _ in range(3):
        _upsert(clean, [("S0001", date(2026, 9, 25), "jump_up", 100.0, 150.0, 1.5)])
    assert _classify(clean, T) == (1, 0)
    with pytest.raises(runner.ProvenanceRefused):
        _observed(clean)


def test_prune_removes_only_rows_a_complete_rebuild_no_longer_detects_and_a_reappearance_gets_a_new_stamp(clean):
    with clean.connect() as c:
        _put(c, "S0001", ROW[1], "jump_up", datetime(2026, 9, 21, 1, 0, tzinfo=UTC), ratio=1.5)
        _put(c, "S0002", ROW[1], "jump_up", datetime(2026, 9, 21, 1, 0, tzinfo=UTC), ratio=1.5)
        c.commit()
        keep = _stamp(c, "S0001", ROW[1])
    with clean.connect() as c:
        removed = bd.prune_discontinuities(c.cursor(), {("S0001", ROW[1], "jump_up")})
        c.commit()
        assert removed == 1 and _stamp(c, "S0002", ROW[1]) is None and _stamp(c, "S0001", ROW[1]) == keep
    before = _db_now(clean)
    _upsert(clean, [("S0002", ROW[1], "jump_up", 100.0, 150.0, 1.5)])
    with clean.connect() as c:
        assert _stamp(c, "S0002", ROW[1]) >= before                                         # it vanished, then reappeared: a new fact, a new stamp


def test_main_prunes_only_after_a_complete_replace_and_no_longer_deletes_the_table_up_front():
    src = inspect.getsource(bd.main) + inspect.getsource(bd._build)
    assert "if (args.replace or args.scan_only) and not args.test and not args.limit:" in src and "prune_discontinuities" in src
    assert 'DELETE FROM price_discontinuities"' not in src                                  # the up-front delete that destroyed provenance is gone
    assert "NOW()" not in inspect.getsource(bd.upsert_discontinuities) and "detected_at=NOW()" not in inspect.getsource(bd)


def test_the_pipelines_worst_observed_finish_and_every_retry_are_inside_the_cutoff_in_every_regime():
    """The scan is written by pipeline step 10, so its completion is bounded by the pipeline's slowest observed finish (03:15 Asia/Jerusalem on the 14-day earnings
    re-fetch night) and by the collector's recovery fire; the first attempt (23:45) and retry (01:00) start before it. All of that is before
    the cutoff in the winter, summer and both mismatch windows, so a delayed-but-normal run still qualifies and a run a whole cycle late does not."""
    jer = ZoneInfo("Asia/Jerusalem")
    hh, mm = 3, 15                  # forward_collection.contract.SCHEDULER_DESIGN['pipeline_worst_finish_local'] (not imported: the Market Intelligence tests never import the collector)
    for s in [date(2026, 3, 16), date(2026, 7, 6), date(2026, 10, 26), date(2026, 12, 7)]:
        cut = inputs.knowledge_cutoff(s)
        nxt = s + timedelta(days=1)
        assert datetime.combine(nxt, time(hh, mm), jer).astimezone(UTC) < cut - timedelta(hours=5), s          # at least five hours of margin on the slowest night seen
        assert datetime.combine(nxt, time(1, 0), jer).astimezone(UTC) < cut, s                                 # the 01:00 retry starts inside the window
        assert datetime.combine(nxt, time(23, 45), jer).astimezone(UTC) > cut, s                               # the NEXT cycle starts after it: its scan is for the next session


def test_the_nightly_pipeline_passes_the_session_to_the_dataset_step_so_a_scan_is_recorded():
    text = open(os.path.join(ROOT, "automation_pipeline.sh"), encoding="utf-8").read()
    step10 = [ln for ln in text.splitlines() if ln.startswith("run_step 10 ")]
    assert len(step10) == 1 and 'build_dataset.py --replace "${SESSION_ARGS[@]}"' in step10[0]
    assert 'SESSION_ARGS=(--session "$SESSION_DATE")' in text


# ---------------------------------------------------------------------------------------------------- non-finite ratios (found on production data)
INF, NAN = float("inf"), float("nan")


def _legacy_nonfinite(c, symbol, ratio, kind="jump_up"):
    c.cursor().execute(
        "INSERT INTO price_discontinuities (symbol, date, kind, prev_close, close, ratio, detected_at) "
        "VALUES (%s, %s, %s, 0, 0.0001, %s, (%s::timestamptz AT TIME ZONE current_setting('TimeZone')))",
        (symbol, date(2026, 9, 10), kind, ratio, datetime(2026, 9, 21, 1, 0, tzinfo=UTC)))


@pytest.mark.parametrize("ratio,kind", [(INF, "jump_up"), (INF, "nonpositive"), (-INF, "jump_down"), (NAN, "nonpositive")])
def test_a_non_finite_ratio_that_is_unchanged_keeps_its_first_detected_stamp_across_rebuilds(clean, ratio, kind):
    """Production has 64 legacy rows whose ratio is Infinity (previous close 0). numeric NaN/Infinity made the tolerance test 'greater', which re-stamped them nightly."""
    with clean.connect() as c:
        _legacy_nonfinite(c, "S0001", ratio, kind)
        c.commit()
        original = _stamp(c, "S0001", date(2026, 9, 10), kind)
    for _ in range(3):
        _upsert(clean, [("S0001", date(2026, 9, 10), kind, 0.0, 0.0001, ratio)])
    with clean.connect() as c:
        assert _stamp(c, "S0001", date(2026, 9, 10), kind) == original


@pytest.mark.parametrize("old,new", [(INF, 1.5), (1.5, INF), (NAN, 1.5), (1.5, NAN), (INF, NAN), (INF, -INF), (INF, None), (None, INF)])
def test_a_change_between_a_finite_value_and_a_non_finite_one_is_a_new_fact(clean, old, new):
    with clean.connect() as c:
        _legacy_nonfinite(c, "S0001", old)
        c.commit()
        original = _stamp(c, "S0001", date(2026, 9, 10), "jump_up")
    before = _db_now(clean)
    _upsert(clean, [("S0001", date(2026, 9, 10), "jump_up", 0.0, 0.0001, new)])
    with clean.connect() as c:
        assert _stamp(c, "S0001", date(2026, 9, 10), "jump_up") >= before > original
