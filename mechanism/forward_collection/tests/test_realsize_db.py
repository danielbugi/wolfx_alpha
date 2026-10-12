"""Real size: the 1,100-stock universe against the writers' REAL floors (nothing scaled down), over enough sessions that the first (5-session) labels mature."""
import forward_world as FW
from forward_collection import contract as C

K = 10
N_SESSIONS = 8


def test_real_universe_and_real_floors_collect_every_session_and_the_first_labels_mature():
    gen = FW.make_world(n_days=N_SESSIONS, n_stocks=FW.N_STOCKS)
    w = next(gen)
    try:
        strat, steps = FW.start_world(w)
        reports = FW.run_forward_sessions(w, steps, range(N_SESSIONS), n_candidates=K, strategy=strat)
        assert [r.verdict for r in reports] == [C.COMPLETE] * N_SESSIONS
        assert w.count("universe_snapshot", "provenance = 'observed'") == N_SESSIONS
        assert w.count("market_snapshot", "provenance = 'observed'") == N_SESSIONS
        assert w.count("stock_relative_strength") >= N_SESSIONS * 1000
        horizon5 = w.count("forward_return_label", "horizon_sessions = 5")
        assert horizon5 == K * (N_SESSIONS - 5)        # sessions 0..2 are the only ones whose 5-session horizon has matured
        assert w.count("forward_return_label", "horizon_sessions = 20") == 0
    finally:
        gen.close()
