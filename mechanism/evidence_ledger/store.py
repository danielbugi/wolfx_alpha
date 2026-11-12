"""Evidence Ledger v0: an append-only, hash-chained JSON-lines store (no database migration in v0).

Every line: {seq, kind, key, record, record_hash, identity, prev_line_hash, line_hash}. Opening the store re-verifies the whole chain and refuses to append
to a broken or edited file. A key is first-capture-wins per kind: the same record again is a no-op; a CHANGED record for an existing key is
appended as kind '<kind>_revision' pointing to the first line, so the original discovery (and its timestamp) is never replaced.
The file lives outside the repository; a later database table can import it line for line, keeping each line's own captured_at.
"""
from __future__ import annotations

import hashlib
import json
import os
from typing import Any, Dict, Iterator, List, Mapping, Optional, Tuple

GENESIS = "0" * 64


class LedgerCorrupt(RuntimeError):
    pass


def _canon(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str)


def _h(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


class Store:
    def __init__(self, path: str):
        self.path = path
        self.lines: List[Dict[str, Any]] = []
        self.first: Dict[Tuple[str, str], Dict[str, Any]] = {}
        self.seen: Dict[Tuple[str, str], set] = {}
        if os.path.exists(path):
            self._load()

    def _load(self) -> None:
        prev = GENESIS
        with open(self.path, encoding="utf-8") as fh:
            for n, raw in enumerate(fh, 1):
                line = json.loads(raw)
                body = {k: line[k] for k in ("seq", "kind", "key", "record", "record_hash", "identity", "prev_line_hash")}
                if line["prev_line_hash"] != prev or line["seq"] != n or _h(_canon(line["record"])) != line["record_hash"] or _h(_canon(body)) != line["line_hash"]:
                    raise LedgerCorrupt(f"{self.path}: chain broken at line {n}")
                prev = line["line_hash"]
                self._index(line)
        self._prev = prev

    def _index(self, line: Mapping[str, Any]) -> None:
        base = line["kind"].removesuffix("_revision")
        self.lines.append(dict(line))
        self.first.setdefault((base, line["key"]), dict(line))
        self.seen.setdefault((base, line["key"]), set()).add(line["identity"])

    @property
    def head(self) -> str:
        return getattr(self, "_prev", GENESIS)

    def append(self, kind: str, key: str, record: Mapping[str, Any], *, compare: Optional[str] = None) -> str:
        """'added' | 'unchanged' | 'revision'. `compare` names the field whose value identifies a version (default: the whole record), so a
        re-capture of the same evidence at a later time is 'unchanged' and keeps the first capture time."""
        k = (kind, key)
        identity = _h(_canon(record.get(compare) if compare else record))
        if identity in self.seen.get(k, set()):
            return "unchanged"
        kind_out = kind
        if k in self.first:
            kind_out, record = kind + "_revision", dict(record, revises_seq=self.first[k]["seq"])
        body = {"seq": len(self.lines) + 1, "kind": kind_out, "key": key, "record": record, "record_hash": _h(_canon(record)),
                "identity": identity, "prev_line_hash": self.head}
        line = dict(body, line_hash=_h(_canon(body)))
        os.makedirs(os.path.dirname(os.path.abspath(self.path)), exist_ok=True)
        with open(self.path, "a", encoding="utf-8") as fh:
            fh.write(_canon(line) + "\n")
            fh.flush()
            os.fsync(fh.fileno())
        self._prev = line["line_hash"]
        self._index(line)
        return "revision" if kind_out.endswith("_revision") else "added"

    def records(self, kind: str) -> Iterator[Dict[str, Any]]:
        """First captures of a kind (the discovery records); revisions are available in .lines."""
        for (k, _), line in self.first.items():
            if k == kind:
                yield line["record"]

    def history(self, kind: str) -> Dict[str, List[Dict[str, Any]]]:
        """key -> [first capture, revisions...] in ledger order."""
        out: Dict[str, List[Dict[str, Any]]] = {}
        for line in self.lines:
            if line["kind"].removesuffix("_revision") == kind:
                out.setdefault(line["key"], []).append(line["record"])
        return out
