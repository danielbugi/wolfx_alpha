# mechanism/alerts/star.py
"""The one rule for the ★ marker, shared by the channel digest (digest_builder / digest_format) and the assistant's lists (screens.py).
A stock is starred when it is in the TOP STAR_RANK of two or more of the day's lists. Counting only the top ranks keeps the star meaningful
when the lists run deeper than the visible five rows (with 15 rows per list on a small day nearly every stock is in two lists). Pure, no I/O."""
from __future__ import annotations

from typing import Dict, Optional

STAR_RANK = 5


def is_starred(list_ranks: Optional[Dict[str, int]]) -> bool:
    """list_ranks = {list name: rank} as stored in digest_stocks.list_ranks; None / empty = not in any list."""
    return sum(1 for rank in (list_ranks or {}).values() if rank <= STAR_RANK) >= 2


def starred_lists(list_ranks: Optional[Dict[str, int]]) -> list:
    """The list names in which the stock ranks within STAR_RANK (empty unless it is starred)."""
    if not is_starred(list_ranks):
        return []
    return [name for name, rank in list_ranks.items() if rank <= STAR_RANK]
