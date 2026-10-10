"""Disposable worlds for the Slice 6 research-status tests (explicit imports only; never a conftest name).

Every world is a throwaway schema (migrations 1-30 subset the dataset builder uses) seeded with HISTORICAL stamps. The shapes are chosen so a test can
say exactly which sessions are observed, late, reconstructed, missing, partial or back-dated, and then read the status back through the real CLI.

A "healthy" world is contiguous, complete and observed on every due session, with enough final-labelled rows in every split. An "unhealthy" world is
the realistic first weeks after activation: candidates captured, nothing else, no labels.
"""
from datetime import timedelta

from psycopg2 import extensions

import dataset_world as DW
from dataset_world import CAL, MAT, TE, TR, VA, UNIVERSE, World, at, config, sha
from lab_samples import NOW  # noqa: F401  (re-exported)

# the last session whose decision deadline is inside the world's cutoff (MAT 12:00 UTC)
LAST_DUE = max(i for i, d in enumerate(CAL) if DW.C.decision_deadline(d, 1) <= DW.CUTOFF)


def status_config(**over):
    """The Slice 5 test config without the event-driven sources (they have no live collector and are covered separately)."""
    cfg = config(**over)
    for k in ("catalyst", "first_seen"):
        if k not in over:
            cfg.pop(k, None)
    return cfg


def _strategy_id(cur):
    cur.execute("SELECT id FROM strategies LIMIT 1")
    return cur.fetchone()[0]


def activate(cur, idx, *, state="enabled", set_idx=None):
    """An activation row effective from CAL[idx], set (stamped) at 06:00 UTC of CAL[set_idx or idx]."""
    cur.execute("INSERT INTO research_capture_activation (strategy_id, state, effective_from_session, set_at, note) VALUES (%s, %s, %s, %s, 'status test')",
                (_strategy_id(cur), state, CAL[idx], at(set_idx if set_idx is not None else idx, 6)))


def settle_run(cur, idx, status="complete", *, finished=None, n=3, started=None, hash_drift=0, snapshot_drift=0):
    """Give the (single) capture run of session `idx` an honest history: started 20:50 UTC, finished `finished` (default 21:05), with counters that
    satisfy the table's own CHECK constraints for the status."""
    started = started or at(idx, 20, 50)
    if status == "running":
        cur.execute("UPDATE candidate_capture_run SET run_started_at = %s WHERE session_date = %s", (started, CAL[idx]))
        return
    finished = finished or at(idx, 21, 5)
    common = dict(st=started, fin=finished, d=CAL[idx], n=n, hd=hash_drift, sd=snapshot_drift)
    if status == "complete":
        cur.execute("UPDATE candidate_capture_run SET status = 'complete', run_started_at = %(st)s, run_finished_at = %(fin)s, universe_size = 3, "
                    "candidates = %(n)s, captured = %(n)s, already_captured = 0, hash_drift = %(hd)s, guard_rejected = 0, guard_not_evaluated = 0, "
                    "stale_skipped = 0, snapshot_skipped = 0, snapshot_drift = %(sd)s, invalid_skipped = 0 WHERE session_date = %(d)s", common)
    elif status == "partial":
        cur.execute("UPDATE candidate_capture_run SET status = 'partial', run_started_at = %(st)s, run_finished_at = %(fin)s, universe_size = 3, "
                    "candidates = %(n)s + 1, captured = %(n)s, already_captured = 0, hash_drift = %(hd)s, guard_rejected = 0, guard_not_evaluated = 0, "
                    "stale_skipped = 0, snapshot_skipped = 1, snapshot_drift = %(sd)s, invalid_skipped = 0 WHERE session_date = %(d)s", common)
    elif status == "failed":
        cur.execute("UPDATE candidate_capture_run SET status = 'failed', run_started_at = %(st)s, run_finished_at = %(fin)s, error = 'boom' "
                    "WHERE session_date = %(d)s", common)
    else:
        raise ValueError(status)


def seed_status_world(conn, *, cand=(), market=(), rs=(), labels=(), activation=0, run_status=None, late=(), reconstructed=(), recon_only=(),
                      cand_late=(), cand_backdated=(), market_backdated=(), rs_extra_run=(), symbols=UNIVERSE, market_state="RISK_ON",
                      drift=(), post_cutoff=(), no_sector=()):
    """Seed a status world and commit it.

    cand / market / rs / labels: session indexes holding captured candidates / market+sector snapshots / relative strength / primary-horizon labels
    activation: index the capture activation is effective from (None: no activation row at all)
    run_status: {idx: 'partial'|'failed'|'running'} (default 'complete' for every captured session)
    late: market+sector+rs rows stamped after the decision deadline; cand_late: candidate rows stamped after it
    reconstructed: sessions that ALSO get a reconstructed market row; recon_only: sessions whose ONLY market row is reconstructed
    cand_backdated / market_backdated: rows stamped before their own session's UTC day began (impossible availability)
    rs_extra_run: sessions whose relative-strength rows carry two different run content hashes (conflicting observations)
    drift: sessions whose capture run reports hash_drift (a conflicting re-run) | post_cutoff: sessions whose rows arrive AFTER the cutoff
    no_sector: symbols with no sector (candidate snapshots and relative strength)
    """
    cur = conn.cursor()
    run_status = dict(run_status or {})
    cand, market, rs, labels = list(cand), list(market), list(rs), set(labels)
    saved = dict(DW.SECTOR_OF)
    for s in no_sector:
        DW.SECTOR_OF.pop(s, None)
    try:
        w = _seed(conn, cur, locals())
    finally:
        DW.SECTOR_OF.clear()
        DW.SECTOR_OF.update(saved)
    conn.commit()
    return w


def _seed(conn, cur, a):
    cand, market, rs, labels, activation, run_status, late = a["cand"], a["market"], a["rs"], a["labels"], a["activation"], a["run_status"], a["late"]
    cand_late, cand_backdated, market_backdated, post_cutoff = a["cand_late"], a["cand_backdated"], a["market_backdated"], a["post_cutoff"]
    recon_only, reconstructed, symbols, market_state, drift, rs_extra_run = (a["recon_only"], a["reconstructed"], a["symbols"], a["market_state"],
                                                                              a["drift"], a["rs_extra_run"])
    triggers = DW._stamp_triggers(cur)
    for trg, rel in triggers:
        cur.execute(f'ALTER TABLE "{rel}" DISABLE TRIGGER "{trg}"')
    w = World(conn)
    try:
        if activation is not None:
            activate(cur, activation)
        for idx in cand:
            stamp = at(idx)
            if idx in cand_late:
                stamp = at(idx) + timedelta(days=4)
            elif idx in cand_backdated:
                stamp = at(idx) - timedelta(days=3)
            elif idx in post_cutoff:
                stamp = DW.CUTOFF + timedelta(days=2)
            for s in symbols:
                w.candidate(s, idx, captured_at=stamp, with_labels=False)
                if idx in labels:
                    w.label(s, idx, DW.PRIMARY, direction=-1 if s == "C" else 1)
            run_at = dict(started=DW.CUTOFF + timedelta(days=2), finished=DW.CUTOFF + timedelta(days=2, minutes=15)) if idx in post_cutoff else {}
            settle_run(cur, idx, run_status.get(idx, "complete"), n=len(symbols), hash_drift=1 if idx in drift else 0, **run_at)
        for idx in market:
            stamp = at(idx) + (timedelta(days=5) if idx in late else timedelta(0))
            if idx in market_backdated:
                stamp = at(idx) - timedelta(days=3)
            if idx in post_cutoff:
                stamp = DW.CUTOFF + timedelta(days=2)
            if idx in recon_only:
                w.market(idx, provenance="reconstructed", captured_at=stamp, state=market_state)
                continue
            w.market(idx, captured_at=stamp, state=market_state)
            if idx in reconstructed:
                w.market(idx, provenance="reconstructed", captured_at=stamp, state=market_state)
        for idx in rs:
            stamp = at(idx, 21, 30) + (timedelta(days=4) if idx in late else timedelta(0))
            if idx in post_cutoff:
                stamp = DW.CUTOFF + timedelta(days=2)
            for s in symbols:
                w.stock_rs(s, idx, created_at=stamp, run_hash=sha("other-run", idx) if idx in rs_extra_run and s == symbols[-1] else None)
    finally:
        if conn.get_transaction_status() == extensions.TRANSACTION_STATUS_INERROR:
            conn.rollback()                               # the DISABLE was part of the aborted transaction: nothing to re-enable
        else:
            for trg, rel in triggers:
                cur.execute(f'ALTER TABLE "{rel}" ENABLE ALWAYS TRIGGER "{trg}"')
    return w


def healthy(conn, **over):
    """Contiguous, complete, observed history on every due session: candidates + market + sector + relative strength, every mature primary-horizon
    candidate labelled, activation effective from the first session."""
    rng = list(range(0, LAST_DUE + 1))
    mature = [i for i in rng if i + DW.PRIMARY < CAL.index(MAT)]
    base = dict(cand=rng, market=rng, rs=rng, labels=mature, activation=0)
    base.update(over)
    return seed_status_world(conn, **base)


def unhealthy(conn, **over):
    """The realistic first weeks after activation: candidates captured on the last 8 due sessions, nothing else written, no labels."""
    first = LAST_DUE - 7
    base = dict(cand=list(range(first, LAST_DUE + 1)), activation=first)
    base.update(over)
    return seed_status_world(conn, **base)
