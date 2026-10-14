"""Forward paper tracker: replaying histories day by day reproduces the batch experiment exactly (same exit code), variants keep independent state, the ledger's one-open-position rule holds per variant,
sessions are explicit and idempotent, state round-trips, and the module cannot write anything anywhere. Pure; no database."""
import ast
import json
import re
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pytest

from research import experiment_runner as E
from research import paper_tracker as P

D0 = date(2021, 3, 1)
VARIANTS = E.registered_variants()
HIST_H = [100.0] * 10
HIST_L = [99.6] * 10


def sessions(n):
    out, d = [], D0
    while len(out) < n:
        if d.isoweekday() < 6:
            out.append(d)
        d += timedelta(days=1)
    return out


def random_series(rng, n, gappy=True):
    o = np.empty(n)
    h = np.empty(n)
    l = np.empty(n)
    c = np.empty(n)
    price = 100.0 * float(np.exp(rng.normal(0, 0.1)))
    for i in range(n):
        o[i] = price * (1 + rng.normal(0, 0.012 if gappy else 0.002))
        c[i] = o[i] * (1 + rng.normal(0.0008, 0.016))
        h[i] = max(o[i], c[i]) * (1 + abs(rng.normal(0, 0.008)))
        l[i] = min(o[i], c[i]) * (1 - abs(rng.normal(0, 0.008)))
        price = c[i]
    return o, h, l, c


def universe(seed, n_sym=6, n=320):
    rng = np.random.default_rng(seed)
    data, signals = {}, {}
    for k in range(n_sym):
        sym = f"S{k}"
        data[sym] = random_series(rng, n)
        idx = sorted(set(int(x) for x in rng.integers(25, n - 2, 18)) | {int(x) + 1 for x in rng.integers(25, n - 3, 4)})   # includes back-to-back signals
        signals[sym] = [(i, int(rng.choice([1, -1]))) for i in idx]
    return data, signals


def feed(book, data, signals, days, lo, hi):
    by_day = {}
    for sym, sg in signals.items():
        for i, d in sg:
            by_day.setdefault(i, []).append((sym, d))
    for i in range(lo, hi):
        book.on_session(days[i], {s: tuple(float(a[i]) for a in arr) for s, arr in data.items()})
        for sym, d in by_day.get(i, []):
            _, h, l, _ = data[sym]
            book.register_signal(sym, d, days[i], 1.5, h[max(0, i - 10):i + 1], l[max(0, i - 10):i + 1])
    return book


def replay(variants, data, signals, days, hold_cap=None):
    return feed(P.PaperBook(variants, hold_cap=hold_cap), data, signals, days, 0, len(days))


def batch(variants, data, signals, days, cap=60):
    out = {}
    for sym, arr in data.items():
        o, h, l, c = arr
        _, pos = E.simulate_symbol(variants, [cap], sym, o, h, l, c, days, np.full(len(c), 1.5), signals[sym])
        for p in pos:
            out[(p.variant.key, sym, p.direction, p.signal_date)] = p
    return out


def key(ob):
    return (ob.variant_key, ob.symbol, ob.direction, ob.signal_date)


def flat(price=100.0):
    return (price, price + 0.4, price - 0.4, price)


# ---------------------------------------------------------------------------------------------------------------------------------- equivalence with the batch experiment
@pytest.mark.parametrize("seed", [1, 2, 3, 4])
def test_day_by_day_replay_reproduces_the_batch_experiment_exactly(seed):
    days = sessions(320)
    data, signals = universe(seed)
    book = replay(VARIANTS, data, signals, days)
    ref = batch(VARIANTS, data, signals, days)
    closed = {key(o): o for o in book.closed_observations() if o.trade is not None}
    still_open = {key(o) for o in book.open_observations() if o.status == P.OPEN}
    done = {k for k, p in ref.items() if not p.censored}
    cens = {k for k, p in ref.items() if p.censored}
    assert set(closed) == done and still_open == cens and len(ref) > 100
    for k, ob in closed.items():
        p, t = ref[k], ob.trade
        assert (ob.entry_date, ob.exit_date) == (p.entry_date, p.exit_date)
        assert (t.exit_price, t.reason, t.duration, t.r_gross, t.mfe_r, t.mae_r, t.initial_stop, t.initial_risk) == \
               (p.trade.exit_price, p.trade.reason, p.trade.duration, p.trade.r_gross, p.trade.mfe_r, p.trade.mae_r, p.trade.initial_stop, p.trade.initial_risk)


def test_a_longer_hold_cap_replays_identically_and_is_flagged_as_not_the_registered_definition():
    days = sessions(320)
    data, signals = universe(7, n_sym=3)
    book = replay(VARIANTS, data, signals, days, hold_cap=120)
    ref = batch(VARIANTS, data, signals, days, cap=120)
    closed = {key(o): o for o in book.closed_observations() if o.trade is not None}
    assert set(closed) == {k for k, p in ref.items() if not p.censored} and closed
    assert all(not o.registered_cap and o.hold_cap == 120 for o in book.closed_observations())
    assert all(o.registered_cap for o in replay(VARIANTS, data, signals, days).closed_observations())


# ---------------------------------------------------------------------------------------------------------------------------------- independence
def test_each_variants_observations_do_not_depend_on_which_variants_are_tracked():
    days = sessions(320)
    data, signals = universe(11)
    together = replay(VARIANTS, data, signals, days)
    for v in VARIANTS:
        alone = replay([v], data, signals, days)
        mine = [o for o in together.closed_observations() if o.variant_key == v.key]
        assert [(key(o), o.entry_date, o.exit_date, o.trade) for o in alone.closed_observations()] == [(key(o), o.entry_date, o.exit_date, o.trade) for o in mine]
        assert sorted(key(o) for o in alone.open_observations()) == sorted(key(o) for o in together.open_observations() if o.variant_key == v.key)


def test_the_observations_of_different_variants_never_share_mutable_state():
    book = P.PaperBook(VARIANTS)
    book.on_session(D0, {"X": flat()})
    book.register_signal("X", 1, D0, 1.5, HIST_H, HIST_L)
    obs = book.open_observations()
    assert len(obs) == 5 and len({id(x.pending_hist_h) for x in obs}) == 5 and len({id(x.pending_hist_l) for x in obs}) == 5
    HIST_H.append(999.0)                                                                  # mutating the caller's list afterwards cannot reach any observation
    assert all(999.0 not in x.pending_hist_h for x in obs)
    HIST_H.pop()


# ---------------------------------------------------------------------------------------------------------------------------------- the one-open-position rule
def test_one_open_observation_per_variant_symbol_direction_and_the_two_directions_are_independent():
    days = sessions(10)
    book = P.PaperBook(VARIANTS[:2])
    book.on_session(days[0], {"A": flat()})
    assert set(book.register_signal("A", 1, days[0], 1.0, HIST_H, HIST_L).values()) == {"registered"}
    book.on_session(days[1], {"A": flat()})
    assert set(book.register_signal("A", 1, days[1], 1.0, HIST_H, HIST_L).values()) == {"skipped_open_position"}
    assert set(book.register_signal("A", -1, days[1], 1.0, HIST_H, HIST_L).values()) == {"registered"}


def test_a_signal_on_the_session_the_position_closed_is_skipped_and_the_next_session_is_accepted():
    days = sessions(12)
    book = P.PaperBook([next(v for v in VARIANTS if v.policy_name == "A_atr_stop_fixed_3R")])
    book.on_session(days[0], {"A": flat()})
    book.register_signal("A", 1, days[0], 1.0, HIST_H, HIST_L)
    book.on_session(days[1], {"A": flat()})
    book.on_session(days[2], {"A": (100.0, 100.5, 90.0, 91.0)})                         # the stop (98) is hit on session 2
    assert [o.exit_reason for o in book.closed_observations()] == ["stop"] and book.open_observations() == []
    assert set(book.register_signal("A", 1, days[2], 1.0, HIST_H, HIST_L).values()) == {"skipped_open_position"}
    book.on_session(days[3], {"A": flat(91.0)})
    assert set(book.register_signal("A", 1, days[3], 1.0, [91.0] * 10, [90.6] * 10).values()) == {"registered"}


def test_a_signal_that_cannot_be_entered_is_closed_without_blocking_later_ones():
    days = sessions(6)
    book = P.PaperBook([VARIANTS[0]])
    book.on_session(days[0], {"A": flat()})
    book.register_signal("A", 1, days[0], float("nan"), HIST_H, HIST_L)                  # unusable ATR
    book.on_session(days[1], {"A": flat()})
    assert [o.exit_reason for o in book.closed_observations()] == ["not_enterable"] and book.closed_observations()[0].trade is None
    assert set(book.register_signal("A", 1, days[1], 1.0, HIST_H, HIST_L).values()) == {"registered"}


# ---------------------------------------------------------------------------------------------------------------------------------- explicit, idempotent sessions
def test_sessions_must_increase_and_a_repeated_session_is_a_no_op():
    days = sessions(5)
    book = P.PaperBook(VARIANTS)
    book.on_session(days[0], {"A": flat()})
    book.register_signal("A", 1, days[0], 1.0, HIST_H, HIST_L)
    h0 = book.state_hash()
    assert book.on_session(days[0], {"A": (1.0, 2.0, 0.5, 1.5)}) == [] and book.state_hash() == h0       # same session again, even with different data: ignored
    with pytest.raises(P.TrackerError):
        book.on_session(days[0] - timedelta(days=3), {})


def test_a_signal_must_be_registered_for_the_session_just_processed():
    days = sessions(5)
    book = P.PaperBook(VARIANTS)
    book.on_session(days[1], {"A": flat()})
    with pytest.raises(P.TrackerError):
        book.register_signal("A", 1, days[0], 1.0, HIST_H, HIST_L)
    with pytest.raises(P.TrackerError):
        book.register_signal("A", 1, days[2], 1.0, HIST_H, HIST_L)


def test_a_missing_bar_leaves_an_open_position_unchanged_and_is_counted():
    days = sessions(8)
    book = P.PaperBook([next(x for x in VARIANTS if x.policy_name == "C_atr_trail_3x")])
    book.on_session(days[0], {"A": flat()})
    book.register_signal("A", 1, days[0], 1.0, HIST_H, HIST_L)
    book.on_session(days[1], {"A": flat()})
    held = book.open_observations()[0].position.bars_held
    book.on_session(days[2], {})                                                          # the symbol did not trade
    ob = book.open_observations()[0]
    assert ob.stale_sessions == 1 and ob.position.bars_held == held
    book.on_session(days[3], {"A": flat()})
    assert book.open_observations()[0].position.bars_held == held + 1


def test_a_pending_entry_waits_for_the_symbols_next_bar():
    days = sessions(6)
    book = P.PaperBook([VARIANTS[0]])
    book.on_session(days[0], {"A": flat()})
    book.register_signal("A", 1, days[0], 1.0, HIST_H, HIST_L)
    book.on_session(days[1], {})
    assert book.open_observations()[0].status == P.PENDING
    book.on_session(days[2], {"A": flat(101.0)})
    ob = book.open_observations()[0]
    assert ob.status == P.OPEN and ob.entry_date == days[2] and ob.position.entry_price == 101.0


# ---------------------------------------------------------------------------------------------------------------------------------- persistence
def test_the_state_round_trips_and_continues_identically():
    days = sessions(320)
    data, signals = universe(5, n_sym=3)
    cut = 140
    first = feed(P.PaperBook(VARIANTS), data, signals, days, 0, cut)
    blob = json.dumps(first.to_state(), allow_nan=False)
    restored = P.PaperBook.from_state(json.loads(blob))
    assert restored.state_hash() == first.state_hash() and restored.open_observations()
    for book in (first, restored):
        feed(book, data, {s: [x for x in sg if x[0] >= cut] for s, sg in signals.items()}, days, cut, 320)
    assert first.state_hash() == restored.state_hash() and first.closed_observations()


def test_an_unknown_state_schema_is_refused():
    with pytest.raises(P.TrackerError):
        P.PaperBook.from_state({"schema": "other"})


# ---------------------------------------------------------------------------------------------------------------------------------- it cannot write anything
def test_the_tracker_has_no_io_and_touches_no_ledger_database_or_clock():
    src = Path(P.__file__).read_text(encoding="utf-8")
    code = re.sub(r'""".*?"""', "", src, flags=re.S)
    code = "\n".join(l for l in code.splitlines() if not l.lstrip().startswith("#"))
    for forbidden in ("psycopg2", "requests", "urllib", "socket", "subprocess", "os.environ", "datetime.now", "date.today", "time.time", "open(", "INSERT", "UPDATE ", "DELETE ", "signal_ledger", "telegram"):
        assert forbidden not in code, forbidden
    tree = ast.parse(src)
    imports = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports |= {a.name for a in node.names}
        elif isinstance(node, ast.ImportFrom):
            imports |= {f"{node.module}.{a.name}" if node.module == "research" else node.module for a in node.names}
    assert imports <= {"__future__", "dataclasses", "hashlib", "json", "math", "datetime", "typing", "research.exit_policies", "research.experiment_runner"}, imports
