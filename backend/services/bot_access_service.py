# File: backend/services/bot_access_service.py
"""
Bot Access Control - the backend side of "who may use the First Light private assistant" (mechanism/alerts/access.py).

Wraps the exact same Access/PgStore classes the bot's own owner-only chat commands (/approve, /decline, /revoke,
/invite, /requests, /users) already use, on top of the backend's pooled database connection - see
telegram_control_service.py's PooledDb, extended there with get_sync_connection since Access needs the transactional
update PgStore.decide_request() does, not just single-statement queries. One rule set (access.py), two ways to act on
it (the bot chat, this API); neither can drift from the other since both call the same functions.

Approving or declining also DMs the affected Telegram user directly, mirroring exactly what run_bot.py's owner-button
handler does. mechanism/alerts/telegram_client.py's TelegramClient is deliberately restricted to the three channel
targets (dev/prod/owner) with structural tests forbidding other constructors - a private-chat DM to an arbitrary
approved/declined user is not a channel post, so it goes through a small raw Bot API call here instead (same shape as
run_bot.py's own aiogram Bot.send_message path). Never recorded in the telegram_messages ledger.
"""
import os
import sys
from pathlib import Path
from typing import Callable, Dict, List, Optional

import requests

_MECHANISM = str(Path(__file__).resolve().parents[2] / "mechanism")
if _MECHANISM not in sys.path:
    sys.path.append(_MECHANISM)

from alerts.access import MAX_MEMBERS_KEY, MODE_KEY, MODES, Access  # noqa: E402
from alerts.bot_service import PgStore  # noqa: E402
from alerts import screens, texts  # noqa: E402
from alerts.telegram_client import _load_env  # noqa: E402

from services.telegram_control_service import PooledDb  # noqa: E402

API = "https://api.telegram.org"
_username_cache: Dict[str, Optional[str]] = {}


def _owner_id() -> Optional[int]:
    _load_env()
    raw = os.getenv("BOT_OWNER_ID", "").strip()
    return int(raw) if raw.isdigit() else None


def _mode() -> str:
    _load_env()
    mode = os.getenv("BOT_ACCESS_MODE", "approve").strip().lower()
    return mode if mode in MODES else "approve"


def build_access(get_connection: Callable) -> Access:
    store = PgStore(PooledDb(get_connection))
    return Access(store, _owner_id(), _mode(), int(os.getenv("BOT_MAX_MEMBERS", "25")), int(os.getenv("BOT_MAX_PENDING", "100")))


def _require_owner(access: Access) -> int:
    if access.owner_id is None:
        raise RuntimeError("BOT_OWNER_ID is not set in .env - the bot has no owner, so nobody can be approved/declined/revoked.")
    return access.owner_id


def _to_keyboard(screen) -> Optional[list]:
    if not screen.rows:
        return None
    return [[({"text": b.text, "url": b.url} if b.url else {"text": b.text, "callback_data": b.data or "noop"}) for b in row]
            for row in screen.rows]


def _dm(chat_id: int, text: str, keyboard=None) -> bool:
    """Best-effort direct message to one Telegram user. False (never raises) if it could not be delivered - e.g. they
    blocked the bot or never opened a chat with it - matching notify_user_access's own silent-degrade behaviour in
    run_bot.py: a failed DM never fails the approve/decline action itself."""
    _load_env()
    token = os.getenv("TELEGRAM_BOT_TOKEN", "")
    if not token:
        return False
    payload = {"chat_id": chat_id, "text": text, "parse_mode": "HTML", "disable_web_page_preview": True}
    if keyboard:
        payload["reply_markup"] = {"inline_keyboard": keyboard}
    try:
        resp = requests.post(f"{API}/bot{token}/sendMessage", json=payload, timeout=10)
        return bool(resp.ok and resp.json().get("ok"))
    except requests.RequestException:
        return False


def _bot_username() -> Optional[str]:
    if "u" in _username_cache:
        return _username_cache["u"]
    _load_env()
    token = os.getenv("TELEGRAM_BOT_TOKEN", "")
    username = None
    if token:
        try:
            resp = requests.get(f"{API}/bot{token}/getMe", timeout=10)
            data = resp.json()
            username = (data.get("result") or {}).get("username") if data.get("ok") else None
        except requests.RequestException:
            username = None
    _username_cache["u"] = username
    return username


def _who(row: Dict) -> Dict:
    return {"first_name": row.get("first_name"), "username": row.get("username")}


def overview(get_connection: Callable) -> Dict:
    access = build_access(get_connection)
    counts = access.summary()
    pending_n, pending_rows = access.waiting(limit=25)
    funnel = access.funnel(30)
    store = access.store
    return {
        "mode": access.effective_mode(),
        "mode_from_dashboard": store.get_setting(MODE_KEY) in MODES,
        "settings_available": store.has_member_profiles(),     # migration 34 applied: the mode/cap can be switched here
        "owner_configured": access.owner_id is not None,
        "max_members": access.effective_max_members(),
        "max_pending": access.max_pending,
        "active": counts["active"],
        "revoked": counts["revoked"],
        "open_invites": counts["open_invites"],
        "pending_count": pending_n,
        "pending": [{"telegram_user_id": int(r["telegram_user_id"]), "requested_at": r["requested_at"], **_who(r)} for r in pending_rows],
        "funnel_30d": funnel,
    }


def approve(get_connection: Callable, target: int) -> Dict:
    access = build_access(get_connection)
    owner = _require_owner(access)
    result = access.approve_request(owner, target)          # 'approved' | 'none' (no waiting request) | 'owner'
    if result != "approved":
        return {"result": result, "notified": False}
    guide = screens.onboarding(1)
    notified = _dm(target, texts.ACCESS_GRANTED_USER)
    if notified:
        _dm(target, guide.text, _to_keyboard(guide))
    return {"result": result, "notified": notified}


def decline(get_connection: Callable, target: int) -> Dict:
    access = build_access(get_connection)
    owner = _require_owner(access)
    result = access.decline_request(owner, target)           # 'declined' | 'none'
    if result != "declined":
        return {"result": result, "notified": False}
    status = access.store.request_status(target)              # decide_request already stamped decided_at -> cooldown_until
    until = status.get("cooldown_until") if status else None
    notified = _dm(target, texts.ACCESS_DECLINED_USER.format(date=screens._day(until)))
    return {"result": result, "notified": notified}


def revoke(get_connection: Callable, target: int) -> str:
    access = build_access(get_connection)
    owner = _require_owner(access)
    return access.revoke(owner, target)


def create_invite(get_connection: Callable, note: Optional[str]) -> Dict:
    access = build_access(get_connection)
    owner = _require_owner(access)
    payload = access.create_invite(owner, note)               # 'inv_<code>'
    username = _bot_username()
    return {"payload": payload, "link": f"https://t.me/{username}?start={payload}" if username else None}


def members(get_connection: Callable, status: str) -> List[Dict]:
    """Everyone who has ('active') or had ('revoked') access, newest first. Names appear once migration 34 is applied."""
    access = build_access(get_connection)
    return [{"telegram_user_id": int(r["telegram_user_id"]), **_who(r), "status": r["status"],
             "approved_at": r.get("approved_at"), "revoked_at": r.get("revoked_at"), "last_seen": r.get("last_seen"),
             "finished_guide": r.get("acknowledged_at") is not None, "tracked": int(r.get("tracked_n") or 0),
             "morning_on": bool(r.get("morning_on")), "joined_via": r.get("joined_via")}
            for r in access.store.members(status)]


def restore(get_connection: Callable, target: int) -> Dict:
    """Let a revoked person back in (the same as the bot's /approve) and tell them, best-effort."""
    access = build_access(get_connection)
    owner = _require_owner(access)
    if access.is_owner(target):
        return {"result": "owner", "notified": False}
    if access.store.access_status(target) != "revoked":
        return {"result": "not_revoked", "notified": False}
    access.approve(owner, target)
    return {"result": "restored", "notified": _dm(target, texts.ACCESS_GRANTED_USER)}


def audit(get_connection: Callable, limit: int) -> List[Dict]:
    access = build_access(get_connection)
    return [{"at": r["created_at"], "actor": int(r["actor"]), "action": r["action"],
             "target": int(r["target"]) if r.get("target") is not None else None, "detail": r.get("detail")}
            for r in access.store.audit_rows(limit)]


def update_settings(get_connection: Callable, mode: Optional[str], max_members: Optional[int]) -> Dict:
    """Switch the access mode and/or the member cap. The bot reads them on the next request: no restart. ValueError on bad
    values; RuntimeError when migration 34 is not applied (nowhere to keep them) or there is no owner."""
    access = build_access(get_connection)
    owner = _require_owner(access)
    if not access.store.has_member_profiles():
        raise RuntimeError("Switching from the dashboard needs migration 34 (add_bot_member_profiles.sql). Until then the bot's "
                           ".env values (BOT_ACCESS_MODE / BOT_MAX_MEMBERS) apply.")
    if mode is not None:
        access.set_mode(owner, mode)
    if max_members is not None:
        access.set_max_members(owner, max_members)
    return {"mode": access.effective_mode(), "max_members": access.effective_max_members()}
