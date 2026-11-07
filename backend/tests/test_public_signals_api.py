"""Public Fundamental Updates API (/api/public/signals): field allowlist, no internals, validation without echo, pagination cursor, ETag/304,
rate limiting, client-IP trust, and no authentication dependency. No database: a fake connection answers the two queries the service makes."""
import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

BACKEND = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
ROOT = os.path.dirname(BACKEND)
sys.path.insert(0, BACKEND)
sys.path.insert(0, os.path.join(ROOT, "mechanism"))

from inflection import update_model as UM  # noqa: E402
from routers import public_signals as R  # noqa: E402
from services import public_signals_service as S  # noqa: E402

T0 = datetime(2026, 11, 7, 1, 0, tzinfo=timezone.utc)


def _pj(symbol="POWI", **over):
    u, why = UM.build_update({"symbol": symbol, "event_type": "margin_expansion", "fiscal_year": 2026, "fiscal_quarter": 3, "quarter_end": "2026-09-30",
                              "available_at": "2026-11-05", "availability_basis": "sec_acceptance", "first_detected_on": "2026-11-06",
                              "metrics": {"rev_yoy": 0.055, "op_margin": 0.1524, "op_margin_py": -0.0332, "op_margin_yoy_bps": 1856.6}, "context": {}},
                             company_name="Power Integrations, Inc.", cik=833640, accession="0000833640-26-000156",
                             news=[UM.NewsItem("2026-11-04T23:03:00Z", "Fiscal.ai", "Power Integrations, Inc. Reports Q3 2026 Financial Results", "https://example.com/r")])
    assert u, why
    d = u.to_public_dict()
    d.update(over)
    return d


class FakeConn:
    def __init__(self, store):
        self.store = store

    def cursor(self, cursor_factory=None):
        return FakeCursor(self.store)

    def rollback(self):
        pass

    def close(self):
        pass


class FakeCursor:
    def __init__(self, store):
        self.store, self.rows = store, []

    def execute(self, sql, params=()):
        self.store["sql"].append((sql, params))
        if self.store.get("raise"):
            raise self.store["raise"]
        if "FROM fundamental_update_current" in sql:
            rows = sorted(self.store["current"], key=lambda r: (r["first_published_at"], r["update_key"]), reverse=True)
            if "(first_published_at, update_key) <" in sql:
                ts, key = params[-3], params[-2]
                rows = [r for r in rows if (r["first_published_at"], r["update_key"]) < (ts, key)]
            self.rows = rows[:params[-1]]
        else:
            self.rows = [r for r in self.store["versions"] if r["update_key"] == params[0]]

    def fetchall(self):
        return self.rows

    def close(self):
        pass


@pytest.fixture
def store():
    s = {"sql": [], "current": [], "versions": []}
    for i, sym in enumerate(["AAA", "BBB", "CCC", "DDD", "EEE"]):
        s["current"].append({"update_key": f"{sym}/2026-09-30/margin-expansion", "version": 1, "kind": "initial", "public_json": _pj(sym, key=f"{sym}/2026-09-30/margin-expansion"),
                             "first_published_at": T0 + timedelta(minutes=i), "updated_at": T0 + timedelta(minutes=i)})
    v1 = _pj()
    v2 = dict(v1, figures=[{"label": "Operating margin", "value": "12.0%", "compare": "a year earlier: -3.3%"}] + v1["figures"][1:], content_hash="b" * 64)
    s["versions"] = [{"update_key": "POWI/2026-09-30/margin-expansion", "version": 1, "kind": "initial", "changed": [], "public_json": v1, "published_at": T0},
                     {"update_key": "POWI/2026-09-30/margin-expansion", "version": 2, "kind": "restated", "changed": ["Operating margin", "why"], "public_json": v2,
                      "published_at": T0 + timedelta(days=2)}]
    return s


@pytest.fixture
def client(store, monkeypatch):
    monkeypatch.setattr(R, "_feed_limiter", R.RateLimiter(10_000))
    monkeypatch.setattr(R, "_detail_limiter", R.RateLimiter(10_000))
    app = FastAPI()
    app.include_router(R.public_signals_router)
    app.dependency_overrides[R.get_public_signals_service] = lambda: S.PublicSignalsService(lambda: FakeConn(store))
    return TestClient(app, raise_server_exceptions=False)


INTERNAL = re.compile(r"strength|\"score\"|severity|method_version|event_id|inflection_v|percentile|ret_2d|valuation")


# ======================================================================================================== allowlist / no internals / no auth
def test_response_models_are_exactly_the_allowlist():
    assert tuple(S.PublicUpdate.model_fields) == UM.PUBLIC_FIELDS
    assert set(S.DetailResponse.model_fields) == {"update", "status", "version", "first_published_at", "updated_at", "corrections"}
    assert set(S.FeedItem.model_fields) == {"update", "status", "first_published_at", "updated_at"}
    for m in (S.PublicUpdate, S.Figure, S.NewsItem, S.MarketCap, S.Links, S.FeedItem, S.FeedResponse, S.Correction, S.DetailResponse):
        assert m.model_config.get("extra") == "forbid", m.__name__


def test_the_router_has_no_authentication_and_reads_only_the_publication_record(client, store):
    src = open(os.path.join(BACKEND, "routers", "public_signals.py"), encoding="utf-8").read() + open(os.path.join(BACKEND, "services", "public_signals_service.py"), encoding="utf-8").read()
    assert "auth" not in re.findall(r"^\s*(?:from|import)\s+(\S+)", src, re.M) and "require_authenticated_user" not in src.replace("without require_authenticated_user", "")
    client.get("/api/public/signals")
    client.get("/api/public/signals/POWI/2026-09-30/margin-expansion")
    for sql, _ in store["sql"]:
        tables = set(re.findall(r"FROM\s+(\w+)", sql))
        assert tables <= {"fundamental_update_current", "fundamental_update_publication"}, tables
        assert not re.search(r"\b(INSERT|UPDATE|DELETE|event_id|method_version)\b", sql)


def test_top_level_unknown_fields_are_dropped_and_nested_ones_fail_closed(client, store):
    store["current"][-1]["public_json"] = dict(store["current"][-1]["public_json"], strength=99, event_id=7)
    r = client.get("/api/public/signals")
    assert r.status_code == 200 and not INTERNAL.search(r.text)
    store["current"][-1]["public_json"]["figures"][0]["score"] = 88                 # an unexpected NESTED key: refuse rather than leak
    r = client.get("/api/public/signals")
    assert r.status_code == 503 and "88" not in r.text and "score" not in r.text


# ======================================================================================================== feed + pagination
def test_feed_newest_first_with_cursor_pagination_and_cache_header(client):
    r = client.get("/api/public/signals?limit=2")
    assert r.status_code == 200 and r.headers["cache-control"] == "public, max-age=300"
    body = r.json()
    assert [i["update"]["symbol"] for i in body["items"]] == ["EEE", "DDD"] and body["items"][0]["status"] == "published"
    seen = [i["update"]["symbol"] for i in body["items"]]
    while body["next_cursor"]:
        body = client.get("/api/public/signals", params={"limit": 2, "cursor": body["next_cursor"]}).json()
        seen += [i["update"]["symbol"] for i in body["items"]]
    assert seen == ["EEE", "DDD", "CCC", "BBB", "AAA"]
    assert not INTERNAL.search(r.text)


def test_cursor_round_trip():
    c = S.encode_cursor(T0, "AAA/2026-09-30/margin-expansion")
    assert S.decode_cursor(c) == (T0, "AAA/2026-09-30/margin-expansion") and "AAA" not in c


@pytest.mark.parametrize("query", ["limit=0", "limit=51", "cursor=%21%21notbase64", "cursor=" + "A" * 201, "type=buy-now", "type=" + "&type=".join(["margin-expansion"] * 6),
                                   "sector=Crypto", "cursor=eyJhIjoxfQ"])
def test_feed_rejects_bad_input_without_echoing_it(client, query):
    r = client.get(f"/api/public/signals?{query}")
    assert r.status_code == 422
    assert "buy-now" not in r.text and "Crypto" not in r.text and "notbase64" not in r.text


def test_feed_filters_go_to_sql_as_parameters(client, store):
    assert client.get("/api/public/signals?type=margin-expansion&type=fcf-turnaround&sector=Technology").status_code == 200
    sql, params = store["sql"][-1]
    assert "event_type = ANY(%s)" in sql and "sector = %s" in sql and "Technology" not in sql
    assert params[0] == ["margin_expansion", "fcf_turnaround"] and params[1] == "Technology"


# ======================================================================================================== detail
def test_detail_with_corrections_etag_and_304(client):
    r = client.get("/api/public/signals/POWI/2026-09-30/margin-expansion")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "corrected" and body["version"] == 2 and body["update"]["figures"][0]["value"] == "12.0%"
    assert body["corrections"] == [{"version": 2, "kind": "restated", "changed": ["Operating margin", "why"], "at": "2026-11-09T01:00:00Z"}]
    assert body["first_published_at"] == "2026-11-07T01:00:00Z" and not INTERNAL.search(r.text)
    etag = r.headers["etag"]
    assert etag == '"2-bbbbbbbbbbbbbbbb"' and r.headers["cache-control"] == "public, max-age=300"
    again = client.get("/api/public/signals/POWI/2026-09-30/margin-expansion", headers={"If-None-Match": etag})
    assert again.status_code == 304 and again.content == b""


def test_detail_not_published_is_404(client):
    assert client.get("/api/public/signals/ZZZZ/2026-09-30/margin-expansion").status_code == 404


@pytest.mark.parametrize("path", ["pow i/2026-09-30/margin-expansion", "powi/2026-09-30/margin-expansion", "POWI/2026-9-30/margin-expansion", "POWI/2026-02-30/margin-expansion",
                                  "POWI/1999-12-31/margin-expansion", "POWI/2099-01-01/margin-expansion", "POWI/2026-09-30/limited-repricing", "POWI/2026-09-30/%3Cscript%3E"])
def test_detail_rejects_bad_keys_without_echo(client, path):
    r = client.get(f"/api/public/signals/{path}")
    assert r.status_code in (404, 422) and "script" not in r.text and "pow i" not in r.text.lower()


def test_unapplied_migration_is_a_503_without_details(client, store):
    store["raise"] = Exception('relation "fundamental_update_current" does not exist')
    r = client.get("/api/public/signals")
    assert r.status_code == 503 and "relation" not in r.text
    store["raise"] = Exception("password authentication failed for user x")
    r = client.get("/api/public/signals")
    assert r.status_code == 503 and "password" not in r.text


# ======================================================================================================== rate limiting + client IP
def test_token_bucket():
    now = [0.0]
    rl = R.RateLimiter(60, clock=lambda: now[0])
    assert all(rl.take("a")[0] for _ in range(60))
    ok, wait = rl.take("a")
    assert not ok and wait == 1
    assert rl.take("b")[0]                                         # buckets are per client
    now[0] += 1.0
    assert rl.take("a")[0] and not rl.take("a")[0]


def test_endpoint_returns_429_with_retry_after(client, monkeypatch):
    monkeypatch.setattr(R, "_feed_limiter", R.RateLimiter(2, clock=lambda: 0.0))
    codes = [client.get("/api/public/signals").status_code for _ in range(3)]
    assert codes == [200, 200, 429]
    r = client.get("/api/public/signals")
    assert r.status_code == 429 and int(r.headers["retry-after"]) >= 1


class _Req:
    def __init__(self, peer, xff=None):
        self.client = type("C", (), {"host": peer})()
        self.headers = {"x-forwarded-for": xff} if xff else {}


def test_client_ip_trusts_forwarded_for_only_from_the_proxy():
    import ipaddress
    trusted = [ipaddress.ip_network("172.16.0.0/12")]
    assert R.client_ip(_Req("203.0.113.9", "1.2.3.4"), trusted) == "203.0.113.9"             # a client cannot pick its own bucket
    assert R.client_ip(_Req("172.18.0.5", "1.2.3.4, 198.51.100.7"), trusted) == "198.51.100.7"  # Caddy appends the real peer last
    assert R.client_ip(_Req("172.18.0.5", "garbage"), trusted) == "172.18.0.5"
    assert R.client_ip(_Req("172.18.0.5"), trusted) == "172.18.0.5"
