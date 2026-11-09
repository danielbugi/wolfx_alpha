"""Public Fundamental Updates against a real PostgreSQL with migration 41: the ledger writes, the public service reads the real view and table.
Keyset pagination stays stable when new updates are published between pages; filters and corrections come back exactly; a never-published key is
absent. Skipped without such a database (the CI db-bootstrap job has one and fails on any skip). Symbols are random: the record is append-only."""
import contextlib
import os
import random
import string
import sys

import pytest

BACKEND = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
ROOT = os.path.dirname(BACKEND)
sys.path.insert(0, BACKEND)
sys.path.insert(0, os.path.join(ROOT, "mechanism"))

from inflection import update_model as UM  # noqa: E402
from inflection.update_ledger import Ledger  # noqa: E402
from services.public_signals_service import PublicSignalsService  # noqa: E402

psycopg2 = pytest.importorskip("psycopg2")


def _connect_raw():
    return psycopg2.connect(host=os.getenv("DB_HOST", "localhost"), port=int(os.getenv("DB_PORT", "5432")), dbname=os.getenv("DB_NAME", "trading_production"),
                            user=os.getenv("DB_USER", "postgres"), password=os.getenv("DB_PASSWORD", ""), connect_timeout=5)


@contextlib.contextmanager
def _connect():
    c = _connect_raw()
    try:
        yield c
    finally:
        c.close()


@pytest.fixture(scope="module")
def env():
    try:
        with _connect() as c:
            c.cursor().execute("SELECT 1 FROM fundamental_update_current LIMIT 1")
    except Exception as e:  # pragma: no cover
        pytest.skip(f"database with migration 41 not reachable: {e}")
    return Ledger(_connect), PublicSignalsService(_connect_raw)


def _update(symbol, kind="fcf_turnaround", **over):
    metrics = {"fcf_turnaround": {"free_cash_flow": 7.3e6, "free_cash_flow_py": -22.7e6, "fcf_margin": 0.041, "fcf_margin_yoy_pp": 17.3},
               "margin_expansion": {"rev_yoy": 0.055, "op_margin": 0.1524, "op_margin_py": -0.0332, "op_margin_yoy_bps": 1856.6}}[kind]
    ev = {"symbol": symbol, "event_type": kind, "fiscal_year": 2026, "fiscal_quarter": 3, "quarter_end": "2026-09-30", "available_at": "2026-11-05",
          "availability_basis": "sec_acceptance", "first_detected_on": "2026-11-06", "metrics": metrics, "context": {}}
    ev.update(over)
    u, why = UM.build_update(ev, company_name=f"{symbol} Inc.", cik=1, profile={"date": "2026-11-05", "sector": "Utilities", "industry": "Utilities - Regulated"},
                             check_freshness=False)
    assert u, why
    return u


def _sym():
    return "ZY" + "".join(random.choices(string.ascii_uppercase, k=6))


def test_feed_pagination_is_stable_while_new_updates_are_published(env):
    ledger, svc = env
    mine = [_sym() for _ in range(5)]
    for s in mine:
        assert ledger.publish(_update(s), 1, "inflection_v2_sec_pit") == "published"
    page = svc.feed(limit=2, types=["fcf-turnaround"], sector="Utilities")
    seen = [i.update.symbol for i in page.items]
    assert seen == mine[::-1][:2]                                                 # newest first
    late = _sym()
    ledger.publish(_update(late), 1, "inflection_v2_sec_pit")                     # published between pages: must not shift the next page
    while page.next_cursor and not set(mine) <= set(seen):
        page = svc.feed(limit=2, cursor=page.next_cursor, types=["fcf-turnaround"], sector="Utilities")
        seen += [i.update.symbol for i in page.items]
    assert [s for s in seen if s in mine] == mine[::-1] and late not in seen and len(seen) == len(set(seen))
    other = svc.feed(limit=50, types=["margin-expansion"])
    assert not {i.update.symbol for i in other.items} & set(mine)                  # the type filter


def test_detail_reports_corrections_and_hides_unpublished(env):
    ledger, svc = env
    s = _sym()
    u = _update(s, kind="margin_expansion")
    ledger.publish(u, 42, "inflection_v2_sec_pit")
    assert ledger.revise(u.key, _update(s, kind="margin_expansion", metrics={"rev_yoy": 0.055, "op_margin": 0.12, "op_margin_py": -0.0332, "op_margin_yoy_bps": 1532.0}),
                         42, "inflection_v2_sec_pit") == "restated"
    d = svc.detail(s, "2026-09-30", "margin-expansion")
    assert d.status == "corrected" and d.version == 2 and d.update.figures[0].value == "12.0%"
    assert [(c.version, c.kind) for c in d.corrections] == [(2, "restated")] and "Operating margin" in d.corrections[0].changed
    dumped = d.model_dump_json()
    assert "event_id" not in dumped and "method_version" not in dumped and "inflection_v2" not in dumped   # internal provenance is never returned
    assert svc.detail(_sym(), "2026-09-30", "margin-expansion") is None


def _base():
    d = _update("ZYBASE", kind="margin_expansion").to_public_dict()
    d["news"] = [{"published_at": "2026-11-05T12:00:00Z", "source": "Wire", "headline": "Results", "url": "https://news.example/a"}]
    d["market_cap"] = {"value": "$2.17B", "as_of": "2026-11-05"}
    return d


def _fig(d, **kv):
    return dict(d, figures=[dict(d["figures"][0], **kv)] + d["figures"][1:])


def _drop(obj, key):
    return {k: v for k, v in obj.items() if k != key}


# (name, mutate, storable). Every case goes through the writer's check, the database function and the public response model.
NESTED_CASES = [
    ("valid", lambda d: d, True),
    ("no news", lambda d: dict(d, news=[]), True),
    ("no market cap", lambda d: dict(d, market_cap=None), True),
    ("no figures", lambda d: dict(d, figures=[]), True),
    ("figure without compare", lambda d: dict(d, figures=[_drop(d["figures"][0], "compare")]), True),
    ("figure compare null", lambda d: _fig(d, compare=None), True),
    ("links without sec", lambda d: dict(d, links={"finviz": "https://f.example", "detail": "https://d.example"}), True),
    ("links company_filings", lambda d: dict(d, links=dict(d["links"], sec_kind="company_filings")), True),
    ("nullable company name", lambda d: dict(d, company_name=None, sector=None, industry=None), True),
    ("score in figure", lambda d: _fig(d, internal_score=91.5), False),
    ("score text in figure", lambda d: _fig(d, internal_score="91.5-SECRET"), False),
    ("numeric figure value", lambda d: _fig(d, value=0.15), False),
    ("numeric compare", lambda d: _fig(d, compare=1), False),
    ("figure missing label", lambda d: dict(d, figures=[_drop(d["figures"][0], "label")]), False),
    ("figure not an object", lambda d: dict(d, figures=["15%"]), False),
    ("figure null", lambda d: dict(d, figures=[None]), False),
    ("news extra key", lambda d: dict(d, news=[dict(d["news"][0], rank=1)]), False),
    ("news missing url", lambda d: dict(d, news=[_drop(d["news"][0], "url")]), False),
    ("news not an object", lambda d: dict(d, news=[None]), False),
    ("news as object", lambda d: dict(d, news=d["news"][0]), False),
    ("internal link", lambda d: dict(d, links=dict(d["links"], admin="https://internal.example/SECRET")), False),
    ("links missing finviz", lambda d: dict(d, links=_drop(d["links"], "finviz")), False),
    ("numeric sec link", lambda d: dict(d, links=dict(d["links"], sec=1)), False),
    ("unknown sec kind", lambda d: dict(d, links=dict(d["links"], sec_kind="internal")), False),
    ("links as list", lambda d: dict(d, links=[]), False),
    ("market cap extra key", lambda d: dict(d, market_cap=dict(d["market_cap"], raw=2.17e9)), False),
    ("market cap missing date", lambda d: dict(d, market_cap=_drop(d["market_cap"], "as_of")), False),
    ("market cap as text", lambda d: dict(d, market_cap="$2.17B"), False),
    ("why as object", lambda d: dict(d, why={"text": "x", "score": 1}), False),
    ("numeric label", lambda d: dict(d, label=5), False),
    ("boolean company name", lambda d: dict(d, company_name=True), False),
    ("numeric sector", lambda d: dict(d, sector=3), False),
    ("missing why", lambda d: _drop(d, "why"), False),
    ("extra top-level score", lambda d: dict(d, score=91.5), False),
]


@pytest.mark.parametrize("name,mutate,storable", NESTED_CASES, ids=[c[0] for c in NESTED_CASES])
def test_writer_database_and_api_agree_on_every_nested_shape(env, name, mutate, storable):
    """One allowlist, three enforcement points: the writer (update_model.public_dict_problem), the database (fundamental_update_public_json_ok
    plus the top-level CHECK) and the public response model. Every payload gets the same verdict from all three."""
    import json
    from pydantic import ValidationError
    from services.public_signals_service import PublicUpdate as ApiUpdate, _public

    d = mutate(_base())
    writer = UM.public_dict_problem(d) is None
    with _connect() as c:
        cur = c.cursor()
        cur.execute("SELECT jsonb_typeof(%s::jsonb) = 'object' AND fundamental_update_public_json_ok(%s::jsonb) "
                    "AND (%s::jsonb - %s::text[]) = '{}'::jsonb AND %s::jsonb ?& %s::text[]",
                    (json.dumps(d), json.dumps(d), json.dumps(d), list(UM.PUBLIC_FIELDS), json.dumps(d), list(UM.PUBLIC_FIELDS)))
        database = cur.fetchone()[0] is True
        c.rollback()
    try:
        ApiUpdate(**d)                                                             # strict: no unknown key, at any level
        _public(d)
        api = True
    except (ValidationError, TypeError):
        api = False
    assert (writer, database, api) == (storable, storable, storable), name


def test_unapproved_fields_in_public_json_never_reach_a_caller(env):
    """Write time: the database refuses an unknown NESTED key (public_nested_chk), as it refuses unknown top-level keys (public_keys_chk).
    Defence in depth: even with that constraint removed (inside this rolled-back transaction), the response models refuse the whole response
    and the router answers a generic 503 instead of returning the field."""
    import json
    from psycopg2 import errors
    from pydantic import ValidationError
    from routers.public_signals import _unavailable

    s = _sym()
    d = _update(s, kind="margin_expansion").to_public_dict()
    d["figures"] = [dict(d["figures"][0], internal_score="91.5-SECRET")] + d["figures"][1:]
    d["links"] = dict(d["links"], admin="https://internal.example/SECRET")
    conn = _connect_raw()
    cur = conn.cursor()
    cur.execute("SAVEPOINT s")
    with pytest.raises(errors.CheckViolation, match="public_nested_chk"):
        cur.execute("INSERT INTO fundamental_update_publication (update_key, version, kind, public_json, content_hash, render_version, symbol, quarter_end, "
                    "event_type, sector, public_date, event_id, method_version) VALUES (%s, 1, 'initial', %s::jsonb, %s, %s, %s, '2026-09-30', %s, 'Utilities', "
                    "'2026-11-05', 1, 'v')", (d["key"], json.dumps(d), d["content_hash"], d["render_version"], s, d["event_type"]))
    cur.execute("ROLLBACK TO SAVEPOINT s")
    cur.execute("ALTER TABLE fundamental_update_publication DROP CONSTRAINT fundamental_update_publication_public_nested_chk")   # rolled back below
    cur.execute("INSERT INTO fundamental_update_publication (update_key, version, kind, public_json, content_hash, render_version, symbol, quarter_end, event_type, "
                "sector, public_date, event_id, method_version) VALUES (%s, 1, 'initial', %s::jsonb, %s, %s, %s, '2026-09-30', %s, 'Utilities', '2026-11-05', 1, 'v')",
                (d["key"], json.dumps(d), d["content_hash"], d["render_version"], s, d["event_type"]))
    svc = PublicSignalsService(lambda: conn)                                        # the service reads in the same transaction, then rolls it back
    with pytest.raises(ValidationError) as caught:
        svc.detail(s, "2026-09-30", "margin-expansion")
    http = _unavailable(caught.value)
    assert http.status_code == 503 and "SECRET" not in str(http.detail) and "internal" not in str(http.detail)
    conn2 = _connect_raw()
    try:
        assert PublicSignalsService(lambda: conn2).detail(s, "2026-09-30", "margin-expansion") is None   # rolled back: never stored
    finally:
        conn2.close()
