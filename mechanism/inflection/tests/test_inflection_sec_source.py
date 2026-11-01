"""SEC-sourced quarters: availability is the filing's ACCEPTANCE date, the event set is separate from the yfinance one (own method version), and the loader never returns a quarter whose
filing was accepted after the as-of date."""
import os
import sys
from datetime import date, datetime, timezone

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "mechanism"))
sys.path.insert(0, ROOT)

from inflection import config as C  # noqa: E402
from inflection import pipeline as P  # noqa: E402

D = date.fromisoformat
ENDS = ["2024-06-30", "2024-09-30", "2024-12-31", "2025-03-31", "2025-06-30", "2025-09-30", "2025-12-31", "2026-03-31"]
REV = [100, 100, 100, 100, 110, 120, 140, 170]
OI = [10, 10, 10, 10, 12, 14, 20, 34]
NI = [5, 5, 5, 5, -1, -1, 2, 9]
FCF = [4, 4, 4, 4, -2, -2, 3, 8]


def rows(with_acceptance=True):
    out = []
    for i, e in enumerate(ENDS):
        end = D(e)
        acc = datetime(end.year + (1 if end.month > 10 else 0), (end.month + 1) % 12 + 1 if end.month < 11 else 1, 20, 21, 30, tzinfo=timezone.utc) if with_acceptance else None
        out.append({"quarter": end, "fiscal_year": end.year, "fiscal_quarter": (end.month - 1) // 3 + 1, "revenue": REV[i] * 1e6, "operating_income": OI[i] * 1e6, "net_income": NI[i] * 1e6,
                    "operating_cash_flow": 8e6, "free_cash_flow": FCF[i] * 1e6, "updated_at": "2026-10-31", "accepted_at": acc})
    return out


def test_acceptance_date_is_the_availability_and_the_source_and_method_version_are_labelled():
    qs = rows()
    f, e = P.build_symbol("ZZSEC", qs, [], "Technology", None, None, None, D("2026-10-01"), C.METHOD_VERSION_SEC)
    assert f and e
    for r in f + e:
        assert r["availability_basis"] == "sec_acceptance" and r["source"] == C.SOURCE_SEC and r["method_version"] == C.METHOD_VERSION_SEC == "inflection_v1_sec"
    by_q = {x["quarter"]: x["accepted_at"].date() for x in qs}
    for r in f:
        assert r["available_at"] == by_q[r["quarter_end"]]


def test_a_quarter_whose_filing_is_accepted_after_asof_is_unknown_on_that_day():
    qs = rows()
    last_acc = qs[-1]["accepted_at"].date()
    f, _ = P.build_symbol("ZZSEC", qs, [], "Technology", None, None, None, last_acc.replace(day=last_acc.day - 1), C.METHOD_VERSION_SEC)
    assert D("2026-03-31") not in {r["quarter_end"] for r in f}


def test_rows_without_acceptance_keep_the_yfinance_path_unchanged():
    f, e = P.build_symbol("ZZYF", rows(with_acceptance=False), [], "Technology", None, None, None, D("2026-10-01"))
    assert f and all(r["method_version"] == C.METHOD_VERSION and r["source"] == C.SOURCE_QUARTERLY and r["availability_basis"] != "sec_acceptance" for r in f + e)


def _db():
    try:
        from shared import db as _d
        _d.execute_dict_query("SELECT 1 FROM sec_quarter LIMIT 1")
        _d.execute_dict_query("SELECT 1 FROM fundamental_inflection_event LIMIT 1")
        return _d
    except Exception as ex:  # pragma: no cover
        pytest.skip(f"database with migrations 38 and 40 not reachable: {ex}")


def test_loader_returns_only_filings_accepted_by_asof_with_their_acceptance_date():
    d = _db()
    cik = 9_900_001
    d.execute_insert("DELETE FROM sec_fact WHERE cik = %s", (cik,))
    d.execute_insert("DELETE FROM sec_quarter WHERE cik = %s", (cik,))
    d.execute_insert("DELETE FROM sec_filing WHERE cik = %s", (cik,))
    d.execute_insert("DELETE FROM sec_symbol_cik WHERE cik = %s", (cik,))
    try:
        d.execute_insert("INSERT INTO sec_symbol_cik (symbol, cik) VALUES ('ZZSEC', %s)", (cik,))
        for acc, filed, accepted, end in (("0000000000-26-000001", "2026-05-05", "2026-05-05 21:10+00", "2026-03-31"), ("0000000000-26-000002", "2026-08-05", "2026-08-05 21:10+00", "2026-06-30")):
            d.execute_insert("INSERT INTO sec_filing (accession, cik, form, filed_date, accepted_at, period_end) VALUES (%s,%s,'10-Q',%s,%s,%s)", (acc, cik, filed, accepted, end))
            for metric, val in (("revenue", 1e8), ("operating_income", 1e7), ("net_income", 5e6)):
                d.execute_insert("INSERT INTO sec_fact (cik, accession, metric, tag, period_start, period_end, value, unit, fy, fp, form, filed_date) VALUES (%s,%s,%s,'t',%s,%s,%s,'USD',2026,'Q1','10-Q',%s)",
                                 (cik, acc, metric, D(end) - __import__("datetime").timedelta(days=89), end, val, filed))
                d.execute_insert("INSERT INTO sec_quarter (cik, period_end, metric, value, unit, basis, fiscal_year, fiscal_quarter, accession, filed_date, form) VALUES (%s,%s,%s,%s,'USD','reported',2026,1,%s,%s,'10-Q')",
                                 (cik, end, metric, val, acc, filed))
        from inflection import store as S
        got = S.load_inputs_sec(D("2026-06-01"), ["ZZSEC"]).quarterly["ZZSEC"]
        assert [r["quarter"] for r in got] == [D("2026-03-31")] and got[0]["accepted_at"] == D("2026-05-05")
        both = S.load_inputs_sec(D("2026-09-01"), ["ZZSEC"]).quarterly["ZZSEC"]
        assert len(both) == 2 and [r["accepted_kind"] for r in both] == ["sec_acceptance", "sec_acceptance"]
        d.execute_insert("DELETE FROM sec_filing WHERE accession = %s", ("0000000000-26-000002",))        # filing row missing: fall back to the filing DATE, never to an assumed lag
        again = S.load_inputs_sec(D("2026-09-01"), ["ZZSEC"]).quarterly["ZZSEC"]
        assert again[1]["accepted_at"] == D("2026-08-05") and again[1]["accepted_kind"] == "sec_filed_date"
    finally:
        d.execute_insert("DELETE FROM sec_fact WHERE cik = %s", (cik,))
        d.execute_insert("DELETE FROM sec_quarter WHERE cik = %s", (cik,))
        d.execute_insert("DELETE FROM sec_filing WHERE cik = %s", (cik,))
        d.execute_insert("DELETE FROM sec_symbol_cik WHERE cik = %s", (cik,))
