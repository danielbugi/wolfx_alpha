"""Bot member management API (/api/bot-access): owner only, the members list with names, re-admitting a revoked member, the
audit log, and switching the access mode / member cap from the dashboard. No network or database: the access rules are the
real mechanism/alerts/access.py over the bot tests' in-memory store, and the Telegram DM is a fake."""
import os
import sys

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

BACKEND = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
ROOT = os.path.dirname(BACKEND)
sys.path.insert(0, BACKEND)
sys.path.insert(0, os.path.join(ROOT, "mechanism"))
sys.path.insert(0, os.path.join(ROOT, "mechanism", "alerts", "tests"))

from auth.dependencies import CurrentUser, get_auth_store, require_authenticated_user  # noqa: E402
from routers.bot_access import bot_access_router  # noqa: E402
from services import bot_access_service as svc  # noqa: E402
from alerts.access import Access  # noqa: E402
from qa_harness import MemoryStore  # noqa: E402

OWNER = 4242


class Store(MemoryStore):
    profiles = True                                       # migration 34 applied?

    def has_member_profiles(self):
        return self.profiles

    def set_setting(self, key, value, by):
        return super().set_setting(key, value, by) if self.profiles else False


@pytest.fixture
def env(monkeypatch):
    store, dms = Store(), []
    monkeypatch.setattr(svc, "build_access", lambda get_connection: Access(store, OWNER, "approve", 25))
    monkeypatch.setattr(svc, "_dm", lambda chat_id, text, keyboard=None: dms.append((chat_id, text)) or True)
    return store, dms


def _client(role="owner"):
    app = FastAPI()
    app.include_router(bot_access_router)
    app.dependency_overrides[get_auth_store] = lambda: object()
    app.dependency_overrides[require_authenticated_user] = lambda: CurrentUser(1, "t@example.com", role)
    return TestClient(app)


def test_everything_is_owner_only(env):
    c = _client(role="viewer")
    for method, path in (("get", "/api/bot-access/members"), ("get", "/api/bot-access/audit"), ("put", "/api/bot-access/settings"),
                         ("post", "/api/bot-access/members/5/restore")):
        assert getattr(c, method)(path, **({"json": {"mode": "auto"}} if method == "put" else {})).status_code == 403


def test_members_list_shows_names_and_activity(env):
    store, _ = env
    store.set_access(5, "active", OWNER)
    store.touch_user(5, ("Dana", "dana"))
    store.upsert_tracked(5, "AAPL", "watch", 100, "close", None, None, 100)
    store.set_access(6, "revoked", OWNER)
    active = _client().get("/api/bot-access/members").json()["members"]
    assert [(m["telegram_user_id"], m["first_name"], m["username"], m["tracked"]) for m in active] == [(5, "Dana", "dana", 1)]
    revoked = _client().get("/api/bot-access/members?status=revoked").json()["members"]
    assert [m["telegram_user_id"] for m in revoked] == [6]
    assert _client().get("/api/bot-access/members?status=everyone").status_code == 422


def test_restore_lets_a_revoked_member_back_in_and_tells_them(env):
    store, dms = env
    store.set_access(6, "revoked", OWNER)
    r = _client().post("/api/bot-access/members/6/restore")
    assert r.status_code == 200 and r.json() == {"result": "restored", "notified": True}
    assert store.access_status(6) == "active" and dms == [(6, svc.texts.ACCESS_GRANTED_USER)]
    assert _client().post("/api/bot-access/members/6/restore").status_code == 409          # already active: nothing to restore
    assert _client().post("/api/bot-access/members/77/restore").status_code == 409         # never a member
    assert _client().post(f"/api/bot-access/members/{OWNER}/restore").status_code == 400


def test_settings_switch_the_mode_and_cap_and_are_audited(env):
    store, _ = env
    r = _client().put("/api/bot-access/settings", json={"mode": "auto", "max_members": 200})
    assert r.status_code == 200 and r.json() == {"mode": "auto", "max_members": 200}
    ov = _client().get("/api/bot-access/overview").json()
    assert ov["mode"] == "auto" and ov["max_members"] == 200 and ov["mode_from_dashboard"] and ov["settings_available"]
    actions = [(e["action"], e["detail"]) for e in _client().get("/api/bot-access/audit").json()["entries"]]
    assert actions[:2] == [("cap", "200"), ("mode", "auto")]                                 # newest first


@pytest.mark.parametrize("body", [{}, {"mode": "open"}, {"max_members": 0}, {"max_members": 10001}])
def test_bad_settings_are_refused(env, body):
    assert _client().put("/api/bot-access/settings", json=body).status_code in (400, 422)


def test_settings_say_why_they_cannot_be_switched_before_migration_34(env):
    store, _ = env
    store.profiles = False
    r = _client().put("/api/bot-access/settings", json={"mode": "auto"})
    assert r.status_code == 409 and "migration 34" in r.json()["detail"]["message"]
    ov = _client().get("/api/bot-access/overview").json()
    assert ov["mode"] == "approve" and ov["settings_available"] is False                    # the .env value still applies


def test_waiting_requests_carry_the_requesters_name(env):
    store, _ = env
    store.add_request(77, ("Noa", None))
    [p] = _client().get("/api/bot-access/overview").json()["pending"]
    assert (p["telegram_user_id"], p["first_name"], p["username"]) == (77, "Noa", None)
