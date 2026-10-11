# mechanism/alerts/access.py
"""
Who may use the private assistant (PRIVATE_ASSISTANT_PLAN.md section 3, decision D1: invite-only allowlist).

Framework-free like bot_service.py, so it is unit-tested without Telegram. Rules:
  * The OWNER is configured in .env (BOT_OWNER_ID) and is never looked up in the database, so a database problem cannot lock the
    owner out and nobody can revoke the owner. With no owner configured the owner commands do not exist, so nobody new can be let
    in (fail closed); only people already stored as active keep their access. If the database cannot be read, the check raises and
    the person gets the generic error reply: never data.
  * Everyone else needs a row in bot_access with status 'active'. Rows are created by redeeming a one-time invitation link or by
    the owner's /approve. A revoked user stays blocked: a new invitation link cannot re-admit them, only an explicit /approve.
  * Nothing is stored about a person who is not authorised (no touch_user, no request log), so strangers leave no data behind.
  * Invitation codes are random, expire, and only their SHA-256 hash is stored.
"""
from __future__ import annotations

import hashlib
import re
import secrets
from dataclasses import dataclass
from datetime import datetime
from typing import Dict, List, Optional, Protocol, Tuple

INVITE_PREFIX = "inv_"
INVITE_TTL_HOURS = 72
NOTE_MAX = 60
RETENTION_DAYS = 30
COOLDOWN_DAYS = 7               # a declined person can ask again after this
PENDING_DAYS = 14               # an unanswered request is deleted after this
MAX_PENDING = 100               # waiting-list cap (beyond it the button answers 'full' and stores nothing)
MAX_MEMBERS = 25                # used by BOT_ACCESS_MODE=auto
MODES = ('approve', 'auto', 'closed')
MEMBERS_CAP_LIMIT = 10000       # the highest member cap the dashboard may set
MODE_KEY, MAX_MEMBERS_KEY = "access_mode", "max_members"     # bot_settings keys (migration 34); no row = the .env value applies
NAME_MAX, USERNAME_MAX = 64, 32
_ID_RE = re.compile(r"^[0-9]{1,15}$")               # ASCII only: \d also matches e.g. full-width digits, which int() then accepts
_CODE_RE = re.compile(r"^[A-Za-z0-9_-]{8,40}$")

OWNER, MEMBER = "owner", "member"


def hash_code(code: str) -> str:
    return hashlib.sha256(code.encode()).hexdigest()


def parse_user_id(text: Optional[str]) -> Optional[int]:
    """'/approve 123456789' -> 123456789; None for anything that is not a plain Telegram user id."""
    parts = (text or "").split()
    token = parts[0] if parts else ""
    return int(token) if _ID_RE.match(token) and int(token) > 0 else None


def clean_note(text: Optional[str]) -> Optional[str]:
    note = " ".join((text or "").split())[:NOTE_MAX]
    return note or None


Profile = Tuple[Optional[str], Optional[str]]           # (first_name, username) as Telegram shows them; either may be missing


def clean_profile(first_name: Optional[str], username: Optional[str]) -> Profile:
    """A person's public Telegram name, trimmed to the column sizes. Mirrored as-is (a removed @username becomes None)."""
    name = " ".join((first_name or "").split())[:NAME_MAX] or None
    user = (username or "").strip().lstrip("@")[:USERNAME_MAX] or None
    return name, user


class AccessStore(Protocol):
    def access_status(self, uid: int) -> Optional[str]: ...
    def set_access(self, uid: int, status: str, invited_by: Optional[int] = None) -> None: ...
    def add_invite(self, code_hash: str, created_by: int, ttl_hours: int, note: Optional[str]) -> None: ...
    def redeem_invite(self, code_hash: str, uid: int) -> Tuple[str, Optional[str]]: ...     # ('ok'|'invalid'|'revoked', note)
    def access_counts(self) -> Dict[str, int]: ...
    def audit(self, actor: int, action: str, target: Optional[int] = None, detail: Optional[str] = None) -> None: ...
    def purge_revoked(self, days: int) -> int: ...
    def request_status(self, uid: int) -> Optional[Dict]: ...
    def add_request(self, uid: int, profile: Optional[Profile] = None) -> bool: ...
    def pending_requests(self, limit: int) -> Tuple[int, List[Dict]]: ...
    def decide_request(self, uid: int, approve: bool) -> Optional[str]: ...
    def purge_requests(self, pending_days: int, cooldown_days: int) -> int: ...
    def count_event(self, event: str) -> None: ...
    def funnel_metrics(self, days: int) -> Dict[str, int]: ...
    def get_setting(self, key: str) -> Optional[str]: ...                    # None also when migration 34 is not applied
    def set_setting(self, key: str, value: str, by: Optional[int]) -> bool: ...   # False when migration 34 is not applied


@dataclass
class RequestResult:
    code: str                                  # sent | auto_approved | pending | cooldown | closed | full | blocked | member
    until: Optional[datetime] = None           # cooldown_until, for 'cooldown'


class Access:
    def __init__(self, store: AccessStore, owner_id: Optional[int], mode: str = "approve", max_members: int = MAX_MEMBERS,
                 max_pending: int = MAX_PENDING):
        if mode not in MODES:
            raise ValueError(f"access mode must be one of {MODES}")
        self.store, self.owner_id, self.mode, self.max_members, self.max_pending = store, owner_id, mode, max_members, max_pending

    # --- the access mode and member cap: the dashboard's setting (bot_settings) wins over the .env value given to __init__
    def effective_mode(self) -> str:
        value = self.store.get_setting(MODE_KEY)
        return value if value in MODES else self.mode

    def effective_max_members(self) -> int:
        value = self.store.get_setting(MAX_MEMBERS_KEY)
        return int(value) if value and value.isdigit() and 0 < int(value) <= MEMBERS_CAP_LIMIT else self.max_members

    def set_mode(self, owner: int, mode: str) -> bool:
        if mode not in MODES:
            raise ValueError(f"access mode must be one of {MODES}")
        if not self.store.set_setting(MODE_KEY, mode, owner):
            return False
        self.store.audit(owner, "mode", None, mode)
        return True

    def set_max_members(self, owner: int, n: int) -> bool:
        if not 0 < n <= MEMBERS_CAP_LIMIT:
            raise ValueError(f"the member cap must be between 1 and {MEMBERS_CAP_LIMIT}")
        if not self.store.set_setting(MAX_MEMBERS_KEY, str(n), owner):
            return False
        self.store.audit(owner, "cap", None, str(n))
        return True

    def is_owner(self, uid: int) -> bool:
        return self.owner_id is not None and uid == self.owner_id

    def role(self, uid: int) -> Optional[str]:
        """'owner' | 'member' | None (not authorised)."""
        if self.is_owner(uid):
            return OWNER
        return MEMBER if self.store.access_status(uid) == "active" else None

    # --- owner actions (the caller has already checked is_owner)
    def create_invite(self, owner: int, note: Optional[str], ttl_hours: int = INVITE_TTL_HOURS) -> str:
        code = secrets.token_urlsafe(9)                       # 12 URL-safe characters, ~72 bits
        note = clean_note(note)
        self.store.add_invite(hash_code(code), owner, ttl_hours, note)
        self.store.audit(owner, "invite", None, note)
        return INVITE_PREFIX + code

    def approve(self, owner: int, target: int) -> str:
        if self.is_owner(target):
            return "That is your own id - the owner always has access."
        self.store.set_access(target, "active", owner)
        self.store.decide_request(target, True)                # a typed /approve also clears that person's waiting request
        self.store.audit(owner, "approve", target)
        return f"Access granted to {target}."

    def revoke(self, owner: int, target: int) -> str:
        if self.is_owner(target):
            return "You cannot revoke the owner."
        if self.store.access_status(target) is None:
            return f"{target} has no access to revoke."
        self.store.set_access(target, "revoked", owner)
        self.store.audit(owner, "revoke", target)
        return (f"Access revoked for {target}. They are blocked now; their saved data is deleted after "
                f"{RETENTION_DAYS} days.")

    def summary(self) -> Dict[str, int]:
        return self.store.access_counts()

    def purge(self) -> int:
        n = self.store.purge_revoked(RETENTION_DAYS)
        if n and self.owner_id is not None:
            self.store.audit(self.owner_id, "purge", None, f"{n} revoked user(s)")
        self.store.purge_requests(PENDING_DAYS, COOLDOWN_DAYS)     # unanswered requests and finished cool-downs are deleted too
        return n

    # --- the request-access flow (FUNNEL_PLAN.md section 3). A person is stored ONLY after they tapped "Request access".
    def request_state(self, uid: int) -> Dict:
        """Read-only: what may a person without access do? {'can_request', 'reason': None|member|closed|blocked|pending|cooldown, 'until', 'instant'}"""
        if self.role(uid) is not None:
            return {"can_request": False, "reason": "member", "until": None}
        if self.effective_mode() == "closed":
            return {"can_request": False, "reason": "closed", "until": None}
        if self.store.access_status(uid) == "revoked":
            return {"can_request": False, "reason": "blocked", "until": None}
        req = self.store.request_status(uid)
        if req and req["status"] == "pending":
            return {"can_request": False, "reason": "pending", "until": None}
        if req and req.get("cooldown_active"):
            return {"can_request": False, "reason": "cooldown", "until": req.get("cooldown_until")}
        instant = self.effective_mode() == "auto" and self.store.access_counts()["active"] < self.effective_max_members()
        return {"can_request": True, "reason": None, "until": None, "instant": instant}     # instant = the button lets them straight in

    def request(self, uid: int, profile: Optional[Profile] = None) -> RequestResult:
        st = self.request_state(uid)
        if not st["can_request"]:
            return RequestResult(st["reason"], st["until"])
        if self.effective_mode() == "auto" and self.store.access_counts()["active"] < self.effective_max_members():
            self.store.set_access(uid, "active", self.owner_id)
            self.store.count_event("approved_auto")
            self.store.audit(uid, "approve", uid, "auto")
            return RequestResult("auto_approved")
        if self.store.pending_requests(0)[0] >= self.max_pending:
            return RequestResult("full")                       # nothing is stored when the waiting list is full
        if not self.store.add_request(uid, profile):
            return RequestResult("pending")                    # lost a race with another tap of the same person
        self.store.count_event("requested")
        return RequestResult("sent")

    def approve_request(self, owner: int, uid: int) -> str:
        """'approved' | 'none' (no waiting request: already handled or expired) | 'owner'."""
        if self.is_owner(uid):
            return "owner"
        if self.store.decide_request(uid, True) is None:
            return "none"
        self.store.set_access(uid, "active", owner)
        self.store.audit(owner, "approve", uid, "request")
        return "approved"

    def decline_request(self, owner: int, uid: int) -> str:
        """'declined' | 'none'. The person is remembered only for the cool-down so they cannot ask again at once."""
        if self.store.decide_request(uid, False) is None:
            return "none"
        self.store.count_event("declined")
        self.store.audit(owner, "decline", uid)
        return "declined"

    def waiting(self, limit: int = 5) -> Tuple[int, List[Dict]]:
        return self.store.pending_requests(limit)

    def note_channel_open(self) -> None:
        """Someone opened the bot from the channel button: an AGGREGATE counter only, no user id is stored."""
        self.store.count_event("opened_channel")

    def funnel(self, days: int) -> Dict[str, int]:
        return self.store.funnel_metrics(days)

    # --- a person opening an invitation link
    def redeem(self, uid: int, payload: Optional[str]) -> Tuple[str, Optional[str]]:
        """('ok', note) | ('invalid', None) | ('revoked', None). A payload that is not an invitation is 'invalid'."""
        if not payload or not payload.startswith(INVITE_PREFIX):
            return "invalid", None
        code = payload[len(INVITE_PREFIX):]
        if not _CODE_RE.match(code):
            return "invalid", None
        result, note = self.store.redeem_invite(hash_code(code), uid)
        if result == "ok":
            self.store.audit(uid, "redeem", uid, note)
        return result, note
