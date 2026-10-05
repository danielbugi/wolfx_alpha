"""Market Intelligence stays strategy-neutral and side-effect free: the pure modules import no driver and read no clock, only `store`
touches a database, and nothing in the screener / ledger / ML / bot path imports the package (so deploying it cannot change them)."""
import os
import re

from mi_samples import ROOT

PKG = os.path.join(ROOT, "mechanism", "market_intelligence")
PURE = ["risk_regime.py", "relative_strength.py", "events.py", "provenance.py", "breadth.py", "sector_intel.py", "filing_contract.py",
        "first_seen.py", "stock_rs_rows.py", "edgar_collector.py", "vendor_trial.py"]
DB_MODULES = ["store.py", "first_seen_store.py", "classification_store.py"]   # the only modules that may import a driver; none reads a clock
READ_ONLY_IO = ["inputs.py", "runner.py"]          # touch a database only through a connection handed in; never read the clock or the environment
NO_CLOCK = re.compile(r"datetime\.now|date\.today|utcnow|time\.time\(|os\.environ|requests|urllib|yfinance|sqlalchemy")
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


def test_the_runner_and_loaders_never_read_the_clock_or_the_environment():
    for name in READ_ONLY_IO:
        hit = NO_CLOCK.search(_code_lines(_src(os.path.join(PKG, name))))
        assert not hit, f"{name}: {hit.group(0)}"


def test_the_loaders_only_select():
    text = _code_lines(_src(os.path.join(PKG, "inputs.py")))
    assert not re.search(r"\b(INSERT|UPDATE|DELETE|TRUNCATE|CREATE|ALTER|DROP)\b", text, re.I)


def test_only_store_imports_a_database_driver():
    for name in os.listdir(PKG):
        if name.endswith(".py") and name not in DB_MODULES:
            assert "psycopg2" not in _src(os.path.join(PKG, name)), name


def test_the_database_modules_never_read_the_clock_or_the_environment():
    # availability timestamps are stamped by the database (observed_at / classified_at / created_at), never supplied by the writer
    for name in DB_MODULES:
        hit = NO_CLOCK.search(_code_lines(_src(os.path.join(PKG, name))))
        assert not hit, f"{name}: {hit.group(0)}"


def test_the_store_never_updates_deletes_or_truncates():
    tables = ("universe_snapshot|market_snapshot|sector_snapshot|market_event|market_event_revision|"
              "source_observation|source_poll|catalyst_classification|stock_relative_strength")
    for name in DB_MODULES:
        text = _src(os.path.join(PKG, name))
        assert not re.search(r"\b(UPDATE|DELETE\s+FROM|TRUNCATE(?:\s+TABLE)?)\s+(" + tables + r")\b", text, re.I), name


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
    allowed_prefixes = ("backend/routers/", "backend/services/", "mechanism/alerts/", "mechanism/forward_collection/")
    bad = [o for o in offenders if not o.startswith(allowed_prefixes)]
    assert not bad, bad
    assert not [o for o in offenders if o.startswith(("mechanism/screeners", "mechanism/research", "mechanism/ml_enhancement", "ml_training"))]
