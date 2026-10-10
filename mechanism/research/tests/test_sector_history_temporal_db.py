"""Slice 10: the append-only sector history, driven through REAL dataset builds against throwaway Postgres.

Every scenario seeds one (symbol, source) chain with HISTORICAL capture times (the stamp triggers are switched off inside the disposable schema for
the seeding only and re-enabled ENABLE ALWAYS afterwards, exactly like `dataset_world.seed_world`), authors a manifest that opts into the history for
that scenario's own `source`, builds the dataset (assembler -> independent audit -> input verification) and compares it with a control build. The
assertions are on dataset VALUES (byte-identity of whole rows), not on state labels alone.

What is proven here, from running code:
  learn-later            an observation that arrives later changes NO row decided before it (also when it arrives the next calendar day, inside the
                         grace window);  reclassification  Tech then Health Care: a decision sees what was knowable then, nothing is rewritten;
  same-value refresh     freshness is extended by a LATER same-value poll and by nothing else;  vendor failure  a failed poll never erases or refreshes
                         anything: the last known sector keeps aging and goes stale after 30 days;  an inferred no-sector is not a vendor failure;
  conflict / broken chain  fail closed, per cell, and never repaired by a later observation;
  reconstruction         sector_reconstruction rows are never read: they cannot make a cell observed, fresh or PIT-safe.
"""
import json
from datetime import date, datetime, timedelta, timezone

import pytest

import dataset_world as DW
from dataset_world import CAL, CUTOFF, denv  # noqa: F401  (explicit fixture import: no top-level `conftest` name)
from data_updaters import sector_history_recorder as REC
from research.lab import dataset_assemble as A
from research.lab import dataset_contract as C
from research.lab import dataset_readiness as READY
from research.lab import dataset_reader as RD
from research.lab import dataset_runner as R
from research.lab import sector_history as SH

PRIMARY = DW.PRIMARY
T_MID = CAL[300]                 # a validation-window decision session with a candidate for every symbol
T_PREV = CAL[297]                # the previous candidate session
T_NEXT = CAL[303]

SRC_CTR = {"n": 0}


class _AnySource(str):
    """Each scenario owns a private, uniquely named chain so scenarios cannot see one another; the spec validator pins the real source to
    `yfinance_info` (Slice 11), which would make every scenario share one chain. Only this module widens that pin, and only while it runs."""

    def __ne__(self, other):
        return False


@pytest.fixture(scope="module", autouse=True)
def _scenario_sources_are_private_chains():
    real = C.AUTHORITATIVE_HISTORY_SOURCE
    C.AUTHORITATIVE_HISTORY_SOURCE = _AnySource(real)
    try:
        yield
    finally:
        C.AUTHORITATIVE_HISTORY_SOURCE = real


def dt(d, hour=6):
    return datetime(d.year, d.month, d.day, hour, tzinfo=timezone.utc)


def daily(i0, i1, kind="conf", **kw):
    """One poll event per session CAL[i0]..CAL[i1] (07:00 UTC)."""
    return [(kind, dt(CAL[i], 7), *kw.get("rest", ())) for i in range(i0, i1 + 1)]


# ------------------------------------------------------------------ seeding (historical stamps; disposable schema only)
OBS_SQL = ("INSERT INTO sector_observation (symbol, source, seq, sector, sector_raw, no_sector_reason, change_kind, captured_at, effective_session, "
           "source_asof, provenance, raw_payload_hash, run_id, writer, code_ref, prev_value_hash, value_hash) "
           "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,NULL,'observed_forward',%s,%s,'test_seed','test',%s,%s) RETURNING id")
POLL_SQL = ("INSERT INTO sector_poll (run_id, symbol, source, response_state, chain_effect, response_sector, no_sector_reason, failure_reason, "
            "raw_payload_hash, observation_id, attempted_at, writer, code_ref) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'test_seed','test')")


def seed_chain(cur, symbol, source, events):
    """events: ('obs', when, sector|None) | ('badobs', when, sector) (a wrong value_hash) | ('conf', when) | ('fail', when, state, reason).
    Observations are chained exactly as the database trigger would (seq, prev link, hash)."""
    head = None
    seq = 0
    for n, ev in enumerate(sorted(events, key=lambda e: e[1]), start=1):
        kind, when = ev[0], ev[1]
        run = f"seed-{symbol}-{source}-{n}"
        if kind in ("obs", "badobs"):
            sector = ev[2]
            seq += 1
            prev = head["value_hash"] if head else None
            reason = None if sector is not None else "vendor_null"
            ck = "first" if head is None else ("became_none" if sector is None else ("became_set" if head["sector"] is None else "changed"))
            payload = REC.payload_hash(sector)
            vh = SH.chain_hash(symbol=symbol, source=source, seq=seq, sector=sector, no_sector_reason=reason, sector_raw=sector, captured_at=when,
                               provenance="observed_forward", raw_payload_hash=payload, prev_value_hash=prev)
            if kind == "badobs":
                vh = "f" * 64
            cur.execute(OBS_SQL, (symbol, source, seq, sector, sector, reason, ck, when, when.date(), payload, run, prev, vh))
            oid = cur.fetchone()[0]
            head = {"id": oid, "value_hash": vh, "sector": sector}
            cur.execute(POLL_SQL, (run, symbol, source, "sector" if sector is not None else "no_sector", "created_observation", sector, reason, None,
                                   payload, oid, when, ))
        elif kind == "conf":
            sector = head["sector"]
            reason = None if sector is not None else "vendor_null"
            cur.execute(POLL_SQL, (run, symbol, source, "sector" if sector is not None else "no_sector", "confirmed_head", sector, reason, None,
                                   REC.payload_hash(sector), head["id"], when))
        elif kind == "fail":
            cur.execute(POLL_SQL, (run, symbol, source, ev[2], "none", None, None, ev[3], None, None, when))
        else:
            raise AssertionError(kind)


def seed(conn, source, events_by_symbol):
    cur = conn.cursor()
    pairs = (("sector_observation", "sector_observation_stamp"), ("sector_poll", "sector_poll_stamp"))
    for t, trg in pairs:
        cur.execute(f'ALTER TABLE {t} DISABLE TRIGGER {trg}')
    try:
        for symbol, events in events_by_symbol.items():
            seed_chain(cur, symbol, source, events)
    finally:
        for t, trg in pairs:
            cur.execute(f'ALTER TABLE {t} ENABLE ALWAYS TRIGGER {trg}')
    conn.commit()


# ------------------------------------------------------------------ scenarios
class Scn:
    """One history source's world: the raw rows the reader returns, and (lazily) the real build."""

    def __init__(self, env, source):
        self.env, self.source = env, source
        self.manifest = DW.author_manifest(env.conn, DW.config(sector_history={"source": source}))
        self.cfg = C.config_for(self.manifest.document)
        with RD.read_only_session(env.conn):
            self.raw = RD.read_raw(env.conn.cursor(), self.manifest, self.cfg)
        self._build = None

    @property
    def build(self):
        if self._build is None:
            self._build = R.build_from_manifest(self.env.conn, self.manifest, code=self.env.code)
        return self._build

    @property
    def rows(self):
        return {(r["symbol"], r["t0_session"], r["horizon_sessions"]): r for r in self.build.rows}

    def assemble(self, raw):
        return A.assemble(self.manifest, self.cfg, raw)

    def select(self, symbol, t0, source_rows=None):
        obs, conf = SH.group_history(source_rows if source_rows is not None else self.raw[C.HISTORY_SOURCE], self.source)
        return SH.select(obs.get(symbol, ()), conf.get(symbol, ()), t0=t0, grace_days=1, cutoff=CUTOFF)

    def readiness(self):
        b = self.build
        return READY.assess(manifest=self.manifest, cfg=b.config, rows=b.rows, audit_document=b.audit.document, audit_hash=b.audit.audit_hash,
                            verification=b.verification, code_check=b.code_check, dataset_hash=b.dataset_hash, report_hash=b.report["report_hash"],
                            inputs_hash=b.inputs_hash, include_test=False)


def make(env, events_by_symbol, name=None):
    SRC_CTR["n"] += 1
    source = f"scn_{SRC_CTR['n']}_{name or 'x'}"[:40]
    seed(env.conn, source, events_by_symbol)
    return Scn(env, source)


@pytest.fixture(scope="module")
def baseline(denv):
    """The dataset WITHOUT the history opt-in (Slice 8 candidate-bounded evidence only): the control for 'history that agrees changes nothing'."""
    manifest, cfg, raw = denv.parts()
    return {(r["symbol"], r["t0_session"], r["horizon_sessions"]): r for r in A.assemble(manifest, cfg, raw).rows}


def ser(row):
    return json.dumps(row, sort_keys=True, default=str)


def a_rows(rows, symbol="A", before=None, from_=None):
    return {k: v for k, v in rows.items() if k[0] == symbol and (before is None or k[1] < before) and (from_ is None or k[1] >= from_)}


def only_symbol_other_than(rows, symbols):
    return {k: v for k, v in rows.items() if k[0] not in symbols}


def same(r1, r2):
    assert r1.keys() == r2.keys()
    bad = [k for k in r1 if ser(r1[k]) != ser(r2[k])]
    assert not bad, bad[:3]


def prim(rows, symbol="A"):
    return {k: v for k, v in rows.items() if k[0] == symbol and k[2] == PRIMARY}


def states(rows, symbol="A", **rng):
    return {k[1]: v["sector__state"] for k, v in prim(a_rows(rows, symbol, **rng)).items()}


# ================================================================== LEARN LATER
def test_learn_later_the_row_decided_before_an_observation_is_byte_identical_after_it_exists(denv, baseline):
    """Unknown at T-1, Tech at T: every dataset row decided before T is identical with and without the observation; only rows at/after T gain a sector."""
    empty = make(denv, {}, "empty")
    learned = make(denv, {"A": [("obs", dt(T_MID, 6), "Tech")]}, "learn")
    r0, r1 = empty.rows, learned.rows
    before0, before1 = a_rows(r0, before=T_MID), a_rows(r1, before=T_MID)
    assert before0 and any(k[1] == T_PREV for k in before0)
    same(before0, before1)                                                                          # byte-identical, every column, every horizon
    assert set(states(r1, before=T_MID).values()) == {C.SECTOR_UNCONFIRMED}                          # and what they say is "not confirmed"
    same(only_symbol_other_than(r0, {"A"}), only_symbol_other_than(r1, {"A"}))                      # no other symbol moved either
    after = prim(a_rows(r1, from_=T_MID))
    live = {k: v for k, v in after.items() if (k[1] - T_MID).days <= 30}
    assert live and {v["sector__state"] for v in live.values()} <= {C.OK, C.LATE, C.ABSENT} and C.OK in {v["sector__state"] for v in live.values()}
    for k, v in live.items():                                                                        # history that agrees changes nothing at all
        assert ser(v) == ser(baseline[k])
    assert any(v["sector__state"] == C.SECTOR_UNCONFIRMED for v in prim(a_rows(r0, from_=T_MID)).values())   # without it: still unconfirmed
    assert learned.select("A", T_PREV).kind == SH.K_NO_HISTORY and learned.select("A", T_MID).kind == SH.K_OBSERVED


def test_an_observation_captured_the_next_calendar_day_never_reaches_the_previous_decision(denv):
    """Inside the one-day grace window `is_known` alone would admit it: the effective-session rule is what keeps it out of the T-1 decision."""
    stamp = dt(T_PREV, 1) + timedelta(days=1)
    assert C.is_known(stamp, T_PREV, 1, CUTOFF) is True                                              # the grace rule alone says "knowable"
    leak = make(denv, {"A": [("obs", stamp, "Tech")]}, "leak")
    empty = make(denv, {}, "empty2")
    assert leak.select("A", T_PREV).kind == SH.K_NO_HISTORY                                          # ... the history refuses it
    same(a_rows(empty.rows, before=T_MID), a_rows(leak.rows, before=T_MID))                          # and no earlier row changed
    assert leak.select("A", T_PREV + timedelta(days=1)).kind == SH.K_OBSERVED                        # it is usable from its own session on


def test_an_observation_after_the_knowledge_cutoff_is_never_read(denv):
    late = make(denv, {"A": [("obs", dt(T_MID, 6), "Tech"), ("obs", CUTOFF + timedelta(days=3), "Energy")]}, "postcut")
    kinds = {(r["row_kind"], r["seq"]) for r in late.raw[C.HISTORY_SOURCE]}
    assert kinds == {("observation", 1), ("confirmation", 1)} or kinds == {("observation", 1)}
    assert late.select("A", T_MID + timedelta(days=5)).sector == "Tech"


# ================================================================== RECLASSIFICATION
def test_reclassification_decisions_between_and_after_see_what_was_knowable_then(denv, baseline):
    reclass = make(denv, {"A": [("obs", dt(CAL[250], 6), "Tech"), *daily(251, 299), ("obs", dt(T_MID, 6), "Health Care"), *daily(301, 345)]}, "reclass")
    control = make(denv, {"A": [("obs", dt(CAL[250], 6), "Tech"), *daily(251, 345)]}, "techonly")
    t_between, t_after = CAL[290], CAL[310]
    s1, s2 = reclass.select("A", t_between), reclass.select("A", t_after)
    assert (s1.sector, s1.head_seq, s1.kind) == ("Tech", 1, SH.K_OBSERVED)                          # between: Tech
    assert (s2.sector, s2.head_seq, s2.kind) == ("Health Care", 2, SH.K_OBSERVED)                   # after: Health Care
    # nothing decided before the reclassification is rewritten by it: whole rows, every column
    same(a_rows(reclass.rows, before=T_MID), a_rows(control.rows, before=T_MID))
    between = prim(a_rows(reclass.rows, from_=CAL[270], before=T_MID))
    assert between and C.OK in {v["sector__state"] for v in between.values()}
    assert all(v["sector"] in ("Tech", None) and v["sector__state"] in (C.OK, C.LATE, C.ABSENT, C.RECONSTRUCTED_EXCLUDED)
               for v in between.values())
    for k, v in between.items():                                                                     # == the Slice 8 baseline: history that agrees adds nothing
        assert ser(v) == ser(baseline[k])
    # the chain itself: seq 1 is the same row with and without the later change, and seq 2 links to it
    h1 = {r["seq"]: r for r in reclass.raw[C.HISTORY_SOURCE] if r["row_kind"] == "observation"}
    c1 = {r["seq"]: r for r in control.raw[C.HISTORY_SOURCE] if r["row_kind"] == "observation"}
    assert (h1[1]["sector"], h1[1]["stamp"], h1[1]["change_kind"]) == (c1[1]["sector"], c1[1]["stamp"], c1[1]["change_kind"]) == ("Tech", dt(CAL[250], 6), "first")
    assert h1[2]["prev_value_hash"] == h1[1]["value_hash"] and h1[2]["change_kind"] == "changed" and h1[2]["sector"] == "Health Care"
    assert SH.verify_chain([h1[1], h1[2]]) == []
    # once BOTH sources name Health Care (the candidate evidence is edited the way a re-snapshotted candidate would read), the row says Health Care
    raw = {k: list(v) for k, v in reclass.raw.items()}
    raw["candidates"] = [({**r, "fs_sector": "Health Care"} if r["symbol"] == "A" and r["session_date"] >= T_MID else r) for r in raw["candidates"]]
    asm = reclass.assemble(raw)
    after = [r for r in asm.rows if r["symbol"] == "A" and r["t0_session"] >= T_MID and r["horizon_sessions"] == PRIMARY and r["t0_session"] <= CAL[330]]
    # the sector NAME is Health Care from T2 on (this world has no Health Care sector snapshot, hence `absent`; the stored RS row still names Tech, so
    # the sector-relative cell is an identity conflict or absent -- never a Tech value presented under a Health Care row)
    assert after and {r["sector"] for r in after} == {"Health Care"} and {r["sector__state"] for r in after} == {C.ABSENT}
    assert {r["rs_vs_sector__state"] for r in after} <= {C.ABSENT, C.SECTOR_IDENTITY_CONFLICT} and all(r["rs_vs_sector_pp"] is None for r in after)
    # and the previous-sector evidence did not bleed into rows decided before the change
    before = [r for r in asm.rows if r["symbol"] == "A" and CAL[270] <= r["t0_session"] < T_MID and r["horizon_sessions"] == PRIMARY
              and r["sector__state"] == C.OK]
    assert before and {r["sector"] for r in before} == {"Tech"}


def test_a_candidate_that_still_says_the_old_sector_after_a_reclassification_is_an_identity_conflict(denv):
    """Candidate evidence says Tech, the history says Health Care at the same decision point: fail closed (cell level), the dataset still builds."""
    scn = make(denv, {"A": [("obs", dt(CAL[250], 6), "Health Care"), *daily(251, 345)]}, "conflict")
    live = {k: v for k, v in prim(a_rows(scn.rows, from_=CAL[250])).items() if k[1] <= CAL[345]}
    assert live
    for k, v in live.items():
        assert v["sector__state"] in (C.SECTOR_IDENTITY_CONFLICT, C.LATE, C.ABSENT) and v["sector"] is None, (k, v["sector__state"])
        assert v["rs_vs_sector_pp"] is None and v["rs_sector"] is None
    assert C.SECTOR_IDENTITY_CONFLICT in {v["sector__state"] for v in live.values()}
    assert scn.build.audit.ok and scn.build.audit.document["counts"]["sector_history"]["by_relation"].get(SH.R_IDENTITY_CONFLICT, 0) > 0
    chk = {c["id"]: c for c in scn.readiness()["eligibility"]["checks"]}
    assert chk["relative_strength_sector_pit_safe"]["passed"] is True and chk["sector_history_chain_intact"]["passed"] is True   # masked, not fatal
    # the market-relative RS is untouched by the conflict
    assert any(v["rs_ret_pct"] is not None for v in live.values() if v["rs__state"] == C.OK)


def test_a_later_observation_never_repairs_an_earlier_conflict(denv):
    conflicted = make(denv, {"A": [("obs", dt(CAL[250], 6), "Health Care"), *daily(251, 345)]}, "conf_a")
    repaired = make(denv, {"A": [("obs", dt(CAL[250], 6), "Health Care"), *daily(251, 299), ("obs", dt(T_MID, 6), "Tech"), *daily(301, 345)]}, "conf_b")
    same(a_rows(conflicted.rows, before=T_MID), a_rows(repaired.rows, before=T_MID))
    early = prim(a_rows(repaired.rows, from_=CAL[270], before=T_MID))
    assert early and {v["sector__state"] for v in early.values()} <= {C.SECTOR_IDENTITY_CONFLICT, C.LATE, C.ABSENT}
    assert C.SECTOR_IDENTITY_CONFLICT in {v["sector__state"] for v in early.values()}                 # still a conflict: it is not retro-repaired
    late = {k: v for k, v in prim(a_rows(repaired.rows, from_=T_MID)).items() if k[1] <= CAL[345]}
    assert {v["sector__state"] for v in late.values()} <= {C.OK, C.LATE, C.ABSENT} and C.OK in {v["sector__state"] for v in late.values()}   # agrees again


# ================================================================== SAME-VALUE REFRESH
def test_a_same_value_refresh_extends_freshness_by_exactly_the_refresh_and_keeps_the_identity(denv, baseline):
    """Tech observed at T1 (CAL[250]) and re-confirmed at T2 (CAL[270]): identity stays Tech (ONE observation, no duplicate), the polls prove the
    refresh, and freshness is measured from T2 -- the head's own capture time stays T1."""
    t1, t2 = dt(CAL[250], 6), dt(CAL[270], 6)
    refreshed = make(denv, {"A": [("obs", t1, "Tech"), ("conf", t2)]}, "refresh")
    plain = make(denv, {"A": [("obs", t1, "Tech")]}, "norefresh")
    obs = [r for r in refreshed.raw[C.HISTORY_SOURCE] if r["row_kind"] == "observation"]
    conf = [r for r in refreshed.raw[C.HISTORY_SOURCE] if r["row_kind"] == "confirmation"]
    assert len(obs) == 1 and obs[0]["sector"] == "Tech" and len(conf) == 1 and conf[0]["value_hash"] == obs[0]["value_hash"]
    t_probe = CAL[270] + timedelta(days=25)                                                          # 45 days after T1, 25 after T2
    s_ref, s_plain = refreshed.select("A", t_probe), plain.select("A", t_probe)
    assert s_ref.sector == s_plain.sector == "Tech" and s_ref.head_seq == s_plain.head_seq == 1
    assert s_ref.head_captured_at == t1 and s_ref.currency_at == t2 and s_ref.confirmations_used == 1
    assert s_ref.evidence.state == "observed_fresh" and s_plain.evidence.state == "observed_stale"   # 25 <= 30 < 45
    assert s_plain.currency_at == t1 and s_plain.confirmations_used == 0
    # the refresh does not rewrite the past: before T2 the two datasets agree on every row decided before the refresh
    same(a_rows(refreshed.rows, before=CAL[270]), a_rows(plain.rows, before=CAL[270]))
    # and it does not extend beyond itself: 31 days after T2 the refreshed chain is stale again
    assert refreshed.select("A", CAL[270] + timedelta(days=30)).evidence.state == "observed_fresh"
    assert refreshed.select("A", CAL[270] + timedelta(days=31)).evidence.state == "observed_stale"
    # dataset level: a candidate session ~35 days after T1 is stale WITHOUT the refresh and fresh WITH it
    mid = [k for k in prim(a_rows(plain.rows)) if 31 <= (k[1] - CAL[250]).days and (k[1] - CAL[270]).days <= 30 and baseline[k]["sector__state"] == C.OK]
    assert mid
    for k in mid:
        assert plain.rows[k]["sector__state"] == C.SECTOR_STALE and refreshed.rows[k]["sector__state"] == C.OK
        assert ser(refreshed.rows[k]) == ser(baseline[k])


# ================================================================== VENDOR FAILURE
def test_vendor_failures_neither_erase_nor_refresh_the_last_known_sector_and_it_keeps_aging(denv):
    d = CAL[250]
    failing = make(denv, {"A": [("obs", dt(d, 6), "Tech"),
                                *[("fail", dt(CAL[i], 7), "request_failed", "timeout") for i in range(251, 341)],
                                *[("fail", dt(CAL[i], 8), "invalid_response", "ambiguous_source_none") for i in range(251, 341)]]}, "failing")
    quiet = make(denv, {"A": [("obs", dt(d, 6), "Tech")]}, "quiet")
    # a failed poll is not even read: the dataset is byte-identical to the one that never failed
    assert {r["row_kind"] for r in failing.raw[C.HISTORY_SOURCE]} == {"observation"}
    same(failing.rows, quiet.rows)
    # the last known classification is untouched in the store
    cur = denv.conn.cursor()
    cur.execute("SELECT count(*), min(sector), max(sector) FROM sector_observation WHERE source = %s", (failing.source,))
    assert cur.fetchone() == (1, "Tech", "Tech")
    cur.execute("SELECT count(*) FROM sector_poll WHERE source = %s AND chain_effect = 'none'", (failing.source,))
    assert cur.fetchone()[0] == 180
    denv.conn.rollback()
    # what the research system believes on each day after the last success: Tech, aging by calendar day; stale (masked) from day 31
    for k in range(0, 46):
        s = failing.select("A", d + timedelta(days=k))
        assert (s.kind, s.sector, s.currency_at) == (SH.K_OBSERVED, "Tech", dt(d, 6)), k
        assert s.evidence.state == ("observed_fresh" if k <= 30 else "observed_stale"), k
    # at dataset level the same rule holds row by row
    for (sym, t0, h), row in prim(a_rows(failing.rows, from_=d)).items():
        assert row["sector__state"] == (C.OK if (t0 - d).days <= 30 else C.SECTOR_STALE), (t0, row["sector__state"])
    ages = {(t0 - d).days <= 30 for (_s, t0, _h) in prim(a_rows(failing.rows, from_=d))}
    assert ages == {True, False}                                                                    # both sides of the boundary are exercised


# ================================================================== INFERRED NO-SECTOR vs FAILURE
def test_an_inferred_no_sector_answer_is_not_a_vendor_failure(denv):
    d = CAL[250]
    nosec = make(denv, {"A": [("obs", dt(d, 6), "Tech"), *daily(251, 289), ("obs", dt(CAL[290], 6), None), *daily(291, 345)]}, "nosector")
    failed = make(denv, {"A": [("obs", dt(d, 6), "Tech"), *daily(251, 289),
                               *[("fail", dt(CAL[i], 7), "request_failed", "timeout") for i in range(290, 346)]]}, "failure")
    s_none, s_fail = nosec.select("A", T_MID), failed.select("A", T_MID)
    assert (s_none.kind, s_none.sector, s_none.evidence.reason) == (SH.K_INFERRED_NO_SECTOR, None, "no_sector")
    assert (s_fail.kind, s_fail.sector, s_fail.evidence.state) == (SH.K_OBSERVED, "Tech", "observed_fresh")      # a failure keeps believing Tech
    row_none, row_fail = nosec.rows[("A", T_MID, PRIMARY)], failed.rows[("A", T_MID, PRIMARY)]
    # owner rule (Slice 11): the history ASSERTS no sector while the candidate evidence still says Tech -> neither silently wins, both cells conflict
    assert row_none["sector"] is None and row_none["sector__state"] == C.SECTOR_IDENTITY_CONFLICT and row_none["rs_vs_sector_pp"] is None
    win = [k for k in prim(a_rows(nosec.rows, from_=CAL[290])) if k[1] <= CAL[345] and nosec.rows[k]["rs__state"] == C.OK]
    assert win                                                                                       # the symbol stays; market-relative RS stays
    for k in win:
        v = nosec.rows[k]
        # the history says "no sector"; the candidate and the stored RS row still name Tech: a CONFLICT (not a quiet no_sector, not a quiet Tech)
        assert v["sector"] is None and v["sector__state"] == C.SECTOR_IDENTITY_CONFLICT and v["rs_vs_sector__state"] == C.SECTOR_IDENTITY_CONFLICT
        assert v["rs_vs_sector_pp"] is None and v["rs_ret_pct"] is not None
    assert row_fail["sector"] == "Tech" and row_fail["sector__state"] == C.OK
    # the decision before the explicit "no sector" is untouched by it
    same(a_rows(nosec.rows, before=CAL[290]), a_rows(failed.rows, before=CAL[290]))
    # an inferred no-sector does not age into "stale": it stays an inferred no-sector (not a sector, not a failure) however long it is polled
    assert nosec.select("A", CAL[290] + timedelta(days=80)).kind == SH.K_INFERRED_NO_SECTOR
    assert nosec.build.audit.ok


# ================================================================== STALENESS (29 / 30 / 31 days)
def test_the_thirty_day_boundary_at_dataset_level(denv, baseline):
    """The same decision session, three symbols whose sector was captured 29 / 30 / 31 days earlier (no refresh): fresh, fresh, stale."""
    scn = make(denv, {"C": [("obs", dt(T_MID, 6) - timedelta(days=29), "Energy")],
                      "A": [("obs", dt(T_MID, 6) - timedelta(days=30), "Tech")],
                      "B": [("obs", dt(T_MID, 6) - timedelta(days=31), "Tech")]}, "boundary")
    rows = scn.rows
    c29, a30, b31 = rows[("C", T_MID, PRIMARY)], rows[("A", T_MID, PRIMARY)], rows[("B", T_MID, PRIMARY)]
    assert (c29["sector"], c29["sector__state"]) == ("Energy", C.OK)
    assert (a30["sector"], a30["sector__state"]) == ("Tech", C.OK)                                   # age 30 <= 30: fresh
    assert (b31["sector"], b31["sector__state"]) == (None, C.SECTOR_STALE)                           # age 31 > 30: stale, masked
    assert ser(a30) == ser(baseline[("A", T_MID, PRIMARY)]) and ser(c29) == ser(baseline[("C", T_MID, PRIMARY)])
    # the next session: day 30 -> day 33 (A) and day 29 -> day 32 (C): both stale now
    assert rows[("A", T_NEXT, PRIMARY)]["sector__state"] == C.SECTOR_STALE and rows[("C", T_NEXT, PRIMARY)]["sector__state"] == C.SECTOR_STALE
    for k, want in ((29, "observed_fresh"), (30, "observed_fresh"), (31, "observed_stale")):
        assert scn.select("A", dt(T_MID, 6).date() - timedelta(days=30) + timedelta(days=k)).evidence.state == want, k


# ================================================================== BROKEN CHAIN
def test_a_broken_link_fails_closed_for_later_decisions_only_and_blocks_readiness(denv):
    bad = make(denv, {"A": [("obs", dt(CAL[250], 6), "Tech"), *daily(251, 299), ("badobs", dt(T_MID, 6), "Energy"), *daily(301, 345)]}, "broken")
    good = make(denv, {"A": [("obs", dt(CAL[250], 6), "Tech"), *daily(251, 299), ("obs", dt(T_MID, 6), "Energy"), *daily(301, 345)]}, "intact")
    # rows decided before the tampered link was written are not affected by it (the later row is not consulted)
    same(a_rows(bad.rows, before=T_MID), a_rows(good.rows, before=T_MID))
    after = prim(a_rows(bad.rows, from_=T_MID))
    assert after and {v["sector__state"] for v in after.values()} == {C.SECTOR_IDENTITY_CONFLICT}
    assert all(v["rs_vs_sector_pp"] is None for v in after.values())
    sel = bad.select("A", CAL[310])
    assert sel.kind == SH.K_BROKEN and "hash_mismatch" in sel.problems
    doc = bad.build.audit.document
    assert doc["counts"]["sector_history"]["chain_problems"] and "sector_history_chain_broken" in {x["code"] for x in doc["limitations"]}
    chk = {c["id"]: c for c in bad.readiness()["eligibility"]["checks"]}
    assert chk["sector_history_chain_intact"]["passed"] is False and bad.readiness()["eligibility"]["model_research_eligible"] is False
    assert {c["id"]: c for c in good.readiness()["eligibility"]["checks"]}["sector_history_chain_intact"]["passed"] is True


# ================================================================== RECONSTRUCTION NEVER BECOMES OBSERVED
def test_reconstructed_sector_history_can_never_satisfy_observed_coverage(denv):
    """`sector_reconstruction` filled for every symbol and every session with the very sectors the candidates carry: the history reader never reads it,
    so no cell is confirmed, nothing is fresh, no sector-relative value is carried, and the observation table stays empty for that source."""
    cur = denv.conn.cursor()
    cur.execute("INSERT INTO sector_reconstruction (symbol, sector, reconstructed_from, row_date, method, import_batch, provenance, code_ref) "
                "SELECT s.symbol, s.sector, 'daily_fundamentals.sector', d, 'project_current_map_backwards', 'batch-1', 'reconstructed', 'test' "
                "FROM (VALUES ('A','Tech'),('B','Tech'),('C','Energy')) AS s(symbol, sector) CROSS JOIN unnest(%s::date[]) AS d",
                (list(CAL[:600]),))
    denv.conn.commit()
    cur.execute("SELECT count(*) FROM sector_reconstruction")
    assert cur.fetchone()[0] >= 3 * 600
    scn = make(denv, {}, "recon")
    empty = make(denv, {}, "recon_ctl")
    assert scn.raw[C.HISTORY_SOURCE] == []                                                           # the reader returned nothing from it
    cur.execute("SELECT count(*) FROM sector_observation WHERE source = %s", (scn.source,))
    assert cur.fetchone()[0] == 0
    denv.conn.rollback()
    same(scn.rows, empty.rows)
    prims = [r for r in scn.build.rows if r["horizon_sessions"] == PRIMARY]
    assert prims and {r["sector__state"] for r in prims} == {C.SECTOR_UNCONFIRMED}
    assert all(r["sector"] is None and r["rs_vs_sector_pp"] is None for r in prims)                   # not a single sector-relative value is carried
    summary = scn.build.audit.document["counts"]["sector_history"]
    assert set(summary["by_history_kind"]) == {SH.K_NO_HISTORY}
    chk = {c["id"]: c for c in scn.readiness()["eligibility"]["checks"]}
    assert chk["relative_strength_sector_pit_safe"]["passed"] is True                                # masked, not "safe by reconstruction"
    for sym in "ABC":
        for day in (T_PREV, T_MID, CAL[400]):
            assert scn.select(sym, day).kind == SH.K_NO_HISTORY


def test_the_history_reader_cannot_touch_the_reconstruction_table():
    import inspect
    src = "".join(inspect.getsource(f) for f in (RD._read_sector_history, RD.history_rows, RD.poll_activity))
    assert "sector_reconstruction" not in src and "daily_fundamentals" not in src
    assert "sector_observation" in src and "sector_poll" in src


def test_a_reconstructed_provenance_row_injected_into_the_history_is_never_fresh(denv):
    scn = make(denv, {"A": [("obs", dt(CAL[250], 6), "Tech"), *daily(251, 345)]}, "inject")
    rows = [dict(r) for r in scn.raw[C.HISTORY_SOURCE]]
    first = next(r for r in rows if r["row_kind"] == "observation")
    first["provenance"] = "reconstructed"
    sel = scn.select("A", CAL[300], rows)
    assert sel.kind == SH.K_BROKEN and sel.evidence.state == "unavailable" and "provenance_not_observed_forward" in sel.problems
    raw = {k: list(v) for k, v in scn.raw.items()}
    raw[C.HISTORY_SOURCE] = rows
    asm = scn.assemble(raw)
    cells = [r for r in asm.rows if r["symbol"] == "A" and r["horizon_sessions"] == PRIMARY and r["t0_session"] >= CAL[250]]
    assert cells and {r["sector__state"] for r in cells} == {C.SECTOR_IDENTITY_CONFLICT}
    assert all(r["rs_vs_sector_pp"] is None for r in cells)


def test_nothing_in_a_scenario_world_wrote_the_observation_table_from_daily_fundamentals(denv):
    """There is no migration, trigger or view that copies fundamentals into the forward history: every observation row in this database is one the
    scenarios seeded explicitly and carries the forward provenance and a writer name."""
    cur = denv.conn.cursor()
    cur.execute("SELECT DISTINCT provenance, writer FROM sector_observation")
    assert cur.fetchall() == [("observed_forward", "test_seed")]
    cur.execute("SELECT count(*) FROM sector_observation WHERE source LIKE %s", ("%daily_fundamentals%",))
    assert cur.fetchone()[0] == 0
    denv.conn.rollback()
