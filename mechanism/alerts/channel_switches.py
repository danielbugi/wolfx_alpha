# mechanism/alerts/channel_switches.py
"""
Which recurring channel posts are ON. Owner decision 2026-10-21: the channel carries the ONE daily market report
(publish_post_market.py, POST_MARKET_FORMAT=report) and ONE assistant post a day; everything else is paused. Each pause is
an environment switch, so it can be undone without a new image:

  CHANNEL_NOTICE_SLOTS=2           notice slots that send (comma list; default "2" = the 20:00 assistant post only;
                                   "1,2" restores the 12:00 slot, which also carries the one-line disclaimer)
  CHANNEL_EARNINGS_POST_ENABLED=1  the separate 10:00 "who reports today" post (default off: the report lists the
                                   next session's reporters)
  CHANNEL_EXTRA_POSTS_ENABLED=1    the rotating extra post / weekly recap / education tips of send_channel_posts.py's
                                   automatic mode (default off). A manual `--kind X` send is not affected.

The switches only stop SCHEDULED sends to a channel; dry runs and previews to the owner's private chat always work.
"""
from __future__ import annotations

import os
from typing import Set

DEFAULT_NOTICE_SLOTS = "2"


def notice_slots() -> Set[int]:
    raw = os.getenv("CHANNEL_NOTICE_SLOTS", DEFAULT_NOTICE_SLOTS)
    out = set()
    for part in raw.split(","):
        part = part.strip()
        if part.isdigit():
            out.add(int(part))
    return out


def earnings_post_enabled() -> bool:
    return os.getenv("CHANNEL_EARNINGS_POST_ENABLED", "").strip() == "1"


def extra_posts_enabled() -> bool:
    return os.getenv("CHANNEL_EXTRA_POSTS_ENABLED", "").strip() == "1"
