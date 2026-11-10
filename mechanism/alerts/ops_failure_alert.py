"""Owner-only failure alert for a scheduled production job (first user: donchian-forward-collection).

Called by deploy/vps/run_ops_alert.sh, which a unit's OnFailure= alert service runs on the host. It turns "the job failed" into ONE short message in
the owner's PRIVATE chat with the bot (TelegramClient target 'owner', BOT_OWNER_ID). It never posts to a channel: there is no --to option, the target
is fixed, and PROD_SENDING_ENABLED (the public-channel lock) is neither needed nor touched.

Why it exists: the collector's alert unit used to be ExecStart=/bin/true, so the unit's `failed` state was the only signal. From 2026-10-31 to
2026-11-09 the collector refused every night (exit 5: armed for an older image after a re-pin) and nobody saw it; five sessions were lost for good.

Two independent gates, both required to send:
  * the --send flag (the host wrapper passes it), and
  * OPS_ALERTS_ENABLED=1 in the environment (.env), set by the owner at activation.
Without both, the message is printed and nothing is sent (exit 0). A failed send exits 1, so the alert unit itself shows as failed.

The message holds only the unit name, the systemd result, the exit status, its documented meaning and a short operator detail from the host wrapper.
The detail is truncated and scrubbed of anything that looks like a credential before it is printed or sent; no log contents are forwarded.

    python mechanism/alerts/ops_failure_alert.py --unit donchian-forward-collection --result exit-code --exit-status 5 --detail "..." [--send]
"""
from __future__ import annotations

import argparse
import html
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping, Optional

MECH = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(MECH))
sys.path.insert(0, str(MECH.parent))

ENABLE_ENV = "OPS_ALERTS_ENABLED"
TARGET = "owner"                      # fixed: the owner's private chat. Never a channel.
KIND = "ops_failure_alert"
MAX_DETAIL = 300

# What each exit status means, per unit (the job's own documented contract). Unknown statuses fall back to the systemd result.
EXIT_MEANINGS: Mapping[str, Mapping[int, str]] = {
    "donchian-forward-collection": {
        2: "INCOMPLETE: a required input for the session is missing (prices, discontinuity scan, capture run or sector refresh). "
           "The second fire of the night re-checks; after the session's deadline it becomes a permanent gap.",
        4: "MISSED: the session's decision deadline has passed. This session can no longer be collected (permanent gap).",
        5: "REFUSED before touching anything: usually FORWARD_COLLECTION_ARMED does not match CURRENT_MECHANISM_SHA (an image re-pin disarms the "
           "collector until the arming file is re-created), or there is no trustworthy market calendar. Every night it stays refused is a lost session.",
    },
}
SERVICE_RESULTS = {"exit-code": "exited with a failure status", "timeout": "timed out", "signal": "was killed by a signal",
                   "core-dump": "crashed (core dump)", "watchdog": "watchdog timeout", "start-limit-hit": "hit its start limit",
                   "resources": "could not get the resources to start"}

_SECRET_PATTERNS = (
    re.compile(r"\b\d{6,}:[A-Za-z0-9_-]{30,}\b"),                                         # Telegram bot token
    re.compile(r"(?i)\b([a-z][a-z0-9+.-]*://)[^\s:/@]+:[^\s@/]+@"),                         # credentials inside a URL
    re.compile(r"(?i)\b([A-Z0-9_]*(?:TOKEN|SECRET|PASSWORD|PASSWD|API_?KEY|KEY_ID)[A-Z0-9_]*)\s*[=:]\s*\S+"),
    re.compile(r"\b(?:sk|pk|ghp|gho|github_pat|xox[abp])[-_][A-Za-z0-9_-]{16,}\b"),           # common API key shapes
)


def scrub(text: Optional[str], limit: int = MAX_DETAIL) -> str:
    """Remove anything shaped like a credential, collapse whitespace, truncate."""
    s = " ".join((text or "").split())
    s = _SECRET_PATTERNS[0].sub("[redacted]", s)
    s = _SECRET_PATTERNS[1].sub(lambda m: m.group(1) + "[redacted]@", s)
    s = _SECRET_PATTERNS[2].sub(lambda m: m.group(1) + "=[redacted]", s)
    s = _SECRET_PATTERNS[3].sub("[redacted]", s)
    return s if len(s) <= limit else s[: limit - 1] + "…"


def unit_name(unit: str) -> str:
    u = (unit or "").strip()
    return u[: -len(".service")] if u.endswith(".service") else u


def meaning(unit: str, exit_status: Optional[int], result: Optional[str]) -> str:
    known = EXIT_MEANINGS.get(unit_name(unit), {})
    if exit_status is not None and exit_status in known:
        return known[exit_status]
    r = SERVICE_RESULTS.get((result or "").strip(), (result or "").strip() or "failed")
    return f"The job {r}" + (f" (exit status {exit_status})." if exit_status is not None else ".")


def build_text(unit: str, result: Optional[str], exit_status: Optional[int], detail: Optional[str], now: Optional[datetime] = None) -> str:
    """The owner message (Telegram HTML). Every variable part is escaped."""
    now = now or datetime.now(timezone.utc)
    name = unit_name(unit) or "unknown unit"
    lines = [f"⚠️ <b>Ops alert: {html.escape(name)} failed</b>",
             f"{now.strftime('%Y-%m-%d %H:%M')} UTC · result {html.escape((result or 'unknown').strip())}"
             + (f" · exit {exit_status}" if exit_status is not None else ""),
             "",
             html.escape(meaning(name, exit_status, result))]
    d = scrub(detail)
    if d:
        lines += ["", f"Detail: {html.escape(d)}"]
    lines += ["", "Nothing was retried or changed by this alert. Check the job's log on the server."]
    return "\n".join(lines)


def _int_or_none(v: Optional[str]) -> Optional[int]:
    try:
        return int(str(v).strip())
    except (TypeError, ValueError):
        return None


def send_enabled(send_flag: bool, env: Mapping[str, str] = os.environ) -> bool:
    return bool(send_flag) and env.get(ENABLE_ENV, "0").strip() == "1"


def main(argv=None, *, client_factory=None, env: Optional[Mapping[str, str]] = None) -> int:
    p = argparse.ArgumentParser(description="Owner-only failure alert for a scheduled job (private chat; never a channel).")
    p.add_argument("--unit", required=True)
    p.add_argument("--result", default="")
    p.add_argument("--exit-status", default="")
    p.add_argument("--detail", default="")
    p.add_argument("--send", action="store_true", help=f"send for real (also needs {ENABLE_ENV}=1)")
    a = p.parse_args(argv)
    if env is None:
        try:
            from dotenv import load_dotenv
            load_dotenv(MECH.parent / ".env")
        except ImportError:
            pass
        env = os.environ
    text = build_text(a.unit, a.result, _int_or_none(a.exit_status), a.detail)
    if not send_enabled(a.send, env):
        why = "no --send flag" if not a.send else f"{ENABLE_ENV} is not 1"
        print(f"OPS_ALERT dry run ({why}): nothing sent\n{text}")
        return 0
    if client_factory is None:
        from alerts.telegram_client import TelegramClient
        client_factory = lambda: TelegramClient.from_env(TARGET, dry_run=False)  # noqa: E731
    try:
        client = client_factory()
        client.send_message(text, kind=KIND)
    except Exception as e:  # noqa: BLE001 -- report the type only: an exception message can carry a URL with the token in it
        print(f"OPS_ALERT send FAILED ({type(e).__name__})", file=sys.stderr)
        return 1
    print(f"OPS_ALERT sent to the owner's private chat: {unit_name(a.unit)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
