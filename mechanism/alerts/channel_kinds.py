# mechanism/alerts/channel_kinds.py
"""
Which kinds of channel post are LIVE (current) and which are retired, for the Telegram Control Center's "What's live now" panel.

Pure data + two small helpers, no I/O. Telegram has no notion of "live": every message a bot ever sent stays visible in the channel until it is
deleted. The control page therefore needs OUR definition, and this is it (owner decisions 2026-10-21..26: the channel carries ONE daily market
brief and ONE assistant post a day; the other recurring posts are paused).

The registry says what we INTEND. The page always shows the ledger's own "last posted" next to it, so if a retired kind starts posting again
(`resumed`), it is flagged instead of being silently trusted.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence

# When the one-brief format went live (the re-pin finished; the first brief posted at 22:01 UTC the same day). A retired kind that posts
# AFTER this moment is flagged as "resumed".
FORMAT_CHANGED_AT = datetime(2026, 10, 26, 18, 0, tzinfo=timezone.utc)
FORMAT_CHANGED_ON = "2026-10-26"

# Posts the channel carries now.
CURRENT_POSTS: List[Dict[str, Any]] = [
    {"key": "brief", "kinds": ("market_report", "report_card"), "label": "Daily market brief (card + text)",
     "cadence": "Once after each US market close, about 22:00 UTC", "max_quiet_hours": 84},      # 84 h: Friday evening to Monday evening
    {"key": "assistant", "kinds": ("assistant",), "label": "Assistant post (with picture)",
     "cadence": "Every day at 18:00 UTC", "max_quiet_hours": 36},
    {"key": "start_here", "kinds": ("start_here",), "label": "Start here (pinned)",
     "cadence": "Posted by hand when it changes", "max_quiet_hours": None},
]

# Posts that are switched off, and what it takes to bring each back (plain words; the page shows them).
PAUSED_POSTS: List[Dict[str, Any]] = [
    {"key": "old_format", "label": "Old five-post market format (digest lists, momentum board, top gainers, market health)",
     "kinds": ("digest_card", "digest_header", "digest_list", "momentum_board", "board", "top_gainers", "market_health", "health"),
     "reason": f"Replaced by the daily market brief on {FORMAT_CHANGED_ON}.",
     "restore": "Set POST_MARKET_FORMAT=package on the server (a developer step)."},
    {"key": "midday_notice", "label": "12:00 notice (disclaimer + assistant)", "kinds": ("disclaimer",),
     "reason": "Paused 2026-10-22: one assistant post a day, and the disclaimer lives in the brief and the pinned post.",
     "restore": "Re-enable the midday notice timer on the server (a developer step)."},
    {"key": "earnings_post", "label": "Separate 'who reports today' post", "kinds": ("earnings_today",),
     "reason": "Paused 2026-10-22; the brief lists the next session's reporters.",
     "restore": "Re-enable the earnings timer and set CHANNEL_EARNINGS_POST_ENABLED=1 (a developer step)."},
    {"key": "extras", "label": "Extra post, weekly recap and education tips",
     "kinds": ("recap", "promo", "sector", "macro", "gaps", "near_highs", "aligned", "base_rate", "news", "scoreboard", "market_environment"),
     "reason": "Paused 2026-10-26 with the one-brief format.",
     "restore": "Set CHANNEL_EXTRA_POSTS_ENABLED=1 on the server (a developer step)."},
    {"key": "legacy_alerts", "label": "Legacy alerts", "kinds": ("alert_header", "alert_card"), "reason": "Retired earlier.",
     "restore": "Not restorable from here."},
]

OLD_KINDS = tuple(sorted({k for p in PAUSED_POSTS for k in p["kinds"]}))
CURRENT_KINDS = tuple(sorted({k for p in CURRENT_POSTS for k in p["kinds"]} | {"manual", "other"}))


def format_of(kind: Optional[str]) -> str:
    """'old' for a retired kind, 'current' for everything else (a new kind we have not seen yet counts as current, never hidden)."""
    return "old" if (kind or "") in OLD_KINDS else "current"


def current_status(last_sent: Optional[datetime], max_quiet_hours: Optional[int], now: datetime) -> str:
    """'posted' when the last post is recent enough for its cadence (weekends allowed), 'check' when it has been quiet longer, 'never' when none."""
    if last_sent is None:
        return "never"
    if max_quiet_hours is None:
        return "posted"
    return "posted" if (now - last_sent).total_seconds() <= max_quiet_hours * 3600 else "check"


def last_sent_for(kinds: Sequence[str], by_kind: Dict[str, Dict[str, Any]]) -> Optional[datetime]:
    seen = [by_kind[k]["last_sent"] for k in kinds if k in by_kind and by_kind[k].get("last_sent")]
    return max(seen) if seen else None


def live_count_for(kinds: Sequence[str], by_kind: Dict[str, Dict[str, Any]]) -> int:
    return sum(int(by_kind[k].get("live", 0)) for k in kinds if k in by_kind)
