"""Observed vs reconstructed provenance (D3) -- the one place that decides which rows a read may see.

* observed       captured by the pipeline for that session at the time (or an event we recorded ourselves)
* reconstructed  recomputed later from stored history; the sector map in particular is today's metadata projected backwards

Live and point-in-time research reads are OBSERVED-ONLY by default. A reconstructed row is only reachable by an explicit
`include_reconstructed=True`, and every reader returns the row's provenance so it can never be mistaken for what was known then.
"""
from __future__ import annotations

from typing import Optional, Tuple

OBSERVED = "observed"
RECONSTRUCTED = "reconstructed"
PROVENANCES = (OBSERVED, RECONSTRUCTED)


def read_scope(provenance: Optional[str] = None, include_reconstructed: bool = False) -> Tuple[str, ...]:
    """The provenance values a read is allowed to return.

    read_scope()                                      -> ('observed',)
    read_scope(include_reconstructed=True)            -> ('observed', 'reconstructed')
    read_scope('reconstructed', True)                 -> ('reconstructed',)
    read_scope('reconstructed')                       -> ValueError  (asking for reconstructed data needs the explicit opt-in)
    """
    if provenance is not None and provenance not in PROVENANCES:
        raise ValueError(f"provenance must be one of {PROVENANCES}, not {provenance!r}")
    if provenance == RECONSTRUCTED and not include_reconstructed:
        raise ValueError("reading reconstructed rows requires include_reconstructed=True")
    if provenance is not None:
        return (provenance,)
    return PROVENANCES if include_reconstructed else (OBSERVED,)


def sql_filter(column: str, provenance: Optional[str] = None, include_reconstructed: bool = False) -> Tuple[str, tuple]:
    """('<column> = ANY(%s)', (list,)) -- parameterised; `column` is a trusted identifier supplied by this package, never user input."""
    return f"{column} = ANY(%s)", (list(read_scope(provenance, include_reconstructed)),)
