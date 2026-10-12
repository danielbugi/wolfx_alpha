"""SEC EDGAR collector -- DRY-RUN ONLY (pure). Turns EDGAR "submissions" documents into `FilingFact`s / event drafts and reports what a real run
WOULD write. It never writes: no database, no environment, no file, no clock of its own and no network. The transport (how bytes arrive), the
clock and the sleep are handed in, so a test drives it with a fixture and a fake clock; a live transport does not exist in this module by design
(attaching one is a separate, owner-approved activation step).

Fidelity rules (the contract is `filing_contract`):
  * Facts are copied from the source record verbatim through `build_fact`: accession, CIK, form type, filing date, SEC acceptance timestamp,
    items, report period, primary document. Nothing is defaulted, inferred or "fixed". A record that cannot make a fact is REJECTED with a
    reason (kept in the report), never repaired.
  * The acceptance timestamp must arrive as an explicit UTC instant (`...Z`). A timestamp without a zone is rejected, not assumed.
  * The filer's CIK is the only identity a fact asserts. The document's own CIK must equal the CIK that was requested, else the whole document
    is refused (a mix-up would attach one company's filings to another).
  * A symbol is attached only through supplied `IdentifierLink`s (never guessed), and is recorded with its basis.
  * A classification (if requested) is a separate object that pins the fact's hash; it cannot change the fact.
  * `amends_accession` is NOT in the submissions feed, so it is left unset rather than guessed.
  * Only the "recent" block of a submissions document is read; older pages are counted (`older_pages_not_read`), not fetched.
"""
from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable, Dict, FrozenSet, List, Mapping, Optional, Protocol, Sequence, Tuple

from market_intelligence import filing_contract as F
from market_intelligence.events import EventDraft

BASE_URL = "https://data.sec.gov"
DEFAULT_MAX_RPS = 5.0          # the SEC's published limit is 10/s; stay at half
MAX_FUTURE_SKEW = timedelta(minutes=5)
_ACCEPTED_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?Z$")
_PLACEHOLDER = re.compile(r"example\.(com|org)|your[-_.\s]?(name|email|company)|changeme|placeholder|noreply|no-reply|test@|@test\b|<.*>", re.I)
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(\.[\w-]+)+")
RECENT_COLUMNS = ("accessionNumber", "filingDate", "acceptanceDateTime", "form")


class EdgarError(ValueError):
    """The collector was misconfigured or a document is unusable as a whole."""


class Transport(Protocol):
    def get_json(self, url: str, headers: Mapping[str, str]) -> Mapping[str, Any]: ...


@dataclass(frozen=True)
class EdgarConfig:
    user_agent: str                     # the SEC requires a descriptive agent with contact details; a placeholder is refused
    max_rps: float = DEFAULT_MAX_RPS
    base_url: str = BASE_URL

    def __post_init__(self) -> None:
        ua = self.user_agent or ""
        if not _EMAIL.search(ua) or _PLACEHOLDER.search(ua) or len(ua.split()) < 2:
            raise EdgarError("user_agent must name the operator and a real contact email (e.g. 'Operator Name ops@yourdomain.tld'); "
                             "empty or placeholder values are refused")
        if not 0 < self.max_rps <= 10:
            raise EdgarError("max_rps must be in (0, 10]")


class RateLimiter:
    """Spacing between calls, driven by an injected clock and sleep (so tests need no real time)."""

    def __init__(self, max_per_second: float, now: Callable[[], float], sleep: Callable[[float], None]) -> None:
        self._gap, self._now, self._sleep, self._next = 1.0 / max_per_second, now, sleep, None

    def wait(self) -> float:
        t = self._now()
        waited = 0.0
        if self._next is not None and t < self._next:
            waited = self._next - t
            self._sleep(waited)
            t = self._next
        self._next = t + self._gap
        return waited


def submissions_url(cik: int, base_url: str = BASE_URL) -> str:
    if not isinstance(cik, int) or isinstance(cik, bool) or cik <= 0:
        raise EdgarError(f"cik must be a positive integer, got {cik!r}")
    return f"{base_url}/submissions/CIK{cik:010d}.json"


def raw_hash(record: Mapping[str, Any]) -> str:
    """Hash of the source record exactly as received (audit trail: what the source said, before any contract step)."""
    return hashlib.sha256(json.dumps(record, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()


# ------------------------------------------------------------------ parsing one submissions document
@dataclass(frozen=True)
class Rejected:
    cik: int
    index: Optional[int]
    accession: Optional[str]
    reason: str


@dataclass(frozen=True)
class CollectedFact:
    fact: F.FilingFact
    source_record_hash: str
    symbol: Optional[str]
    symbol_basis: str
    draft: EventDraft
    classification: Optional[F.CatalystClassification] = None


def _column(recent: Mapping[str, Any], name: str, n: int) -> Sequence[Any]:
    col = recent.get(name)
    if col is None:
        return [None] * n
    if not isinstance(col, (list, tuple)) or len(col) != n:
        raise EdgarError(f"filings.recent.{name} is not a column of {n} values")
    return col


def parse_submissions(doc: Mapping[str, Any], cik: int, *, now: datetime, forms: Optional[FrozenSet[str]] = None, since: Optional[date] = None,
                      links: Sequence[F.IdentifierLink] = (), classify: bool = False,
                      known_accessions: FrozenSet[str] = frozenset()) -> Tuple[List[CollectedFact], List[Rejected], Counter]:
    """(facts, rejected, counters) for one document. `now` is supplied by the caller (a sanity bound for acceptance times only)."""
    if now.tzinfo is None:
        raise EdgarError("now must be timezone-aware")
    try:
        doc_cik = int(str(doc.get("cik", "")).lstrip("0") or "0")
    except ValueError:
        doc_cik = 0
    if doc_cik != cik:
        raise EdgarError(f"document is for CIK {doc.get('cik')!r}, but CIK {cik} was requested; refused as a whole")
    filings = doc.get("filings")
    recent = (filings or {}).get("recent") if isinstance(filings, Mapping) else None
    if not isinstance(recent, Mapping):
        raise EdgarError("document has no filings.recent block")
    cols0 = recent.get("accessionNumber")
    if not isinstance(cols0, (list, tuple)):
        raise EdgarError("filings.recent.accessionNumber is missing")
    n = len(cols0)
    cols = {k: _column(recent, k, n) for k in ("accessionNumber", "filingDate", "reportDate", "acceptanceDateTime", "form", "items",
                                               "primaryDocument")}
    forms = frozenset(F.FORM_FAMILY) if forms is None else forms
    facts: List[CollectedFact] = []
    rejected: List[Rejected] = []
    count: Counter = Counter()
    count["older_pages_not_read"] = len((filings or {}).get("files") or [])
    seen: Dict[str, str] = {}
    cutoff = now + MAX_FUTURE_SKEW
    for i in range(n):
        rec = {k: cols[k][i] for k in cols}
        acc = rec["accessionNumber"]
        count["records"] += 1

        def reject(reason: str) -> None:
            count["rejected"] += 1
            rejected.append(Rejected(cik, i, acc if isinstance(acc, str) else None, reason))

        if rec["form"] not in forms:
            count["skipped_form"] += 1
            continue
        acc_at = rec["acceptanceDateTime"]
        if not isinstance(acc_at, str) or not _ACCEPTED_RE.match(acc_at):
            reject("acceptanceDateTime is missing or not an explicit UTC instant (never assumed)")
            continue
        try:
            accepted = datetime.fromisoformat(acc_at.replace("Z", "+00:00"))
            filing_date = date.fromisoformat(rec["filingDate"]) if rec["filingDate"] else None
            report = date.fromisoformat(rec["reportDate"]) if rec["reportDate"] else None
            fact = F.build_fact({"accession": acc, "cik": cik, "form_type": rec["form"], "filing_date": filing_date, "accepted_at": accepted,
                                 "items": rec["items"] or (), "report_period": report, "primary_document": rec["primaryDocument"] or None})
        except (F.FilingContractError, ValueError, TypeError) as e:
            reject(f"{type(e).__name__}: {e}")
            continue
        if fact.accepted_at > cutoff:
            reject("acceptance time is in the future relative to the supplied clock")
            continue
        if since is not None and fact.filing_date < since:
            count["skipped_before_since"] += 1
            continue
        h = raw_hash(rec)
        if acc in seen:
            if seen[acc] == h:
                count["duplicate_identical"] += 1
            else:
                reject("the same accession appears twice with different content")
            continue
        seen[acc] = h
        if acc in known_accessions:
            count["already_known"] += 1
            continue
        symbol, basis = F.resolve_symbol(fact, links) if links else (None, "unmapped")
        facts.append(CollectedFact(fact, h, symbol, basis, F.to_event_draft(fact, symbol, basis),
                                   F.classify_by_items(fact) if classify else None))
    return facts, rejected, count


# ------------------------------------------------------------------ the run
@dataclass
class DryRunReport:
    dry_run: bool = True
    ciks_requested: Tuple[int, ...] = ()
    ciks_complete: List[int] = field(default_factory=list)
    fetch_errors: Dict[int, str] = field(default_factory=dict)
    facts: List[CollectedFact] = field(default_factory=list)
    rejected: List[Rejected] = field(default_factory=list)
    counters: Counter = field(default_factory=Counter)
    throttle_waits: int = 0

    @property
    def complete(self) -> bool:
        return not self.fetch_errors and len(self.ciks_complete) == len(self.ciks_requested)

    @property
    def would_write(self) -> Dict[str, int]:
        """Upper bound against an EMPTY store (the dry run reads no database): one event + one revision per new accession."""
        return {"market_event": len(self.facts), "market_event_revision": len(self.facts),
                "catalyst_classification": sum(1 for f in self.facts if f.classification is not None)}

    def summary(self) -> Dict[str, Any]:
        return {"dry_run": True, "complete": self.complete, "ciks_requested": len(self.ciks_requested), "ciks_complete": len(self.ciks_complete),
                "fetch_errors": dict(self.fetch_errors), "facts": len(self.facts), "rejected": len(self.rejected),
                "counters": dict(self.counters), "would_write": self.would_write}


def collect(cfg: EdgarConfig, transport: Transport, ciks: Sequence[int], *, now: datetime, clock: Callable[[], float], sleep: Callable[[float], None],
            forms: Optional[FrozenSet[str]] = None, since: Optional[date] = None, links: Sequence[F.IdentifierLink] = (), classify: bool = False,
            known_accessions: FrozenSet[str] = frozenset()) -> DryRunReport:
    """Fetch each CIK's submissions through `transport`, parse, and report. One CIK failing (transport error, wrong-CIK or malformed document)
    is recorded and the others continue: the report says `complete=False`, so a caller can never mistake a partial run for a full one."""
    uniq = tuple(dict.fromkeys(ciks))
    rep = DryRunReport(ciks_requested=uniq)
    limiter = RateLimiter(cfg.max_rps, clock, sleep)
    headers = {"User-Agent": cfg.user_agent, "Accept": "application/json"}
    for cik in uniq:
        url = submissions_url(cik, cfg.base_url)
        if limiter.wait() > 0:
            rep.throttle_waits += 1
        try:
            doc = transport.get_json(url, headers)
            facts, rejected, count = parse_submissions(doc, cik, now=now, forms=forms, since=since, links=links, classify=classify,
                                                       known_accessions=known_accessions)
        except Exception as e:  # noqa: BLE001  -- any per-CIK failure is data in the report, not a crash and not a silent skip
            rep.fetch_errors[cik] = f"{type(e).__name__}: {e}"
            continue
        rep.ciks_complete.append(cik)
        rep.facts.extend(facts)
        rep.rejected.extend(rejected)
        rep.counters.update(count)
    return rep


class FixtureTransport:
    """Serves canned documents by URL (tests and offline rehearsal). An unknown URL raises, like a 404."""

    def __init__(self, docs: Mapping[str, Mapping[str, Any]]) -> None:
        self._docs, self.calls = dict(docs), []

    def get_json(self, url: str, headers: Mapping[str, str]) -> Mapping[str, Any]:
        self.calls.append((url, dict(headers)))
        if url not in self._docs:
            raise LookupError(f"404 {url}")
        return self._docs[url]
