"""Market Intelligence stays strategy-neutral and side-effect free: the pure modules import no driver and read no clock, only `store`
touches a database, and nothing in the screener / ledger / ML / bot path imports the package (so deploying it cannot change them)."""
import os
import re

from mi_samples import ROOT

PKG = os.path.join(ROOT, "mechanism", "market_intelligence")
PURE = ["risk_regime.py", "relative_strength.py", "events.py", "provenance.py"]
CLOCK_OR_IO = re.compile(r"psycopg2|sqlalchemy|requests|urllib|yfinance|datetime\.now|date\.today|utcnow|time\.time\(|os\.environ|open\(")


def _src(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def _code_lines(text):
    out, in_doc = [], False
    for line in text.splitlines():
        if line.count('"""') % 2 == 1:
            in_doc = not in_doc
            continue
        if not in_doc and not line.strip().startswith("#"):
            out.append(line.split("  #")[0])
    return "\n".join(out)


def test_pure_modules_have_no_database_network_clock_or_environment():
    for name in PURE:
        hit = CLOCK_OR_IO.search(_code_lines(_src(os.path.join(PKG, name))))
        assert not hit, f"{name}: {hit.group(0)}"


def test_only_store_imports_a_database_driver():
    for name in os.listdir(PKG):
        if name.endswith(".py") and name != "store.py":
            assert "psycopg2" not in _src(os.path.join(PKG, name)), name


def test_the_store_never_updates_deletes_or_truncates():
    text = _src(os.path.join(PKG, "store.py"))
    tables = "universe_snapshot|market_snapshot|sector_snapshot|market_event|market_event_revision"
    assert not re.search(r"\b(UPDATE|DELETE\s+FROM|TRUNCATE(?:\s+TABLE)?)\s+(" + tables + r")\b", text, re.I)


def test_no_strategy_or_ml_path_imports_market_intelligence():
    offenders = []
    for top in ("mechanism", "backend", "ml_training"):
        for base, dirs, files in os.walk(os.path.join(ROOT, top)):
            dirs[:] = [d for d in dirs if d not in ("tests", "__pycache__", "node_modules", "market_intelligence", ".venv")]
            for f in files:
                if not f.endswith(".py"):
                    continue
                rel = os.path.relpath(os.path.join(base, f), ROOT).replace("\\", "/")
                if re.search(r"^\s*(from|import)\s+market_intelligence", _src(os.path.join(base, f)), re.M):
                    offenders.append(rel)
    allowed_prefixes = ("backend/routers/", "backend/services/", "mechanism/alerts/")
    bad = [o for o in offenders if not o.startswith(allowed_prefixes)]
    assert not bad, bad
    assert not [o for o in offenders if o.startswith(("mechanism/screeners", "mechanism/research", "mechanism/ml_enhancement", "ml_training"))]
