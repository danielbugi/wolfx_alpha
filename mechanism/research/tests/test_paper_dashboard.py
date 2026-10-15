"""The forward tracker dashboard aggregation is PURE and reads back what the tracker stored. Hand-built fixtures with arithmetic that can be checked by eye."""
import copy
import json
import math
import re
from datetime import date, timedelta
from pathlib import Path

import pytest

from research import paper_dashboard as D

V2R = "donchian_breakout__exit_a_atr_stop_fixed_2r"
VB = "donchian_breakout__exit_b_donchian_trail_10"
ALL5 = [V2R, "donchian_breakout__exit_a_atr_stop_fixed_3r", VB, "donchian_breakout__exit_c_atr_trail_3x", "donchian_breakout__exit_d_hybrid_atr_stop_to_donchian_10"]
S1, S2 = date(2026, 10, 14), date(2026, 10, 15)


def sess(d, **kw):
    base = {"session_date": d, "scope": "triggered", "n_variants": 5, "n_candidates": 2, "n_registered": 10, "n_skipped_open": 0, "n_entered": 0, "n_closed": 0, "n_not_enterable": 0,
            "n_open_after": 10, "open_state_hash": "a" * 64, "code_ref": "45c2ac39249d"}
    base.update(kw)
    return base


def ctx(regime=True, rs=True, sector="Tech", passed=True, cat_reason="market_event tables: no event has been recorded yet (no job feeds this source)"):
    return {"decision_cutoff": "2026-10-15T08:53:00+03:00", "session": "2026-10-14", "candidate": {"passed_guard": passed},
            "features": {"sector": sector, "available_at": "2026-10-14T23:19:00+00:00"},
            "market_regime": {"available": True, "regime_state": "NEUTRAL"} if regime else {"available": False, "reason": "no observed market snapshot available at the cutoff"},
            "relative_strength": {"available": True} if rs else {"available": False, "reason": "no observed relative-strength row at the cutoff"},
            "catalysts": {"section": "unavailable", "events": [], "reason": cat_reason}}


class World:
    """A tiny forward world: observations with events, plus bars."""

    def __init__(self):
        self.obs, self.events, self.bars, self._id = [], [], {}, 0

    def add(self, variant, symbol, direction=1, signal=S1, atr=2.0, context=None, entered=None, closed=None, not_enterable=None):
        self._id += 1
        o = {"id": self._id, "variant_key": variant, "variant_version": "v1", "definition_hash": "h" * 64, "symbol": symbol, "direction": direction, "signal_session": signal, "atr": atr,
             "context": context if context is not None else ctx()}
        self.obs.append(o)
        if entered:
            self.events.append({"observation_id": o["id"], "event_type": "entered", "session_date": entered["entry_session"], "payload": dict(entered, unit=2 * atr, atr=atr)})
        if closed:
            self.events.append({"observation_id": o["id"], "event_type": "closed", "session_date": closed["exit_session"], "payload": closed})
        if not_enterable:
            self.events.append({"observation_id": o["id"], "event_type": "not_enterable", "session_date": S2, "payload": {"reason": "no_bar"}})
        return o

    def bar(self, symbol, rows):
        self.bars[symbol] = [(d, o, h, l, c) for d, o, h, l, c in rows]

    def payload(self, sessions=None, **kw):
        return D.build_forward_payload(sessions=sessions if sessions is not None else [sess(S1), sess(S2)], observations=self.obs, events=self.events, bars=self.bars, **kw)


def closed_payload(net10, *, gross=None, reason="stop", mfe=1.0, mae=0.5, bars=3, exit_session=S2, entry_session=S2, entry=100.0, exit_price=96.0, ret=-4.0, n0=None, n25=None):
    g = net10 + 0.1 if gross is None else gross
    return {"exit_session": exit_session.isoformat(), "entry_session": entry_session.isoformat(), "exit_price": exit_price, "reason": reason, "bars_held": bars, "entry_price": entry,
            "r_gross_std": g, "mfe_r_std": mfe, "mae_r_std": mae, "return_pct": ret, "stale_sessions": 0,
            "cost_model": {"primary_bps": 10.0, "round_trip_bps_charged_in_r": [0.0, 10.0, 25.0]},
            "r_net_std": {"0": g if n0 is None else n0, "10": net10, "25": net10 - 0.1 if n25 is None else n25}, "r_net_actual": {"0": g, "10": net10, "25": net10 - 0.1}}


def entered(entry=100.0, session=S2):
    return {"entry_session": session.isoformat(), "entry_price": entry, "initial_stop": entry - 4, "initial_risk": 4.0}


# ---------------------------------------------------------------------------------------------------------------------------------- shape, labels, emptiness
def test_no_processed_session_is_an_explicit_unavailable_answer_never_zeros():
    p = D.build_forward_payload(sessions=[], observations=[], events=[], bars={})
    assert p["available"] is False and p["sessions_processed"] == 0 and "no forward session" in p["reason"] and p["source"] == "forward" and "variants" not in p


def test_every_payload_is_labelled_forward_and_separates_replay():
    w = World()
    w.add(V2R, "AAA")
    p = w.payload()
    assert p["source"] == "forward" and p["schema"] == D.SCHEMA and "No order is placed" in p["banner"] and "never part of this payload" in p["definitions"]["replay_separation"]
    assert "dataset_replay" not in json.dumps(p)


def test_the_latest_session_and_counts_come_from_the_stored_session_rows():
    w = World()
    w.add(V2R, "AAA")
    p = w.payload(sessions=[sess(S2, n_entered=7, n_closed=2, n_registered=11), sess(S1, n_registered=10)])
    ls = p["latest_session"]
    assert ls["session_date"] == "2026-10-15" and ls["n_entered"] == 7 and ls["n_closed"] == 2 and ls["n_registered"] == 11 and ls["open_state_hash_prefix"] == "a" * 16
    assert p["since"] == "2026-10-14" and p["sessions_processed"] == 2


# ---------------------------------------------------------------------------------------------------------------------------------- position states, per variant, independence
def test_positions_are_classified_per_variant_independently():
    w = World()
    for v in ALL5:                                                                       # the same four signals followed by all five variants
        w.add(v, "PEND")
        w.add(v, "OPEN", entered=entered())
        w.add(v, "SHUT", entered=entered(), closed=closed_payload(-1.0))
        w.add(v, "NOBAR", not_enterable=True)
    p = w.payload()
    assert [v["variant_key"] for v in p["variants"]] == sorted(ALL5)
    for v in p["variants"]:
        assert v["positions"] == {"registered": 4, "pending_entry": 1, "open": 1, "closed": 1, "not_enterable": 1}
    # a variant that closes a position does not change another variant's counts
    w2 = copy.deepcopy(w)
    w2.add(VB, "EXTRA", entered=entered(), closed=closed_payload(2.0))
    p2 = w2.payload()
    by = {v["variant_key"]: v for v in p2["variants"]}
    assert by[VB]["positions"]["closed"] == 2 and all(by[k]["positions"]["closed"] == 1 for k in ALL5 if k != VB)


def test_unknown_event_observation_ids_are_ignored_not_attached_to_anything():
    w = World()
    w.add(V2R, "AAA", entered=entered())
    w.events.append({"observation_id": 9999, "event_type": "closed", "session_date": S2, "payload": closed_payload(5.0)})
    assert w.payload()["variants"][0]["closed"]["n"] == 0


# ---------------------------------------------------------------------------------------------------------------------------------- closed positions, realised net R, cost sensitivity
def test_realised_net_r_is_read_back_from_the_events_at_every_cost_with_exact_values():
    w = World()
    for i, net in enumerate([2.0, -1.0, -1.0, 0.5]):
        w.add(V2R, f"S{i}", entered=entered(), closed=closed_payload(net, gross=net + 0.1, n0=net + 0.1, n25=net - 0.2))
    c = w.payload()["variants"][0]["closed"]
    assert c["n"] == 4
    assert c["net_r_std"]["10"]["mean"] == pytest.approx(0.125) and c["net_r_std"]["10"]["median"] == pytest.approx(-0.25)
    assert c["net_r_std"]["0"]["mean"] == pytest.approx(0.225) and c["net_r_std"]["25"]["mean"] == pytest.approx(-0.075)
    assert c["net_r_std"]["10"]["win_rate"] == pytest.approx(0.5) and c["net_r_std"]["10"]["profit_factor"] == pytest.approx(2.5 / 2.0)
    assert c["net_r_std"]["0"]["mean"] > c["net_r_std"]["10"]["mean"] > c["net_r_std"]["25"]["mean"]            # more cost, lower R, exactly as recorded


def test_the_interval_is_withheld_below_30_closed_and_shown_from_30():
    few = World()
    for i in range(29):
        few.add(V2R, f"A{i}", entered=entered(), closed=closed_payload(1.0 if i % 2 else -1.0))
    c = few.payload()["variants"][0]
    assert c["closed"]["net_r_std"]["10"]["interval95"] is None and c["closed"]["net_r_std"]["10"]["interval_note"] == "n_too_small" and c["closed"]["n"] == 29
    assert few.payload()["comparison"][0]["status"] == "n_too_small"
    many = World()
    vals = [1.0 if i % 2 else -0.5 for i in range(40)]
    for i, v in enumerate(vals):
        many.add(V2R, f"B{i}", entered=entered(), closed=closed_payload(v))
    s = many.payload()["variants"][0]["closed"]["net_r_std"]["10"]
    mean = sum(vals) / 40
    sd = math.sqrt(sum((x - mean) ** 2 for x in vals) / 39)
    assert s["interval95"] == [pytest.approx(mean - 1.96 * sd / math.sqrt(40), abs=1e-3), pytest.approx(mean + 1.96 * sd / math.sqrt(40), abs=1e-3)] and s["interval_note"] is None
    assert many.payload()["comparison"][0]["status"] == "interval_shown"


def test_exit_reasons_bars_mfe_and_mae_are_aggregated_from_closed_events():
    w = World()
    w.add(V2R, "A", entered=entered(), closed=closed_payload(1.0, reason="target", mfe=3.0, mae=0.5, bars=5))
    w.add(V2R, "B", entered=entered(), closed=closed_payload(-1.0, reason="stop", mfe=0.5, mae=1.0, bars=1))
    w.add(V2R, "C", entered=entered(), closed=closed_payload(-1.0, reason="stop", mfe=1.0, mae=1.5, bars=3))
    c = w.payload()["variants"][0]["closed"]
    assert c["exit_reasons"] == {"stop": 2, "target": 1} and c["bars_held"]["mean"] == pytest.approx(3.0)
    assert c["mfe_r"]["mean"] == pytest.approx(1.5) and c["mae_r"]["mean"] == pytest.approx(1.0) and c["mfe_r"]["median"] == pytest.approx(1.0)


# ---------------------------------------------------------------------------------------------------------------------------------- open positions: marks, running MFE / MAE
def test_open_positions_are_marked_at_the_last_loaded_close_with_exact_r_and_cost():
    w = World()
    w.add(V2R, "LONG", direction=1, atr=2.0, entered=entered(100.0))                         # unit = 2 x 2.0 = 4.0
    w.bar("LONG", [(S2, 100.0, 109.0, 98.0, 106.0), (S2 + timedelta(days=1), 106.0, 110.0, 105.0, 108.0)])
    w.add(V2R, "SHORT", direction=-1, atr=2.0, entered=entered(100.0))
    w.bar("SHORT", [(S2, 100.0, 103.0, 94.0, 96.0)])
    o = w.payload()["variants"][0]["open"]
    assert o["n"] == 2 and o["marked"] == 2 and o["unmarked"] == 0
    long_gross, short_gross = (108.0 - 100.0) / 4.0, -(96.0 - 100.0) / 4.0                    # +2.0 and +1.0
    assert o["unrealised_gross_r"]["mean"] == pytest.approx((long_gross + short_gross) / 2)
    net = lambda g: g - 10 / 1e4 * 100.0 / 4.0
    assert o["unrealised_net_r_10bps"]["mean"] == pytest.approx((net(long_gross) + net(short_gross)) / 2, abs=1e-4)
    # running MFE / MAE from highs and lows since the entry session: long peak (110-100)/4 = 2.5, adverse (100-98)/4 = 0.5; short peak (100-94)/4 = 1.5, adverse (103-100)/4 = 0.75
    assert o["running_mfe_r"]["mean"] == pytest.approx((2.5 + 1.5) / 2) and o["running_mae_r"]["mean"] == pytest.approx((0.5 + 0.75) / 2)
    assert "not a result" in o["label"]


def test_an_open_position_with_no_bar_is_counted_unmarked_never_zero_filled():
    w = World()
    w.add(V2R, "GONE", entered=entered())
    p = w.payload()
    o = p["variants"][0]["open"]
    assert o["n"] == 1 and o["marked"] == 0 and o["unmarked"] == 1 and o["unrealised_gross_r"]["mean"] is None
    assert [h for h in p["health"] if h["name"] == "open_positions_marked"][0]["status"] == "warn"


# ---------------------------------------------------------------------------------------------------------------------------------- big winners and opportunity capture
def test_big_winner_tiers_capture_and_premature_exit_are_hindsight_and_labelled():
    w = World()
    # LONG entered 100, unit 4. Peak high 122 -> 5.5R. Closed at 108 (return +8%) on S2. Afterwards the peak high is 122 -> (122-108)/4 = 3.5R beyond the exit: premature (>= 2R).
    w.add(V2R, "WIN", entered=entered(100.0), closed=closed_payload(1.9, exit_session=S2, exit_price=108.0, ret=8.0))
    w.bar("WIN", [(S2, 100.0, 109.0, 99.0, 108.0), (S2 + timedelta(days=1), 108.0, 115.0, 107.0, 114.0), (S2 + timedelta(days=2), 114.0, 122.0, 113.0, 121.0)])
    # SMALL: peak only 1R, exits correctly, nothing afterwards
    w.add(V2R, "SMALL", entered=entered(100.0), closed=closed_payload(-0.5, exit_session=S2, exit_price=98.0, ret=-2.0))
    w.bar("SMALL", [(S2, 100.0, 104.0, 97.0, 98.0)])
    # OPEN runner: running peak 4.5R
    w.add(V2R, "RUN", entered=entered(100.0))
    w.bar("RUN", [(S2, 100.0, 118.0, 99.0, 117.0)])
    bw = w.payload()["variants"][0]["big_winner"]
    assert bw["decision_time_information"] is False and bw["window"] == "so_far" and bw["entered_positions"] == 3
    assert bw["peak_ge_3r"] == 2 and bw["peak_ge_5r"] == 1                                    # WIN 5.5R, RUN 4.5R, SMALL 1R
    assert bw["capture_ratio"]["n"] == 2
    assert bw["capture_ratio"]["median"] == pytest.approx((8.0 / 22.0 + -2.0 / 4.0) / 2, abs=1e-3)   # WIN 8% of a 22% peak; SMALL -2% of a 4% peak
    assert bw["continuation_after_exit_r"]["n"] == 1 and bw["continuation_after_exit_r"]["mean"] == pytest.approx(3.5)
    assert bw["premature_exit_share"] == pytest.approx(1.0) and bw["premature_exit_n"] == 1
    assert [t["symbol"] for t in bw["top_open_by_peak_r"]] == ["RUN"] and bw["top_open_by_peak_r"][0]["peak_favourable_r_so_far"] == pytest.approx(4.5)
    assert "hindsight" in bw["note"] and "not known" in bw["note"]


def test_every_mfe_mae_and_big_winner_number_states_its_observation_window():
    w = World()
    w.add(V2R, "WIN", entered=entered(100.0), closed=closed_payload(1.9, exit_session=S2, exit_price=108.0, ret=8.0))
    w.bar("WIN", [(S2, 100.0, 109.0, 99.0, 108.0), (S2 + timedelta(days=1), 108.0, 115.0, 107.0, 114.0), (S2 + timedelta(days=2), 114.0, 122.0, 113.0, 121.0)])
    w.add(V2R, "RUN", entered=entered(100.0))
    w.bar("RUN", [(S2, 100.0, 118.0, 99.0, 117.0)])
    v = w.payload()["variants"][0]
    assert "holding period" in v["closed"]["mfe_mae_window"] and "still running" in v["open"]["mfe_mae_window"]
    ow = v["big_winner"]["observation_window"]
    assert ow["bars_since_entry"]["n"] == 2 and ow["bars_since_entry"]["max"] == 3 and ow["bars_since_entry"]["mean"] == pytest.approx(2.0)      # WIN 3 bars, RUN 1 bar
    assert ow["bars_after_exit"]["n"] == 1 and ow["bars_after_exit"]["mean"] == pytest.approx(2.0) and ow["bars_after_exit"]["max"] == 2           # two bars after the exit bar
    assert "Short windows understate" in ow["note"]


def test_the_state_production_is_in_right_after_activation_renders_as_pending_only_with_nothing_invented():
    w = World()
    for v in ALL5:
        for i in range(3):
            w.add(v, f"P{i}")                                                            # registered, entry at the next open: nothing entered or closed yet
    p = w.payload(sessions=[sess(S1, n_registered=15, n_open_after=15)], as_of=S2)
    for v in p["variants"]:
        assert v["positions"] == {"registered": 3, "pending_entry": 3, "open": 0, "closed": 0, "not_enterable": 0}
        assert v["closed"]["n"] == 0 and v["closed"]["net_r_std"]["10"]["mean"] is None and v["open"]["n"] == 0 and v["open"]["unrealised_gross_r"]["mean"] is None
        assert v["big_winner"]["entered_positions"] == 0 and v["big_winner"]["peak_ge_5r"] == 0 and v["big_winner"]["capture_ratio"]["median"] is None
    assert all(c["closed"] == 0 and c["net_r_10bps_mean"] is None and c["status"] == "n_too_small" for c in p["comparison"])
    json.dumps(p, allow_nan=False)


def test_stored_hindsight_events_are_only_counted_never_used_for_the_open_window_numbers():
    w = World()
    o = w.add(V2R, "AAA", entered=entered(), closed=closed_payload(1.0))
    w.events.append({"observation_id": o["id"], "event_type": "hindsight", "session_date": S2, "payload": {"decision_time_information": False, "capture": {"capture_ratio": 0.9}}})
    p = w.payload()
    assert p["variants"][0]["big_winner"]["stored_hindsight_events"] == 1
    assert [h for h in p["health"] if h["name"] == "stored_hindsight_events"][0]["status"] == "info"


# ---------------------------------------------------------------------------------------------------------------------------------- variant comparison
def test_the_comparison_lists_every_variant_on_the_same_registered_candidates():
    w = World()
    for v in ALL5:
        w.add(v, "AAA", entered=entered(), closed=closed_payload(0.5 if v == VB else -0.5))
        w.add(v, "BBB", entered=entered())
        w.bar("BBB", [(S2, 100.0, 101.0, 99.0, 100.0)])
    p = w.payload()
    assert [c["name"] for c in p["comparison"]] == sorted(D.short_name(k) for k in ALL5)
    assert {c["registered"] for c in p["comparison"]} == {2} and {c["open"] for c in p["comparison"]} == {1} and {c["closed"] for c in p["comparison"]} == {1}
    by = {c["name"]: c for c in p["comparison"]}
    assert by["b_donchian_trail_10"]["net_r_10bps_mean"] == pytest.approx(0.5) and by["a_atr_stop_fixed_2r"]["net_r_10bps_mean"] == pytest.approx(-0.5)
    assert all(c["status"] == "n_too_small" for c in p["comparison"])


# ---------------------------------------------------------------------------------------------------------------------------------- completeness and data health
def test_completeness_counts_each_candidate_once_and_explains_every_gap():
    w = World()
    for v in ALL5:                                                                       # five variants share one context per (symbol, direction)
        w.add(v, "AAA", context=ctx())
        w.add(v, "BBB", context=ctx(regime=True, rs=False))
        w.add(v, "CCC", context=ctx(regime=False, rs=False, sector=None, passed=False))
    comp = w.payload()["completeness"]["sessions"][0]
    assert comp["session"] == "2026-10-14" and comp["candidates"] == 3
    assert comp["market_regime_attached"] == 2 and comp["relative_strength_attached"] == 1 and comp["sector_known"] == 2 and comp["guard_rejected"] == 1
    assert comp["catalysts_unavailable"] == 3 and comp["catalysts_with_events"] == 0
    assert comp["unavailable_reasons"]["relative_strength: no observed relative-strength row at the cutoff"] == 2
    assert comp["unavailable_reasons"]["market_regime: no observed market snapshot available at the cutoff"] == 1
    assert comp["unavailable_reasons"]["catalysts: market_event tables: no event has been recorded yet (no job feeds this source)"] == 3


def test_health_flags_low_coverage_gaps_and_unprocessed_captures():
    w = World()
    for i in range(10):
        w.add(V2R, f"S{i}", context=ctx(regime=(i < 5), rs=True))
    caps = [{"session_date": S1, "status": "complete"}, {"session_date": S2, "status": "complete"}, {"session_date": date(2026, 10, 16), "status": "complete"}]
    p = w.payload(sessions=[sess(S1)], capture_runs=caps, as_of=date(2026, 10, 19), trading_sessions=[S1, S2, date(2026, 10, 16)])
    h = {x["name"]: x for x in p["health"]}
    assert h["market_regime_coverage"]["status"] == "warn" and "50.0%" in h["market_regime_coverage"]["detail"]
    assert h["relative_strength_coverage"]["status"] == "ok"
    assert h["captured_sessions_processed"]["status"] == "warn" and "2026-10-15" in h["captured_sessions_processed"]["detail"]
    assert h["session_continuity"]["status"] == "ok"                                      # only the activation session is in range; later ones are the "waiting" warning above
    p2 = w.payload(sessions=[sess(S1), sess(date(2026, 10, 16))], trading_sessions=[S1, S2, date(2026, 10, 16)])
    assert {x["name"]: x for x in p2["health"]}["session_continuity"]["status"] == "warn" and "2026-10-15" in {x["name"]: x for x in p2["health"]}["session_continuity"]["detail"]
    assert h["catalyst_context"]["status"] == "info" and "no catalyst feed" in h["catalyst_context"]["detail"]


# ---------------------------------------------------------------------------------------------------------------------------------- purity
def _big_world():
    w = World()
    for v in ALL5:
        w.add(v, "AAA", entered=entered(), closed=closed_payload(-0.4, exit_price=97.0, ret=-3.0))
        w.add(v, "BBB", entered=entered())
        w.add(v, "CCC")
    w.bar("AAA", [(S2, 100.0, 102.0, 96.0, 97.0), (S2 + timedelta(days=1), 97.0, 103.0, 96.0, 102.0)])
    w.bar("BBB", [(S2, 100.0, 105.0, 99.0, 104.0)])
    return w


def test_the_payload_is_deterministic_json_safe_and_does_not_mutate_its_inputs():
    w = _big_world()
    before = copy.deepcopy((w.obs, w.events, w.bars))
    a, b = w.payload(as_of=date(2026, 10, 15)), w.payload(as_of=date(2026, 10, 15))
    assert json.dumps(a, sort_keys=True, allow_nan=False) == json.dumps(b, sort_keys=True, allow_nan=False)
    assert (w.obs, w.events, w.bars) == before
    assert not re.search(r"NaN|Infinity|undefined", json.dumps(a))


def test_the_module_has_no_io_clock_database_or_randomness():
    src = Path(D.__file__).read_text(encoding="utf-8")
    code = re.sub(r'""".*?"""', "", src, flags=re.S)
    for forbidden in ("psycopg2", "requests", "urllib", "socket", "subprocess", "os.environ", "datetime.now", "date.today", "time.time", "open(", "random", "INSERT", "UPDATE ", "DELETE ", "execute(", "cursor"):
        assert forbidden not in code, forbidden


def test_the_summary_stats_are_correct_on_known_numbers():
    s = D.summarise([1.0, 2.0, 3.0, -1.0])
    assert s["n"] == 4 and s["mean"] == pytest.approx(1.25) and s["median"] == pytest.approx(1.5) and s["win_rate"] == pytest.approx(0.75) and s["profit_factor"] == pytest.approx(6.0)
    assert D.summarise([]) == {"n": 0, "mean": None, "median": None, "win_rate": None, "profit_factor": None, "interval95": None, "interval_note": "no_data"}
    assert D.summarise([1.0, None, float("nan"), 3.0])["n"] == 2                           # missing values are dropped, never counted as zero
    assert D.summarise([1.0, 2.0])["profit_factor"] is None                                # no losses: undefined, not infinite
