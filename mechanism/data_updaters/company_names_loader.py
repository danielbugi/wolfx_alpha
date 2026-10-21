# File: mechanism/data_updaters/company_names_loader.py
"""
One-off / occasional loader for companies.company_name (the table existed but was empty).

Source: Alpaca's asset list (GET /v2/assets, active US equities) with the SAME Alpaca keys the price updater already uses
(ALPACA_API_KEY / ALPACA_API_SECRET). One request, no new provider, no paid service.

Scope: only symbols in our universe (stocks with a bar on the newest date in stock_prices). Alpaca writes class shares
with a dot (BRK.B); our symbols may use a dash, so both spellings are matched. Names are trimmed of the share-class
boilerplate Alpaca appends ("Common Stock", "Class A Common Stock", "Ordinary Shares", ...); nothing else is changed.

Writes: INSERT ... ON CONFLICT (symbol) DO UPDATE of company_name / exchange / updated_at only, and only when the name
actually changed. Additive and idempotent; sector, industry and the other columns are never touched.

Usage (dry run by default; prints counts and samples, writes nothing):
    python company_names_loader.py            # dry run
    python company_names_loader.py --write    # write
Needs DB_HOST/DB_PORT/DB_NAME/DB_USER/DB_PASSWORD and ALPACA_API_KEY/ALPACA_API_SECRET in the environment.
"""
from __future__ import annotations

import json
import os
import re
import sys
import urllib.request
from typing import Dict, Iterable, List, Tuple

ALPACA_HOSTS = ("https://paper-api.alpaca.markets", "https://api.alpaca.markets")
# Only whole trailing share-class phrases, each starting at a word boundary after whitespace (so "Unity" or
# "Broadstone" are never cut).
_SUFFIX = re.compile(
    r"\s+(?:"
    r"(?:class\s+[a-z0-9]+\s+)?(?:common|ordinary|capital|subordinate voting|voting)\s+(?:stock|shares?)\b.*"
    r"|american deposit[ao]ry shares\b.*"
    r"|sponsored adr\b.*"
    r")$",
    re.I,
)


# Trailing listing tags: "CL A", "Class B", ".Class A" (no space), "NEW".
_TAGS = re.compile(r"(?:\s+|(?<=\.))(?:cl|class)\s+[a-z]$|\s+new$", re.I)
_SMALL = {"OF", "THE", "AND", "FOR", "IN", "ON", "AT", "DE"}
_SUFFIX_CASE = {"INC": "Inc", "CORP": "Corp", "LTD": "Ltd", "CO": "Co", "LLC": "LLC", "LP": "LP", "PLC": "plc", "HLDGS": "Holdings"}


def _title_word(w: str, ticker: str, first: bool) -> str:
    core = re.sub(r"[^A-Za-z0-9&]", "", w)
    if not core:
        return w
    if core == ticker or any(ch.isdigit() for ch in core) or "." in w.strip(".,") or "&" in core:
        return w  # the ticker itself, "4", "N.V.", "S.A.", "AT&T"
    if core in _SUFFIX_CASE:
        return w.replace(core, _SUFFIX_CASE[core])
    if core in _SMALL and not first:
        return w.lower()
    if len(core) <= 3:
        return w  # short acronyms: BDC, NV, REX
    return "/".join("-".join(p[:1].upper() + p[1:].lower() for p in part.split("-")) for part in w.split("/"))


def clean_name(name: str, ticker: str = "") -> str:
    """'Braze, Inc. Class A Common Stock' -> 'Braze, Inc.'; 'PAYCOM SOFTWARE, INC.' -> 'Paycom Software, Inc.'.
    Never returns an empty string for a non-empty input."""
    n = " ".join((name or "").split())
    trimmed = _TAGS.sub("", _SUFFIX.sub("", n)).rstrip(" ,-")
    out = trimmed if trimmed else n
    letters = [c for c in out if c.isalpha()]
    if letters and all(c.isupper() for c in letters):
        t = ticker.upper().replace("-", "").replace(".", "")
        out = " ".join(_title_word(w, t, i == 0) for i, w in enumerate(out.split(" ")))
    return out


def fetch_assets() -> List[dict]:
    key, secret = os.environ.get("ALPACA_API_KEY"), os.environ.get("ALPACA_API_SECRET")
    if not key or not secret:
        raise SystemExit("ALPACA_API_KEY / ALPACA_API_SECRET are not set")
    last = None
    for host in ALPACA_HOSTS:
        req = urllib.request.Request(f"{host}/v2/assets?status=active&asset_class=us_equity",
                                     headers={"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": secret})
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:  # wrong environment for these keys: try the other host
            last = f"{host}: HTTP {e.code}"
    raise SystemExit(f"Alpaca asset list unavailable ({last})")


def match(universe: Iterable[str], assets: List[dict]) -> Dict[str, Tuple[str, str]]:
    """{our symbol: (clean name, exchange)} for universe symbols Alpaca knows (dot/dash class spellings both matched)."""
    by_sym = {a["symbol"].upper(): a for a in assets if a.get("symbol") and a.get("name")}
    out = {}
    for s in universe:
        a = by_sym.get(s.upper()) or by_sym.get(s.upper().replace("-", "."))
        if a:
            out[s] = (clean_name(a["name"], s)[:200], (a.get("exchange") or "")[:10] or None)
    return out


def main(write: bool) -> None:
    import psycopg2

    conn = psycopg2.connect(host=os.environ["DB_HOST"], port=os.environ.get("DB_PORT", "5432"), dbname=os.environ["DB_NAME"],
                            user=os.environ["DB_USER"], password=os.environ["DB_PASSWORD"])
    cur = conn.cursor()
    cur.execute("SELECT DISTINCT symbol FROM stock_prices WHERE date = (SELECT MAX(date) FROM stock_prices)")
    universe = sorted(r[0] for r in cur.fetchall())
    assets = fetch_assets()
    names = match(universe, assets)
    print(f"universe {len(universe)} | alpaca assets {len(assets)} | matched {len(names)} | unmatched {len(universe) - len(names)}")
    print("unmatched sample:", [s for s in universe if s not in names][:25])
    for s in ("BRZE", "MYGN", "EFX", "AAPL", "BRK-B", "BRK.B", "GOOGL"):
        if s in names:
            print(f"  {s}: {names[s][0]} ({names[s][1]})")
    if not write:
        print("dry run: nothing written")
        conn.rollback()
        return
    rows = [(s, n, ex) for s, (n, ex) in names.items()]
    cur.executemany(
        """INSERT INTO companies (symbol, company_name, exchange, is_active, created_at, updated_at)
           VALUES (%s, %s, %s, true, NOW(), NOW())
           ON CONFLICT (symbol) DO UPDATE SET company_name = EXCLUDED.company_name, exchange = EXCLUDED.exchange, updated_at = NOW()
           WHERE companies.company_name IS DISTINCT FROM EXCLUDED.company_name""",
        rows,
    )
    conn.commit()
    cur.execute("SELECT COUNT(*), COUNT(company_name) FROM companies")
    print("written; companies rows / with name:", cur.fetchone())
    conn.close()


if __name__ == "__main__":
    main("--write" in sys.argv[1:])
