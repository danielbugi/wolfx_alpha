"""The Market Environment channel post (Market Intelligence, built but NOT enabled): neutral headings, observed-only, exact-session, honest about
missing data, and wired into nothing that sends by itself."""
import os
import re
import sys
from datetime import date

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "mechanism"))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "mechanism", "market_intelligence", "tests"))

import mi_samples as S  # noqa: E402,F401  (path setup + a real regime record)
import tg_html  # noqa: E402
from alerts import channel_content as cx  # noqa: E402
from alerts import channel_control as cc  # noqa: E402
from alerts import channel_posts as cp  # noqa: E402
from market_intelligence import payload as P  # noqa: E402
from market_intelligence import risk_regime as RR  # noqa: E402

BANNED = re.compile(r"\b(buy\w*|sell\w*|targets?|stops?|entry|entries|recommend\w*|signals?|opportunit\w*|picks?|should|profit\w*|"
                    r"winners?|guarantee\w*|alpha|returns?|beat\w*|outperform\w*|earn\w*|money)\b", re.I)
SESSION = date(2026, 9, 30)


def market_row(scores=None, provenance="observed", session=SESSION, spx=(0.2, 1.1, 3.0), med=(0.1, 0.9, None)):
    rec = S.regime(scores).to_record()
    row = {"session_date": session.isoformat(), "provenance": provenance, "feature_set_version": "mi_v1",
           "regime_model_version": rec["model_version"], "regime_state": rec["state"], "regime_score": rec["score"],
           "regime_strength": rec["strength"], "regime_strength_label": rec["strength_label"], "regime_agreement": rec["agreement"],
           "regime_present_weight": rec["present_weight"], "regime_components": rec["components"], "regime_reasons": rec["reasons"],
           "sector_pit_safe": provenance == "observed", "n_symbols": 1200, "n_classified": 1190, "coverage": {}}
    for h, v in zip((5, 20, 60), spx):
        row[f"spx_ret_{h}"] = v
    for h, v in zip((5, 20, 60), med):
        row[f"univ_ret_{h}"], row[f"univ_n_valid_{h}"] = v, 1200
    return row


def sector_rows(rets, provenance="observed", session=SESSION):
    rows = []
    for i, (name, ret) in enumerate(rets, 1):
        row = {"provenance": provenance, "session_date": session.isoformat(), "sector": name, "n_members": 30, "rank_20": i if ret is not None else None}
        for h in (5, 20, 60):
            row.update({f"n_valid_{h}": 30, f"n_excluded_{h}": 0, f"sec_ret_{h}": ret if h == 20 else None})
        rows.append(row)
    return rows


SECTORS = [("Technology", 4.2), ("Energy", 2.0), ("Industrials", 0.4), ("Utilities", -0.3), ("Healthcare", -1.5), ("Financials", -3.1)]


def intel(**kw):
    sectors = kw.pop("sectors", SECTORS)
    return P.build(market_row(**kw), sector_rows(sectors, kw.get("provenance", "observed"), kw.get("session", SESSION)))


def ctx(**kw):
    return cx.Ctx(session=kw.pop("session", SESSION), **kw)


def test_a_positive_environment_reads_neutrally_and_passes_every_channel_check():
    post = cx.build_post("market_environment", ctx(intel=intel()))
    assert post.kind == "market_environment" and post.image is None and post.button is False
    plain = re.sub(r"<[^>]+>", "", post.text).replace("&amp;", "&")
    assert tg_html.problems(post.text, 4096) == []
    assert BANNED.findall(plain) == []
    assert not re.search(r"risk[- ]?(on|off)|regime|bull|bear", plain, re.I)               # the model's state names stay in the API / dashboard
    assert "Market environment: Broadly positive" in plain and "7 measures, fixed rules" in plain
    assert "S&P 500: 5 sessions +0.2% · 20 sessions +1.1% · 60 sessions +3.0%" in plain
    assert "Median stock: 5 sessions +0.1% · 20 sessions +0.9%" in plain and "60 sessions" not in plain.split("Median stock:")[1].split("\n")[0]
    assert "Leading: Technology +4.2% · Energy +2.0% · Industrials +0.4%" in plain
    assert "Lagging: Financials -3.1% · Healthcare -1.5% · Utilities -0.3%" in plain
    assert not re.search(r"investment advice|survivor bias|not a forecast|say nothing about", plain, re.I)
    assert plain.rstrip().endswith("Fixed rules, not tested as a predictor.")                        # the one caveat the pinned post has no room for


@pytest.mark.parametrize("scores,tilt", [({k: -0.6 for k in RR.WEIGHTS}, "Broadly negative"), ({k: 0.0 for k in RR.WEIGHTS}, "Mixed")])
def test_each_state_has_a_neutral_heading(scores, tilt):
    assert f"Market environment: {tilt}" in cx.build_post("market_environment", ctx(intel=intel(scores=scores))).text


@pytest.mark.parametrize("bad", [
    None,                                                                                   # not loaded / not provisioned
    P.unavailable("no_snapshot", "x"),
    intel(provenance="reconstructed"),                                                      # reconstructed is never posted
    intel(session=date(2026, 9, 29)),                                                       # another session's snapshot is never presented as today's
    intel(scores={}),                                                                       # regime could not be computed: no zero, no guessed state
], ids=["none", "unavailable", "reconstructed", "other_session", "regime_unavailable"])
def test_the_post_is_silent_instead_of_inventing_anything(bad):
    assert cx.post_market_environment(ctx(intel=bad)) is None


def test_missing_horizons_and_thin_sectors_are_left_out_not_zeroed():
    p = intel(spx=(None, 1.1, None), med=(None, None, None), sectors=[("Tiny", None)])
    text = cx.post_market_environment(ctx(intel=p)).text
    assert "S&amp;P 500: 20 sessions +1.1%" in text and "Median stock" not in text and "Sectors over" not in text and "5 sessions" not in text
    only_one = cx.post_market_environment(ctx(intel=intel(sectors=[("Technology", 4.2)]))).text
    assert "Sectors over" not in only_one                                                    # one ranked sector cannot be both leading and lagging
    two = cx.post_market_environment(ctx(intel=intel(sectors=SECTORS[:2]))).text
    assert "Leading: Technology +4.2%" in two and "Lagging: Energy +2.0%" in two


def test_a_zero_change_has_no_sign():
    assert "S&amp;P 500: 20 sessions 0.0%" in cx.post_market_environment(ctx(intel=intel(spx=(None, 0.0, None)))).text


# ---------------------------------------------------------------- registration, and that nothing sends it by itself
def test_registered_in_every_place_a_post_kind_must_be():
    assert "market_environment" in cx.KINDS and cx.BUILDERS["market_environment"] is cx.post_market_environment
    assert cp.NOTE_FOR_KIND["market_environment"] in {k for k, _t, _n in cp.SERVICE_NOTES}
    assert cc.KIND_LABELS["market_environment"] == "Market environment"
    assert cp.NOTE_FOR_KIND["market_environment"] == "market_health"                            # the pinned post has no room for a dedicated note yet


def test_it_is_not_enabled_anywhere_a_scheduler_or_the_rotation_could_reach():
    from alerts import publish_post_market as ppm
    assert "market_environment" not in ppm.POST_MARKET_KINDS
    for d in __import__("pandas").bdate_range("2026-01-01", periods=400):
        for news in (False, True):
            for sb in (False, True):
                assert "market_environment" not in cx.pick_kinds(d.date(), news, sb)
    assert "market_environment" not in cx.ROTATION.get(0, ()) + cx.ROTATION.get(4, ())


def test_loading_the_intel_is_opt_in_and_reads_the_exact_session_observed_only():
    try:
        from alerts import send_channel_posts as scp
    except Exception as ex:                                                                  # noqa: BLE001  (no database module in this environment)
        pytest.skip(f"send_channel_posts needs the shared database module: {ex}")
    import inspect
    assert inspect.signature(scp.load_context).parameters["with_intel"].default is False        # the live post-market path never loads it
    src = inspect.getsource(scp.load_market_intelligence)
    assert 'get_market_snapshot(cur, sess, "observed")' in src and 'get_sector_snapshots(cur, sess, "observed")' in src
    assert "market_environment" in scp.CLAIMABLE_KINDS                                           # a manual channel send still goes through the atomic claim


def test_the_post_market_package_does_not_load_market_intelligence():
    src = open(os.path.join(ROOT, "mechanism", "alerts", "publish_post_market.py"), encoding="utf-8").read()
    assert "market_intelligence" not in src and "with_intel" not in src
