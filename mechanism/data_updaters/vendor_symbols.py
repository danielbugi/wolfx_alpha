"""Vendor-boundary symbol translation (Slice 12). Standard library only: the non-mutating dry run imports it without a database.

The canonical First Light symbol (`stock_prices.symbol`, `daily_fundamentals.symbol`, the sector-history chains, polls, datasets, candidates, the research
universe) NEVER changes. Only the symbol put on an OUTBOUND vendor request is translated, here, and only at the vendor call. Nothing stores the translated
symbol: it is not a column, not part of a hashed projection and not part of a chain hash.

WHY: yfinance spells a share class with a dash (`BRK-B`, `BF-B`, `BRK-A`); the universe spells the same instruments `BRK.B`, `BF.B`, `BRK/A` (a dot or a
slash). Probed live from the dev machine (2026-10-06, Slice 11) and from the production VPS network (Slice 12): the dotted/slashed forms return no sector, the
dashed forms return a full classification. The same translation already exists for Tiingo (`shared/tiingo_client._normalize_symbol`), which blindly maps
EVERY `.` and `/`; that is deliberately NOT copied here (see below).

THE RULE is only what the evidence supports, and it fails closed:
  * `^[A-Z0-9]+$`                       plain symbol                      -> unchanged
  * `^[A-Z]{1,5}-[A-Z]$`                already a dashed share class      -> unchanged (never re-translated)
  * `^[A-Z]{1,5}[./][A-Z]$`             a single-letter share class       -> `ROOT-X`            (BRK.B -> BRK-B, BRK/A -> BRK-A, BF.B -> BF-B)
    except the class letters U / W / R: those are the conventions for units, warrants and rights, and the vendors spell them differently from each
    other (`-U`, `-WT`, `-WS`, `-RT`), so a dash is a GUESS there -> refused.
  * anything else (lower case, spaces, a second separator, `^`, `=`, `&`, a multi-letter suffix such as `.WS`, a digit in a dotted root, empty) -> REFUSED.
A refusal raises `UnsupportedSymbolFormat`: no request is made and nothing is guessed. The sector recorder records it as a failed poll with the coded
reason `unsupported_symbol_format` (no information was obtained, so it can never be mistaken for a vendor answer).

The translation is many-to-one by design (`BRK.B` and `BRK/B` are two internal symbols for one vendor symbol); the internal identities stay separate.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

# `fullmatch` everywhere: `$` would also match before a trailing newline, and a symbol with a trailing newline must be refused, not accepted.
PLAIN = re.compile(r"[A-Z0-9]+")
DASHED_CLASS = re.compile(r"[A-Z]{1,5}-[A-Z]")
SEPARATED_CLASS = re.compile(r"([A-Z]{1,5})[./]([A-Z])")
AMBIGUOUS_CLASS_LETTERS = ("U", "W", "R")

RULE_PLAIN, RULE_ALREADY_DASHED, RULE_SEPARATOR_TO_DASH = "plain", "share_class_already_dashed", "share_class_separator_to_dash"
UNCHANGED, TRANSLATED = "unchanged", "translated"

R_EMPTY, R_CHARACTERS, R_AMBIGUOUS_CLASS, R_LAYOUT = "empty_symbol", "unsupported_characters", "ambiguous_class_letter", "unsupported_punctuation_layout"


class UnsupportedSymbolFormat(ValueError):
    """The symbol's punctuation is not one the evidence covers; it is refused rather than guessed. Carries a coded `reason`."""

    def __init__(self, canonical: str, reason: str):
        super().__init__(f"unsupported symbol format for a yfinance request: {canonical!r} ({reason})")
        self.canonical = canonical
        self.reason = reason


@dataclass(frozen=True)
class VendorSymbol:
    canonical: str            # the First Light symbol: the ONLY identity anything is stored under
    request: str              # what goes on the wire
    status: str               # unchanged | translated
    rule: str

    def as_dict(self) -> dict:
        return {"canonical": self.canonical, "request": self.request, "status": self.status, "rule": self.rule}


def to_yfinance(canonical: Optional[str]) -> VendorSymbol:
    """The yfinance request symbol for a canonical symbol, or `UnsupportedSymbolFormat`. Pure; never touches the canonical symbol."""
    if not isinstance(canonical, str) or not canonical:
        raise UnsupportedSymbolFormat(str(canonical), R_EMPTY)
    if PLAIN.fullmatch(canonical):
        return VendorSymbol(canonical, canonical, UNCHANGED, RULE_PLAIN)
    if DASHED_CLASS.fullmatch(canonical):
        return VendorSymbol(canonical, canonical, UNCHANGED, RULE_ALREADY_DASHED)
    m = SEPARATED_CLASS.fullmatch(canonical)
    if m:
        if m.group(2) in AMBIGUOUS_CLASS_LETTERS:
            raise UnsupportedSymbolFormat(canonical, R_AMBIGUOUS_CLASS)
        return VendorSymbol(canonical, f"{m.group(1)}-{m.group(2)}", TRANSLATED, RULE_SEPARATOR_TO_DASH)
    if re.search(r"[^A-Z0-9./-]", canonical):
        raise UnsupportedSymbolFormat(canonical, R_CHARACTERS)
    raise UnsupportedSymbolFormat(canonical, R_LAYOUT)
