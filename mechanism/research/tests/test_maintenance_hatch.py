"""The audited maintenance hatch, against real Postgres with distinct DB roles (SET SESSION AUTHORIZATION).

Authorisation is a research_maintenance_session row written by research_maintenance_begin() for THIS transaction;
no GUC is trusted. These tests prove the ticket lifecycle (open -> approve by a DIFFERENT user -> begin -> use ->
close/expire), the target-table binding, the audit trail, and that the forgeable routes (set_config, direct inserts,
a copied txid) do not open the hatch. Needs a superuser DB role (skips otherwise; CI's trading_user is one)."""
import psycopg2
import psycopg2.errors
import pytest

from conftest import Seed

OBS = "candidate_observation"


def _refused(conn, sql, params=None, match=None):
    cur = conn.cursor()
    cur.execute("SAVEPOINT s")
    with pytest.raises(psycopg2.Error) as exc:
        cur.execute(sql, params)
    cur.execute("ROLLBACK TO SAVEPOINT s")
    if match:
        assert match in str(exc.value), str(exc.value)
    return exc.value


def _call(conn, who, roles, sql, params=None):
    roles.as_user(conn, who)
    cur = conn.cursor()
    cur.execute(sql, params)
    out = cur.fetchone()
    conn.commit()
    return out[0] if out else None


def _approved_ticket(conn, roles, table=OBS, minutes=60, opener=None, approver=None):
    opener, approver = opener or roles.alice, approver or roles.bob
    ticket = _call(conn, opener, roles, "SELECT research_maintenance_open(%s, %s, %s)",
                   (table, "correct a mislabelled test row", minutes))
    _call(conn, approver, roles, "SELECT research_maintenance_approve(%s)", (ticket,))
    return ticket


@pytest.fixture
def world(roles):
    """A superuser connection with one captured observation, plus the role helpers."""
    with roles.connect() as conn:
        seed = Seed(conn)
        obs = seed.observation(symbol="AAA")
        conn.commit()
        yield conn, roles, obs


def test_full_lifecycle_update_is_audited(world):
    conn, roles, obs = world
    ticket = _approved_ticket(conn, roles)
    roles.as_user(conn, roles.alice)
    cur = conn.cursor()
    cur.execute("SELECT research_maintenance_begin(%s)", (ticket,))
    cur.execute("UPDATE candidate_observation SET symbol = 'FIXED' WHERE id = %s", (obs,))
    cur.execute("SELECT table_name, operation, old_row->>'symbol', new_row->>'symbol', performed_by "
                "FROM research_maintenance_audit WHERE ticket_id = %s", (ticket,))
    assert cur.fetchone() == ("candidate_observation", "UPDATE", "AAA", "FIXED", roles.alice)
    conn.commit()
    _call(conn, roles.alice, roles, "SELECT research_maintenance_close(%s)", (ticket,))
    roles.as_user(conn, roles.alice)
    _refused(conn, "SELECT research_maintenance_begin(%s)", (ticket,), match="not usable")


def test_delete_and_truncate_are_audited(world):
    conn, roles, obs = world
    ticket = _approved_ticket(conn, roles)
    roles.as_user(conn, roles.alice)
    cur = conn.cursor()
    cur.execute("SELECT research_maintenance_begin(%s)", (ticket,))
    cur.execute("DELETE FROM candidate_observation WHERE id = %s", (obs,))
    cur.execute("SELECT old_row->>'symbol' FROM research_maintenance_audit WHERE operation = 'DELETE' AND ticket_id = %s", (ticket,))
    assert cur.fetchone()[0] == "AAA"
    conn.commit()
    # TRUNCATE: the activation table has no referrers, so a TRUNCATE of it alone reaches the trigger. (The other three
    # immutable tables are FK-referenced, so PostgreSQL itself refuses to truncate them on their own.)
    roles.as_user(conn, roles.alice)
    cur = conn.cursor()
    cur.execute("SELECT id FROM strategies LIMIT 1")
    sid = cur.fetchone()[0]
    cur.execute("SELECT research_capture_set_state(%s, 'enabled', DATE '2099-03-01', 'activation under test')", (sid,))
    conn.commit()
    t2 = _approved_ticket(conn, roles, table="research_capture_activation")
    roles.as_user(conn, roles.alice)
    cur = conn.cursor()
    cur.execute("SELECT research_maintenance_begin(%s)", (t2,))
    cur.execute("TRUNCATE research_capture_activation")
    cur.execute("SELECT (old_row->>'rows_removed')::int FROM research_maintenance_audit WHERE operation = 'TRUNCATE' AND ticket_id = %s", (t2,))
    assert cur.fetchone()[0] == 1
    conn.commit()


def test_no_ticket_means_no_mutation(world):
    conn, roles, obs = world
    roles.as_user(conn, roles.alice)
    for sql in ("UPDATE candidate_observation SET symbol = 'X'", "DELETE FROM candidate_observation",
                "TRUNCATE research_capture_activation"):
        _refused(conn, sql, match="immutable")
    # a superuser connection is not a maintenance ticket either (session_user has no begun, approved ticket)
    roles.back_to_super(conn)
    _refused(conn, "TRUNCATE candidate_observation, signal_ledger", match="immutable")
    _refused(conn, "DELETE FROM feature_snapshot", match="immutable")


def test_an_approved_ticket_is_not_enough_without_begin(world):
    conn, roles, obs = world
    _approved_ticket(conn, roles)
    roles.as_user(conn, roles.alice)
    _refused(conn, "DELETE FROM candidate_observation", match="immutable")


def test_the_approver_must_differ_from_the_opener(world):
    conn, roles, obs = world
    ticket = _call(conn, roles.alice, roles, "SELECT research_maintenance_open(%s, %s, 60)", (OBS, "correct a mislabelled test row"))
    roles.as_user(conn, roles.alice)
    _refused(conn, "SELECT research_maintenance_approve(%s)", (ticket,), match="must differ")
    # the table CHECK also refuses a self-approval written around the function (superuser connection)
    roles.back_to_super(conn)
    _refused(conn, "UPDATE research_maintenance_log SET approved_by = opened_by, approved_at = NOW(), "
                   "expires_at = NOW() + INTERVAL '1 hour' WHERE id = %s", (ticket,), match="distinct_approver")


def test_only_the_opener_may_begin_and_a_third_party_cannot_borrow_the_ticket(world):
    conn, roles, obs = world
    ticket = _approved_ticket(conn, roles)
    roles.as_user(conn, roles.bob)
    _refused(conn, "SELECT research_maintenance_begin(%s)", (ticket,), match="only its opener")
    roles.as_user(conn, roles.carol)
    _refused(conn, "SELECT research_maintenance_begin(%s)", (ticket,), match="only its opener")
    _refused(conn, "DELETE FROM candidate_observation", match="immutable")


def test_unapproved_ticket_cannot_begin(world):
    conn, roles, obs = world
    ticket = _call(conn, roles.alice, roles, "SELECT research_maintenance_open(%s, %s, 60)", (OBS, "correct a mislabelled test row"))
    roles.as_user(conn, roles.alice)
    _refused(conn, "SELECT research_maintenance_begin(%s)", (ticket,), match="not usable")


def test_an_expired_ticket_cannot_begin_or_authorise(world):
    conn, roles, obs = world
    ticket = _approved_ticket(conn, roles, minutes=1)
    roles.as_user(conn, roles.alice)
    cur = conn.cursor()
    cur.execute("SELECT research_maintenance_begin(%s)", (ticket,))
    conn.commit()
    # the begin-record is committed; now let the ticket expire (superuser rewrites the clock on the ticket)
    roles.back_to_super(conn)
    conn.cursor().execute("ALTER TABLE research_maintenance_log DISABLE TRIGGER research_maintenance_log_guard_row")
    conn.cursor().execute("UPDATE research_maintenance_log SET approved_at = approved_at - INTERVAL '2 hours', "
                          "expires_at = expires_at - INTERVAL '2 hours', opened_at = opened_at - INTERVAL '3 hours' WHERE id = %s", (ticket,))
    conn.cursor().execute("ALTER TABLE research_maintenance_log ENABLE ALWAYS TRIGGER research_maintenance_log_guard_row")
    conn.commit()
    roles.as_user(conn, roles.alice)
    _refused(conn, "SELECT research_maintenance_begin(%s)", (ticket,), match="not usable")
    _refused(conn, "DELETE FROM candidate_observation", match="immutable")


def test_a_ticket_for_one_table_does_not_open_another(world):
    conn, roles, obs = world
    ticket = _approved_ticket(conn, roles, table="feature_snapshot")
    roles.as_user(conn, roles.alice)
    cur = conn.cursor()
    cur.execute("SELECT research_maintenance_begin(%s)", (ticket,))
    _refused(conn, "DELETE FROM candidate_observation", match="immutable")
    _refused(conn, "UPDATE feature_set_registry SET description = 'x'", match="immutable")
    cur.execute("UPDATE feature_snapshot SET sector = 'Test' WHERE symbol = 'AAA'")  # its own target works
    conn.commit()


def test_the_authorisation_dies_with_the_transaction(world):
    conn, roles, obs = world
    ticket = _approved_ticket(conn, roles)
    roles.as_user(conn, roles.alice)
    cur = conn.cursor()
    cur.execute("SELECT research_maintenance_begin(%s)", (ticket,))
    conn.commit()  # the next statement runs in a NEW transaction (new txid)
    _refused(conn, "DELETE FROM candidate_observation", match="immutable")


def test_the_authorisation_is_per_connection(world, connect):
    conn, roles, obs = world
    ticket = _approved_ticket(conn, roles)
    roles.as_user(conn, roles.alice)
    conn.cursor().execute("SELECT research_maintenance_begin(%s)", (ticket,))
    with connect() as other:
        roles.as_user(other, roles.alice)
        _refused(other, "DELETE FROM candidate_observation", match="immutable")
    conn.rollback()


def test_forging_the_setting_does_not_open_the_hatch(world):
    conn, roles, obs = world
    ticket = _approved_ticket(conn, roles)
    roles.as_user(conn, roles.alice)
    cur = conn.cursor()
    cur.execute("SELECT set_config('research.maintenance_ticket', %s, true)", (str(ticket),))
    cur.execute("SELECT set_config('research.maintenance_txid', txid_current()::text, true)")
    _refused(conn, "DELETE FROM candidate_observation", match="immutable")
    cur.execute("SET research.maintenance_ticket = 1")
    _refused(conn, "UPDATE candidate_observation SET symbol = 'X'", match="immutable")


def test_a_begin_record_forged_by_a_non_definer_is_refused(world):
    conn, roles, obs = world
    ticket = _approved_ticket(conn, roles)
    roles.as_user(conn, roles.alice)
    _refused(conn, "INSERT INTO research_maintenance_session (ticket_id, txid, backend_pid) "
                   "VALUES (%s, txid_current(), pg_backend_pid())", (ticket,))
    _refused(conn, "INSERT INTO research_maintenance_log (opened_by, reason, target_table, approved_by, approved_at, expires_at) "
                   "VALUES ('a', 'a long enough reason', 'candidate_observation', 'b', NOW(), NOW() + INTERVAL '1 hour')")
    _refused(conn, "DELETE FROM candidate_observation", match="immutable")


def test_a_session_row_whose_user_does_not_match_is_ignored(world):
    """Even a begin-record planted by a superuser authorises nothing unless it matches session_user, backend and txid."""
    conn, roles, obs = world
    ticket = _approved_ticket(conn, roles)
    roles.back_to_super(conn)
    conn.cursor().execute("INSERT INTO research_maintenance_session (ticket_id, txid, backend_pid, session_user_name) "
                          "VALUES (%s, txid_current(), pg_backend_pid(), %s)", (ticket, roles.bob))
    roles.as_user(conn, roles.alice)
    _refused(conn, "DELETE FROM candidate_observation", match="immutable")


def test_ticket_closure_is_final_and_the_log_is_append_only(world):
    conn, roles, obs = world
    ticket = _approved_ticket(conn, roles)
    _call(conn, roles.bob, roles, "SELECT research_maintenance_close(%s)", (ticket,))  # the approver may close too
    roles.as_user(conn, roles.carol)
    _refused(conn, "SELECT research_maintenance_close(%s)", (ticket,), match="cannot be closed")
    roles.back_to_super(conn)
    _refused(conn, "UPDATE research_maintenance_log SET closed_at = NULL WHERE id = %s", (ticket,), match="cannot be reopened")
    _refused(conn, "UPDATE research_maintenance_log SET reason = 'rewritten history of the ticket' WHERE id = %s", (ticket,), match="write-once")
    _refused(conn, "UPDATE research_maintenance_log SET approved_by = 'x' WHERE id = %s", (ticket,))
    _refused(conn, "DELETE FROM research_maintenance_log", match="append-only")
    _refused(conn, "TRUNCATE research_maintenance_log, research_maintenance_session, research_maintenance_audit", match="append-only")


def test_an_approval_cannot_be_replayed(world):
    conn, roles, obs = world
    ticket = _approved_ticket(conn, roles)
    roles.as_user(conn, roles.carol)
    _refused(conn, "SELECT research_maintenance_approve(%s)", (ticket,), match="not open for approval")


def test_audit_and_begin_records_are_append_only_even_inside_a_ticket(world):
    conn, roles, obs = world
    ticket = _approved_ticket(conn, roles)
    roles.as_user(conn, roles.alice)
    cur = conn.cursor()
    cur.execute("SELECT research_maintenance_begin(%s)", (ticket,))
    cur.execute("UPDATE candidate_observation SET symbol = 'FIXED' WHERE id = %s", (obs,))
    conn.commit()
    roles.back_to_super(conn)
    for t in ("research_maintenance_audit", "research_maintenance_session"):
        _refused(conn, f"UPDATE {t} SET id = id", match="append-only")
        _refused(conn, f"DELETE FROM {t}", match="append-only")
        _refused(conn, f"TRUNCATE {t}", match="append-only")


def test_ticket_checks(conn, seed):
    for sql, match in [
        ("INSERT INTO research_maintenance_log (opened_by, reason, target_table) VALUES ('a', 'too short', 'candidate_observation')", "reason_nonempty"),
        ("INSERT INTO research_maintenance_log (opened_by, reason, target_table) VALUES ('a', 'a sufficiently long reason', 'candidate_capture_run')", "target_table_values"),
        ("INSERT INTO research_maintenance_log (opened_by, reason, target_table, valid_minutes) VALUES ('a', 'a sufficiently long reason', 'candidate_observation', 0)", "valid_minutes_range"),
        ("INSERT INTO research_maintenance_log (opened_by, reason, target_table, valid_minutes) VALUES ('a', 'a sufficiently long reason', 'candidate_observation', 241)", "valid_minutes_range"),
        ("INSERT INTO research_maintenance_log (opened_by, reason, target_table, approved_by) VALUES ('a', 'a sufficiently long reason', 'candidate_observation', 'b')", "approval_complete"),
        ("INSERT INTO research_maintenance_log (opened_by, reason, target_table, approved_by, approved_at, expires_at) VALUES ('a', 'a sufficiently long reason', 'candidate_observation', 'a', NOW(), NOW() + INTERVAL '1 hour')", "distinct_approver"),
        ("INSERT INTO research_maintenance_log (opened_by, reason, target_table, approved_by, approved_at, expires_at) VALUES ('a', 'a sufficiently long reason', 'candidate_observation', 'b', NOW(), NOW() - INTERVAL '1 hour')", "expiry_after_approval"),
    ]:
        _refused(conn, sql, match=match)
    assert seed.ticket()  # a well-formed row is accepted


def test_activation_table_is_hatch_correctable_only_with_its_own_ticket(world):
    conn, roles, obs = world
    roles.as_user(conn, roles.alice)
    cur = conn.cursor()
    cur.execute("SELECT id FROM strategies LIMIT 1")
    sid = cur.fetchone()[0]
    cur.execute("SELECT research_capture_set_state(%s, 'enabled', DATE '2099-03-01', 'activation under test')", (sid,))
    conn.commit()
    _refused(conn, "UPDATE research_capture_activation SET note = 'edited without a ticket'", match="immutable")
    wrong = _approved_ticket(conn, roles, table=OBS)
    roles.as_user(conn, roles.alice)
    conn.cursor().execute("SELECT research_maintenance_begin(%s)", (wrong,))
    _refused(conn, "UPDATE research_capture_activation SET note = 'edited with the wrong ticket'", match="immutable")
    conn.rollback()
    right = _approved_ticket(conn, roles, table="research_capture_activation")
    roles.as_user(conn, roles.alice)
    cur = conn.cursor()
    cur.execute("SELECT research_maintenance_begin(%s)", (right,))
    cur.execute("UPDATE research_capture_activation SET note = 'corrected under an approved ticket'")
    conn.commit()
