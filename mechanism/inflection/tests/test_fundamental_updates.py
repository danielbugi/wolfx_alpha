"""Fundamental Updates (Phase 1, previews only): public allowlist and no-internals, point-in-time rules, news matching, Telegram limits, the card,
corrections, and backward compatibility of the current owner alert. No database, no network (a guard fails any socket use)."""
import io
import json
import os
import re
import socket
import sys
from datetime import date, datetime, timezone

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "mechanism"))
sys.path.insert(0, os.path.join(ROOT, "mechanism", "alerts", "tests"))

from alerts.telegram_client import CAPTION_MAX, plain_text, text_length  # noqa: E402
from inflection import alerts as A  # noqa: E402
from inflection import update_model as UM  # noqa: E402
from inflection.update_card import INK, INK2, MUTED, ACCENT, SURFACE, TILE, H, W, TEXT_TOKENS, render_update_card  # noqa: E402
from inflection.update_format import FOOTER, buttons, caption, text_message  # noqa: E402
from inflection.update_news import core_name, names_company, select_news  # noqa: E402
import tg_html  # noqa: E402


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def refuse(*a, **k):
        raise AssertionError("network use in a Fundamental Updates test")
    monkeypatch.setattr(socket.socket, "connect", refuse)


METRICS = {
    "revenue_acceleration": {"rev_qoq": 0.15, "rev_yoy": 1.0408702325498385, "revenue": 114352000.0, "revenue_py": 56031000.0, "rev_accel_pp": 33.79225033296156,
                             "rev_yoy_prev": 0.7029477292202229},
    "margin_expansion": {"rev_yoy": 0.055138371496564975, "op_margin": 0.15241958621569066, "op_margin_py": -0.03324111369924066, "op_margin_yoy_bps": 1856.6069991493132},
    "operating_leverage_inflection": {"rev_yoy": 0.21451736472621996, "op_leverage": 5.573822103929326, "operating_income": 377576000.0},
    "profitability_turnaround": {"net_income": 45700000.0, "net_income_pq": 75200000.0, "net_income_py": -14700000.0, "cash_conversion": 4.199, "ni_swing_pct_rev": 0.1382467383840696},
    "fcf_turnaround": {"fcf_margin": 0.04105788063666449, "free_cash_flow": 7295000.0, "cash_conversion": None, "fcf_margin_yoy_pp": 17.288113904794468, "free_cash_flow_py": -22662000.0},
}
CONTEXT = {"reaction": {"ret_2d": -0.1397, "report_session": "2026-11-05"},
           "valuation_latest": {"pe_ratio": 50.96, "ps_ratio": 4.93, "market_cap": 2249458688.0, "snapshot_date": "2026-11-06", "ps_percentile_own_history": 0.0588},
           "valuation_at_publication": {"pe_ratio": 88.27, "ps_ratio": 4.83, "market_cap": 2170118912.0, "snapshot_date": "2026-11-05", "ps_percentile_own_history": 0.0303},
           "excess_vs_sector_since_publication": -0.0731}
PROFILE = {"date": "2026-11-05", "sector": "Technology", "industry": "Semiconductors", "market_cap": 2170118912}


def ev(kind="margin_expansion", **over):
    e = {"id": 31234, "symbol": "POWI", "event_type": kind, "severity": "high", "strength": 100.0, "score": 88.0, "fiscal_year": 2026, "fiscal_quarter": 3,
         "quarter_end": "2026-09-30", "available_at": "2026-11-05", "availability_basis": "sec_acceptance", "data_quality": "partial", "method_version": "inflection_v2_sec_pit",
         "metrics": dict(METRICS[kind]) if kind in METRICS else {}, "explanation": ["ENGINE TEXT that must never be published, inflection score 72."],
         "context": json.loads(json.dumps(CONTEXT)), "first_detected_on": "2026-11-06"}
    e.update(over)
    return e


def build(e=None, **kw):
    kw.setdefault("company_name", "Power Integrations, Inc.")
    kw.setdefault("cik", 833640)
    kw.setdefault("accession", "0000833640-26-000156")
    kw.setdefault("profile", PROFILE)
    u, reason = UM.build_update(e or ev(), **kw)
    assert u is not None, reason
    return u


def news_item(headline, published="2026-11-04T23:03:00Z", url=None, symbols=("POWI",), source="fiscal.ai", **over):
    n = {"published_at": published, "updated_at": published, "headline": headline, "source": source, "url": url or f"https://example.com/{abs(hash(headline))}",
         "symbols": list(symbols)}
    n.update(over)
    return n


CUTOFF = datetime(2026, 11, 6, 0, 0, tzinfo=timezone.utc)


# ===================================================================================================================== allowlist / no internals
def test_public_dict_has_exactly_the_allowlisted_keys():
    assert tuple(build().to_public_dict()) == UM.PUBLIC_FIELDS
    assert set(UM.PUBLIC_FIELDS) == {"key", "symbol", "company_name", "event_type", "label", "quarter_end", "fiscal_label", "public_date", "date_source", "sector",
                                     "industry", "market_cap", "figures", "why", "links", "news", "content_hash", "render_version"}


@pytest.mark.parametrize("kind", sorted(UM.PUBLIC_TYPES))
def test_no_internal_field_or_value_reaches_any_public_output(kind):
    u = build(ev(kind))
    blob = json.dumps(u.to_public_dict()) + caption(u)[0] + text_message(u) + json.dumps(buttons(u))
    for word in ("strength", "score", "severity", "inflection_v", "method_version", "partial", "ENGINE TEXT", "ret_2d", "valuation_latest", "percentile", "P/S", "P/E", "ps_ratio",
                 "excess", "reaction", "31234"):
        assert word not in blob, word
    for internal_number in ("-14.0%", "-7.3%", "$2.25B", "50.96", "4.93"):         # price reaction, excess return, latest market cap, latest ratios
        assert internal_number not in blob, internal_number


def test_advice_word_guard_matches_the_channel_test_exactly():
    src = open(os.path.join(ROOT, "mechanism", "alerts", "tests", "test_channel_content.py"), encoding="utf-8").read()
    parts = re.search(r'BANNED = re\.compile\(r"(.*?)"\s*\n\s*r"(.*?)", re\.I\)', src, re.S).groups()
    assert UM.ADVICE_WORDS.pattern == "".join(parts) and UM.ADVICE_WORDS.flags & re.I


@pytest.mark.parametrize("kind", sorted(UM.PUBLIC_TYPES))
def test_every_public_text_we_write_passes_the_advice_guard_and_is_valid_telegram_html(kind):
    u = build(ev(kind))
    cap, _ = caption(u)
    ours = plain_text(cap)
    assert UM.ADVICE_WORDS.findall(ours) == []
    assert not re.search(r"investment advice|\d+\s?%\s+(gain|return|profit|win)", ours, re.I)
    assert tg_html.problems(cap, CAPTION_MAX) == [] and tg_html.problems(text_message(u)) == []
    for label in [x[1] for x in UM.PUBLIC_TYPES.values()] + [FOOTER, "FUNDAMENTAL UPDATE"]:
        assert UM.ADVICE_WORDS.findall(label) == []


# ===================================================================================================================== snapshots
def test_caption_snapshot_margin_expansion():
    cap, dropped = caption(build(news=[UM.NewsItem("2026-11-04T23:03:00Z", "Fiscal.ai", "Power Integrations, Inc. Reports Q3 2026 Financial Results & Outlook",
                                                   "https://example.com/a?x=1&y=2")]))
    assert dropped == []
    assert cap == (
        "\U0001F4CA <b>FUNDAMENTAL UPDATE</b>\n<b>POWI</b> · Power Integrations, Inc.\n<b>Operating margin expanded</b>\n\n"
        "Operating margin moved from -3.3% to 15.2% in a year while revenue changed +5.5%: more of each dollar of sales now reaches operating income.\n\n"
        "<b>Fiscal Q3 2026</b>\n• Operating margin: 15.2% (a year earlier: -3.3%)\n• Margin change: +18.6 points\n• Revenue growth, year over year: +5.5%\n\n"
        "<b>News</b>\n• Nov 4, 23:03 UTC · Fiscal.ai: <a href=\"https://example.com/a?x=1&amp;y=2\">Power Integrations, Inc. Reports Q3 2026 Financial Results &amp; Outlook</a>\n\n"
        "Public Nov 5, 2026 (SEC filing accepted)\nTechnology · Semiconductors · market cap $2.17B on Nov 5, 2026\n"
        "<i>Reported figures from the company's filings. Not advice.</i>")


EXPECTED = {
    "revenue_acceleration": (["Revenue: $114.4M (a year earlier: $56.0M)", "Growth, year over year: +104.1% (previous quarter: +70.3%)", "Change in growth rate: +33.8 points"],
                             "Revenue grew +104.1% from a year earlier, up from +70.3% in the previous quarter: growth sped up instead of slowing."),
    "operating_leverage_inflection": (["Revenue growth, year over year: +21.5%", "Operating income: $377.6M", "Operating income growth vs revenue growth: 5.6x"],
                                      "Operating income grew 5.6 times as fast as revenue (+21.5% year over year): costs rose more slowly than sales."),
    "profitability_turnaround": (["Net income: $45.7M (a year earlier: -$14.7M)", "Net income, previous quarter: $75.2M", "Year-over-year swing, share of revenue: 13.8%"],
                                 "Net income was $45.7M this quarter against -$14.7M a year earlier: the company moved from a loss to positive net income."),
    "fcf_turnaround": (["Free cash flow: $7.3M (a year earlier: -$22.7M)", "Free cash flow margin: 4.1%", "Margin change: +17.3 points"],
                       "Free cash flow was $7.3M this quarter against -$22.7M a year earlier: operations now bring in more cash than the business spends on running and investing in itself."),
}


@pytest.mark.parametrize("kind", sorted(EXPECTED))
def test_figures_and_explanation_snapshot_per_type(kind):
    u = build(ev(kind))
    assert [f"{f.label}: {f.value}" + (f" ({f.compare})" if f.compare else "") for f in u.figures] == EXPECTED[kind][0]
    assert u.why == EXPECTED[kind][1]


def test_keys_links_and_buttons():
    u = build()
    assert u.key == "POWI/2026-09-30/margin-expansion"
    assert u.links == {"finviz": "https://finviz.com/quote.ashx?t=POWI", "detail": "https://dashboard.first-light.finance/signals/POWI/2026-09-30/margin-expansion",
                       "sec": "https://www.sec.gov/Archives/edgar/data/833640/000083364026000156/0000833640-26-000156-index.htm", "sec_kind": "filing"}
    assert buttons(u) == {"inline_keyboard": [[{"text": "SEC filing", "url": u.links["sec"]}, {"text": "Finviz", "url": u.links["finviz"]}],
                                              [{"text": "Details on First Light", "url": u.links["detail"]}]]}
    assert UM.SLUG_TO_TYPE[u.key.split("/")[2]] == "margin_expansion"
    no_acc = build(accession="not-an-accession")
    assert no_acc.links["sec_kind"] == "company_filings" and "CIK=0000833640" in no_acc.links["sec"]
    assert "sec" not in build(cik=None).links and [b["text"] for b in buttons(build(cik=None))["inline_keyboard"][0]] == ["Finviz"]
    assert UM.finviz_link("BRK.B") == "https://finviz.com/quote.ashx?t=BRK-B"


# ===================================================================================================================== eligibility + point in time
@pytest.mark.parametrize("over,reason", [
    ({"event_type": "improvement_with_limited_repricing"}, "event_type_not_public"),
    ({"availability_basis": "assumed_lag_75d"}, "public_date_not_verified"),
    ({"availability_basis": "first_seen"}, "public_date_not_verified"),
    ({"availability_basis": "repricing_window_end(sec_acceptance)"}, "public_date_not_verified"),
    ({"symbol": "bad sym"}, "bad_symbol"),
    # the BILL/ETON shape of 2026-11-06: a June quarter re-dated to a November report, first detected before that date
    ({"quarter_end": "2026-06-30", "available_at": "2026-11-05", "availability_basis": "earnings_calendar", "first_detected_on": "2026-10-31"}, "detected_before_public_date"),
    ({"first_detected_on": None}, "detected_before_public_date"),
    ({"metrics": {"op_margin": 0.15}}, "fewer_than_two_figures"),
    ({"metrics": {"op_margin": 0.15, "op_margin_yoy_bps": 100.0}}, "explanation_inputs_missing"),
])
def test_not_publishable(over, reason):
    u, why = UM.build_update(ev(**over), company_name="X Corp")
    assert u is None and why == reason


def test_nothing_measured_after_publication_changes_the_update():
    base = build()
    later = ev()
    later["context"]["reaction"]["ret_2d"] = 0.5
    later["context"]["valuation_latest"]["market_cap"] = 9.9e12
    later["context"]["excess_vs_sector_since_publication"] = 0.9
    later["strength"], later["score"], later["severity"] = 1.0, 1.0, "low"
    other = build(later)
    assert other.content_hash == base.content_hash and caption(other) == caption(base) and render_update_card(other) == render_update_card(base)


def test_market_cap_and_profile_only_when_dated_on_or_before_the_public_date():
    late = ev()
    late["context"]["valuation_at_publication"]["snapshot_date"] = "2026-11-06"
    u = build(late, profile={**PROFILE, "date": "2026-11-06"})
    assert u.market_cap is None and u.sector is None and u.industry is None
    assert build().market_cap == {"value": "$2.17B", "as_of": "2026-11-05"}


@pytest.mark.parametrize("field,value", [("published_at", "2026-11-06T00:00:01Z"), ("updated_at", "2026-11-06T09:00:00Z"), ("fetched_at", "2026-11-06T09:00:00Z")])
def test_news_after_the_cutoff_never_enters_an_earlier_update(field, value):
    item = news_item("Power Integrations, Inc. Reports Q3 2026 Financial Results")
    item[field] = value
    r = select_news([item], symbol="POWI", company_name="Power Integrations, Inc.", public_date=date(2026, 11, 5), cutoff=CUTOFF)
    assert r["items"] == [] and r["rejected"] == {f"{field}_after_cutoff": 1}


def test_news_window_bounds():
    kw = dict(symbol="POWI", company_name="Power Integrations, Inc.", public_date=date(2026, 11, 5), cutoff=datetime(2026, 11, 12, tzinfo=timezone.utc))
    h = "Power Integrations, Inc. Reports Q3 2026 Financial Results"
    assert select_news([news_item(h, "2026-11-03T23:59:59Z")], **kw)["rejected"] == {"outside_window": 1}
    assert len(select_news([news_item(h, "2026-11-04T00:00:00Z")], **kw)["items"]) == 1
    assert len(select_news([news_item(h, "2026-11-08T23:59:59Z")], **kw)["items"]) == 1
    assert select_news([news_item(h, "2026-11-09T00:00:00Z")], **kw)["rejected"] == {"outside_window": 1}


# ===================================================================================================================== news matching
def test_real_ticker_collisions_roundups_and_ratings_are_rejected():
    """Headlines Alpaca returned for TKO and FTK on 2026-11-04/05."""
    tko = [news_item("Taseko Mines Reports 2026 Third Quarter Financial Results", symbols=["TKO"]),
           news_item("Tikehau Capital Subject to Judicial Search", symbols=["TKO"]),
           news_item("Trekor Metals Announces Date for Third Quarter 2026 Financial Results", symbols=["TKO"]),
           news_item("Guggenheim Maintains Buy on TKO Group Holdings, Raises Price Target to $240", symbols=["TKO"]),
           news_item("TKO Group Holdings, Inc. Reports Third Quarter 2026 Financial Results", symbols=["TKO"]),
           news_item("UnitedHealth, TKO Group, Regeneron And More: CNBC's 'Final Trades'", symbols=["UNH", "TKO", "REGN"]),
           news_item("Why TKO Group Shares Are Trading Higher; Here Are 20 Stocks Moving Premarket", symbols=["TKO"])]
    r = select_news(tko, symbol="TKO", company_name="TKO Group Holdings, Inc.", public_date=date(2026, 11, 4), cutoff=CUTOFF)
    assert [i.headline for i in r["items"]] == ["TKO Group Holdings, Inc. Reports Third Quarter 2026 Financial Results"]
    assert r["rejected"] == {"company_not_named": 3, "analyst_rating": 1, "too_many_symbols": 1, "roundup": 1}
    ftk = select_news([news_item("Berenberg Maintains 'Buy' Rating on flatexDEGIRO", symbols=["FTK"])], symbol="FTK", company_name="Flotek Industries, Inc.",
                      public_date=date(2026, 11, 5), cutoff=CUTOFF)
    assert ftk["items"] == [] and ftk["rejected"] == {"company_not_named": 1}


def test_company_name_matching():
    assert core_name("TKO Group Holdings, Inc.") == "tko" and core_name("Power Integrations, Inc.") == "power integrations"
    assert core_name("The Inc.") is None and core_name(None) is None
    assert names_company("Flotek Q3 revenue doubles", "Flotek Industries, Inc.")                 # distinctive first word
    assert not names_company("Power grid stocks rally", "Power Integrations, Inc.")                # common first word alone is not enough
    assert not names_company("Taseko Mines Reports", "TKO Group Holdings, Inc.")
    assert names_company("Cleveland-Cliffs posts Q3 results", "Cleveland-Cliffs Inc.")


def test_ranking_dedup_limit_and_public_vs_owner_mode():
    items = [news_item("Power Integrations Q3 Adj. EPS $0.46 Beats $0.41 Estimate", "2026-11-04T21:01:00Z"),
             news_item("Power Integrations Sees Q4 Sales $118M-$126M", "2026-11-04T21:10:00Z", url="https://example.com/g"),
             news_item("Power Integrations Sees Q4 Sales $118M-$126M", "2026-11-04T21:11:00Z", url="https://example.com/g2"),      # same headline
             news_item("Power Integrations opens a design center", "2026-11-05T10:00:00Z", url="https://example.com/o"),
             news_item("Power Integrations, Inc. Reports Q3 2026 Financial Results", "2026-11-04T23:03:00Z", url="https://example.com/r"),
             news_item("Power Integrations, Inc. Reports Q3 2026 Financial Results (again)", "2026-11-04T23:04:00Z", url="https://example.com/r")]   # same URL
    kw = dict(symbol="POWI", company_name="Power Integrations, Inc.", public_date=date(2026, 11, 5), cutoff=CUTOFF)
    pub = select_news(items, **kw)
    assert [i.url for i in pub["items"]] == ["https://example.com/r", "https://example.com/g"]            # results release, then outlook; max 2
    assert pub["rejected"] == {"advice_words": 1, "duplicate": 2}
    owner = select_news(items, public=False, **kw)
    assert owner["rejected"] == {"duplicate": 2}


# ===================================================================================================================== Telegram limits + escaping
def test_caption_fits_1024_after_parsing_and_drops_news_first():
    long_news = [UM.NewsItem("2026-11-04T23:03:00Z", "Fiscal.ai", "Power Integrations " + "x" * 190, f"https://example.com/{i}/" + "p" * 300) for i in range(2)]
    u = build(news=long_news, company_name="Power Integrations " + "Very Long Name " * 8)
    cap, dropped = caption(u)
    assert text_length(cap) <= CAPTION_MAX and tg_html.problems(cap, CAPTION_MAX) == []
    assert caption(u, limit=700)[1] in (["news_2"], ["news_2", "news_1"], ["news_2", "news_1", "industry"])
    from inflection.update_format import _compose
    floor = text_length(_compose(u, 0, False, with_links=False))
    tight, dropped = caption(u, limit=floor)
    assert dropped == ["news_2", "news_1", "industry"] and text_length(tight) <= floor and "Margin change" in tight and "Public Nov 5, 2026" in tight
    with pytest.raises(ValueError):
        caption(u, limit=floor - 1)


def test_dynamic_text_is_escaped():
    u = build(company_name="A<b>&C \"Co\"", news=[UM.NewsItem("2026-11-04T23:03:00Z", "S<x>", "Power Integrations <script> & co", "https://e.com/?a=1&b=\"2\"")])
    cap, _ = caption(u)
    assert "<script>" not in cap and "A&lt;b&gt;&amp;C" in cap and 'href="https://e.com/?a=1&amp;b=&quot;2&quot;"' in cap
    assert tg_html.problems(cap, CAPTION_MAX) == []


# ===================================================================================================================== card
def _contrast(a: str, b: str) -> float:
    def lum(h):
        c = [int(h[i:i + 2], 16) / 255 for i in (1, 3, 5)]
        c = [x / 12.92 if x <= 0.03928 else ((x + 0.055) / 1.055) ** 2.4 for x in c]
        return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2]
    hi, lo = sorted((lum(a), lum(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


def test_card_is_a_deterministic_1200x675_png_with_readable_text():
    from PIL import Image
    png = render_update_card(build())
    img = Image.open(io.BytesIO(png))
    assert img.format == "PNG" and img.size == (W, H) == (1200, 675)
    assert render_update_card(build()) == png
    for name, colour in TEXT_TOKENS.items():
        assert _contrast(colour, SURFACE) >= 4.5 and _contrast(colour, TILE) >= 4.5, name


@pytest.mark.parametrize("kind", sorted(UM.PUBLIC_TYPES))
def test_card_renders_every_type_and_extreme_text(kind):
    render_update_card(build(ev(kind), company_name="An Extremely Long Company Name That Will Not Fit On One Line Holdings Corporation"))
    render_update_card(build(ev(kind), company_name=None, profile=None))


def test_card_is_not_the_market_daily_identity():
    from alerts import market_card as MC
    assert SURFACE != MC.SURFACE and ACCENT not in (MC.UP, MC.DOWN)


# ===================================================================================================================== corrections
def test_correction_classification():
    pub = build().to_public_dict()
    assert UM.classify_revision(pub, build()) == ("unchanged", [])
    tiny = ev()
    tiny["metrics"]["op_margin"] += 0.00001                                    # below display precision: not a correction
    assert UM.classify_revision(pub, build(tiny))[0] == "unchanged"
    restated = ev()
    restated["metrics"]["op_margin"] = 0.12
    kind, changed = UM.classify_revision(pub, build(restated))
    assert kind == "restated" and "Operating margin" in changed and "why" in changed
    redated, _ = UM.build_update(ev(available_at="2026-11-07"), company_name="Power Integrations, Inc.", cik=833640, accession="0000833640-26-000156",
                                 profile=PROFILE, check_freshness=False)
    assert UM.classify_revision(pub, redated) == ("redated", ["public_date"])
    assert UM.classify_revision(pub, None) == ("withdrawn", ["event"])
    # the news chosen at publication is not part of the facts compared
    assert UM.classify_revision(pub, build(news=[UM.NewsItem("2026-11-04T23:03:00Z", "X", "Power Integrations news", "https://e.com")]))[0] == "unchanged"


# ===================================================================================================================== backward compatibility
OWNER_EVENT = {"id": 7, "symbol": "OSIS", "company_name": "OSI Systems <Inc>", "event_type": "margin_expansion", "label": "Margin expansion", "strength": 90.0, "severity": "high",
               "score": 72.0, "fiscal_year": 2026, "fiscal_quarter": 2, "quarter_end": "2026-06-30", "available_at": "2026-08-05", "availability_basis": "earnings_calendar",
               "data_quality": "ok", "method_version": "inflection_v1", "explanation": ["Operating margin rose to 18.0% from 3.9% a year earlier (+1406 basis points)."],
               "key_metrics": [{"label": "Operating margin", "current": 0.18, "previous": 0.039, "unit": "pct"},
                               {"label": "Change (bps)", "current": 1406.0, "previous": None, "unit": "bps"}],
               "reaction_2d": 0.042, "excess_vs_sector_since_publication": -0.011, "valuation": {"ps_ratio": 3.2, "ps_percentile_own_history": 0.35}}


def test_current_owner_alert_text_is_unchanged():
    assert A.telegram_text(OWNER_EVENT) == (
        "<b>RESEARCH SIGNAL</b> · not an order recommendation\n<b>OSIS</b> OSI Systems &lt;Inc&gt;\nMargin expansion · high (strength 90, score 72)\n"
        "• Operating margin: 18.0% (was 3.9%)\n• Change (bps): +1406 bps\n"
        "Why it may matter: Operating margin rose to 18.0% from 3.9% a year earlier (+1406 basis points).\n"
        "FY2026 Q2 · public 2026-08-05, date from earnings report date\n"
        "Price: +4.2% around the report, -1.1% vs its sector since · P/S 3.2 (35th percentile of its history)\n"
        "https://dashboard.first-light.finance/stock/OSIS")


def test_the_live_alert_path_does_not_use_the_new_formatter():
    src = open(os.path.join(ROOT, "mechanism", "inflection", "alerts.py"), encoding="utf-8").read()
    assert "update_format" not in src and "update_model" not in src and "update_card" not in src
    assert "sendPhoto" not in src and "send_photo" not in src


# ===================================================================================================================== preview CLI
def test_preview_writes_files_and_sends_nothing(tmp_path):
    from inflection import update_preview as P
    fixture = {"events": [ev(), ev("improvement_with_limited_repricing", symbol="CLF")], "companies": {"POWI": "Power Integrations, Inc.", "CLF": "Cleveland-Cliffs Inc."},
               "ciks": {"POWI": 833640}, "profile": {"POWI": PROFILE}}
    fixture["events"][0]["accession"] = "0000833640-26-000156"
    news = [dict(news_item("Power Integrations, Inc. Reports Q3 2026 Financial Results"), query_symbol="POWI")]
    rows = P.run(fixture, news, str(tmp_path))
    assert [r["status"] for r in rows] == ["ok", "not publishable: event_type_not_public"]
    folder = tmp_path / "POWI_margin_expansion"
    assert sorted(os.listdir(folder)) == ["buttons.json", "caption.html", "caption.txt", "card.png", "update.json", "v1.txt"]
    assert json.loads((folder / "update.json").read_text(encoding="utf-8"))["key"] == "POWI/2026-09-30/margin-expansion"
    assert (tmp_path / "index.html").exists() and rows[0]["caption_chars"] <= CAPTION_MAX
