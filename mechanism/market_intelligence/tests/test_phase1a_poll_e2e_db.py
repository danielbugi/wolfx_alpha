"""The whole polling cycle of the Phase 1A script (`poll_once`) end to end against real Postgres, with the real SecTransport driven by a fake opener: submissions snapshots are kept,
each new filing's SEC header is fetched, snapshotted and reconciled, the facts are ingested, and a second identical cycle writes nothing. This is the test that would have caught
a snapshot being rolled back and a late-feed filing being refused before reconciliation (both happened during the real-SEC dry run)."""
import io
import json
import os
import sys
import urllib.error
from collections import Counter
from datetime import datetime, timedelta, timezone
from email.message import Message

import pytest

import mi_fixtures
from mi_fixtures import ROOT, conn, connect, mi_env  # noqa: F401
import catalyst_fixtures as C
from edgar_samples import CIK_A, UA, doc, r
from market_intelligence import edgar_collector as E
from market_intelligence import sec_transport as T

sys.path.insert(0, os.path.join(ROOT, "ml_training", "experiments"))
import catalyst_phase1a_dryrun as D  # noqa: E402

UTC = timezone.utc
SUB_URL = E.submissions_url(CIK_A)


class Resp:
    def __init__(self, body):
        self._b, self.status, self.headers = body, 200, Message()

    def read(self, n=-1):
        return self._b

    def close(self):
        pass


def header(acc, wall_et):
    return f"<SEC-HEADER>{acc}.hdr.sgml : 20261015\n<ACCEPTANCE-DATETIME>{wall_et}\nACCESSION NUMBER:\t{acc}\n".encode()


class World:
    """URL -> body (or an HTTPError code). Counts requests per URL."""

    def __init__(self):
        self.docs, self.errors, self.hits = {}, {}, Counter()

    def opener(self, req, timeout):
        url = req.full_url
        self.hits[url] += 1
        if url in self.errors:
            raise urllib.error.HTTPError(url, self.errors[url], "x", Message(), io.BytesIO(b""))
        return Resp(self.docs[url])


def transport(world):
    t = [0.0]
    return T.SecTransport(E.EdgarConfig(UA), opener=world.opener, clock=lambda: t[0], sleep=lambda s: t.__setitem__(0, t[0] + s), wall=lambda: datetime.now(UTC))


def acc(n):
    return f"0000320193-26-{n:06d}"


def hurl(n):
    return f"https://www.sec.gov/Archives/edgar/data/{CIK_A}/{acc(n).replace('-', '')}/{acc(n)}.hdr.sgml"


def cycle(conn, w, run_key, since=datetime(2026, 9, 1).date()):
    cur = conn.cursor()
    tr = transport(w)
    rejected, hstats, offs = Counter(), Counter(), Counter()
    rep, res, ready = D.poll_once(conn, cur, tr, E.EdgarConfig(UA), [CIK_A], since, run_key, rejected, hstats, offs)
    return rep, res, ready, rejected, hstats, offs


def world_with(rows, headers):
    w = World()
    w.docs[SUB_URL] = json.dumps(doc(CIK_A, rows)).encode()
    for n, wall in headers.items():
        w.docs[hurl(n)] = header(acc(n), wall)
    return w


ROWS = [r(acc(1), filed="2026-09-18", accepted="2026-09-19T00:05:11.000Z", items="2.02,9.01"),          # header 16:05:11 ET = 20:05:11Z; the feed says 00:05:11Z next day: 4 h late (the real shift)
        r(acc(2), form="10-Q", filed="2026-09-18", accepted="2026-09-18T12:00:00.000Z", items=""),        # header 08:00:00 ET = 12:00Z: the feed agrees
        r(acc(3), form="NT 10-K", filed="2026-09-17", accepted="2026-09-17T18:00:00.000Z", items="")]     # header 10:00:00 ET = 14:00Z; the feed says 18:00Z: 4 h late
HEADERS = {1: "20260918160511", 2: "20260918080000", 3: "20260917100000"}


def test_a_cycle_keeps_the_snapshots_reconciles_the_acceptance_times_and_ingests(conn):
    w = world_with(ROWS, HEADERS)
    rep, res, ready, rejected, hstats, offs = cycle(conn, w, "run-1")
    assert (res.events_created, res.classifications_created, res.errors) == (3, 3, [])
    assert dict(hstats) == {"header_fetched": 3, "reconciled_feed_late": 2, "reconciled_agree": 1} and dict(offs) == {4.0: 2, 0.0: 1}
    cur = conn.cursor()
    cur.execute("SELECT kind, count(*) FROM source_snapshot GROUP BY 1 ORDER BY 1")
    assert cur.fetchall() == [("filing_header", 3), ("submissions", 1)]
    cur.execute("SELECT source_ref, known_at, session_timing, payload->'acceptance'->>'source' FROM market_event_revision ORDER BY source_ref")
    assert cur.fetchall() == [(acc(1), datetime(2026, 9, 18, 20, 5, 11, tzinfo=UTC), "AMC", "feed_late"),
                              (acc(2), datetime(2026, 9, 18, 12, 0, 0, tzinfo=UTC), "BMO", "agree"),
                              (acc(3), datetime(2026, 9, 17, 14, 0, 0, tzinfo=UTC), "INTRADAY", "feed_late")]


def test_a_second_identical_cycle_writes_nothing_and_fetches_no_header_again(conn):
    w = world_with(ROWS, HEADERS)
    cycle(conn, w, "run-1")
    before = {t: C.count(conn, t) for t in ("market_event_revision", "source_observation", "catalyst_classification", "source_snapshot")}
    hits_before = sum(v for u, v in w.hits.items() if u.endswith(".hdr.sgml"))
    rep, res, ready, rejected, hstats, offs = cycle(conn, w, "run-2")
    assert (res.events_created, res.revisions_created, res.unchanged, res.observations_written, res.classifications_created) == (0, 0, 3, 0, 0)
    assert {t: C.count(conn, t) for t in before} == before                       # not even a duplicate snapshot of the unchanged documents
    assert sum(v for u, v in w.hits.items() if u.endswith(".hdr.sgml")) == hits_before and not hstats


def test_a_missing_header_postpones_that_filing_only_and_it_is_ingested_when_the_header_appears(conn):
    w = world_with(ROWS, {1: HEADERS[1], 2: HEADERS[2]})
    w.errors[hurl(3)] = 404
    rep, res, ready, rejected, hstats, offs = cycle(conn, w, "run-1")
    assert res.events_created == 2 and hstats["header_unavailable_retry_next_poll:SecNotFound"] == 1
    w.errors.clear()
    w.docs[hurl(3)] = header(acc(3), HEADERS[3])
    rep, res, ready, rejected, hstats, offs = cycle(conn, w, "run-2")
    assert res.events_created == 1 and res.unchanged == 2


def test_a_header_that_contradicts_the_feed_refuses_that_filing_and_stores_nothing_for_it(conn):
    w = world_with(ROWS, {1: HEADERS[1], 2: HEADERS[2], 3: "20260917150000"})        # header 15:00 ET = 19:00Z, LATER than the feed's 18:00Z
    rep, res, ready, rejected, hstats, offs = cycle(conn, w, "run-1")
    assert res.events_created == 2 and hstats["refused"] == 1 and any(k.startswith("acceptance:") for k in rejected)
    cur = conn.cursor()
    cur.execute("SELECT count(*) FROM market_event_revision WHERE source_ref = %s", (acc(3),))
    assert cur.fetchone()[0] == 0


def test_a_fresh_filing_whose_feed_value_is_hours_in_the_future_is_stored_at_its_header_time(conn):
    now = C.db_now(conn)
    header_utc = (now - timedelta(minutes=3)).replace(microsecond=0).astimezone(UTC)
    wall = header_utc.astimezone(__import__("zoneinfo").ZoneInfo("America/New_York")).strftime("%Y%m%d%H%M%S")
    from market_intelligence import acceptance_check as A
    feed = (header_utc + A.eastern_offset(header_utc)).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    w = world_with([r(acc(9), filed=header_utc.date().isoformat(), accepted=feed)], {9: wall})
    rep, res, ready, rejected, hstats, offs = cycle(conn, w, "run-1", since=header_utc.date())
    assert res.events_created == 1 and res.errors == [] and not rep.rejected, (rep.counters, rep.rejected, hstats)
    cur = conn.cursor()
    cur.execute("SELECT known_at, ingested_at FROM market_event_revision")
    known, ing = cur.fetchone()
    assert known == header_utc and known < ing
