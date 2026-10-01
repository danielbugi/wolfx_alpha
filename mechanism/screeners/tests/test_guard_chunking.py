"""Golden master: evaluating the universe guards in 250-symbol chunks gives exactly the decisions, ordering and
survivor list of the single-query form. No network, no database."""
import pytest

from test_universe_guards import SESSION, bars, mts, screener, sig  # noqa: F401


def shaped_universe(n):
    """n symbols cycling through every guard outcome: pass, short history, illiquid, split-like jump, no rows."""
    rows, symbols = [], []
    for i in range(n):
        sym = f"Z{i:04d}"
        symbols.append(sym)
        kind = i % 5
        if kind == 0:
            rows += bars(sym, 80)                                              # passes
        elif kind == 1:
            rows += bars(sym, 30)                                              # too little history
        elif kind == 2:
            rows += bars(sym, 80, price=1.0, volume=100.0)                     # illiquid
        elif kind == 3:
            rows += bars(sym, 80, overrides={70: {"close": 400.0, "high": 404.0, "low": 396.0}})  # discontinuity
        # kind 4: no rows at all
    return rows, symbols


def run_guards(mts, monkeypatch, rows, symbols, chunk):
    monkeypatch.setattr(mts, "GUARD_CHUNK", chunk)
    s, db = screener(mts, monkeypatch, rows)
    out = s._apply_universe_guards([sig(sym) for sym in symbols])
    return s.guard_decisions, [x["symbol"] for x in out], db.calls


def test_chunked_guards_equal_unchunked(mts, monkeypatch):
    rows, symbols = shaped_universe(613)                       # 3 chunks of 250 (last one short)
    whole, whole_out, whole_calls = run_guards(mts, monkeypatch, rows, symbols, 10 ** 9)
    chunked, chunked_out, chunked_calls = run_guards(mts, monkeypatch, rows, symbols, 250)
    assert len(whole_calls) == 1 and len(chunked_calls) == 3
    assert chunked == whole                                     # every verdict and every reason, identical
    assert list(chunked) == list(whole) == sorted(symbols)      # same ordering
    assert chunked_out == whole_out                             # same survivors in the same order
    assert {tuple(v) for v in whole.values()} > {()}            # the universe really exercised several outcomes
    assert len({tuple(v) for v in whole.values()}) >= 4


@pytest.mark.parametrize("chunk", [1, 2, 7, 250, 613, 614])
def test_every_chunk_size_gives_the_same_decisions(mts, monkeypatch, chunk):
    rows, symbols = shaped_universe(40)
    whole, whole_out, _ = run_guards(mts, monkeypatch, rows, symbols, 10 ** 9)
    got, got_out, calls = run_guards(mts, monkeypatch, rows, symbols, chunk)
    assert got == whole and got_out == whole_out and len(calls) == -(-40 // chunk)


def test_default_chunk_is_250(mts):
    assert mts.GUARD_CHUNK == 250
