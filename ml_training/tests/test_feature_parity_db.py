"""Train/serve parity on REAL data (needs the Postgres DB; skipped when unreachable).

For a random sample of rows stored in ml_breakout_dataset_v2 (features computed by the dataset builder
over each symbol's full history), recompute the features exactly the way the live enhancer does --
`CombinedDonchianScreener._load_price_window()` (last ~323 bars up to the breakout date) followed by the
shared feature module -- and require them to match. This is the check that would have caught the old
bollinger_position / turnaround_volume / piotroski train-vs-serve skew.

Run:  python -m pytest ml_training/tests/test_feature_parity_db.py -q
"""
import json
import os
import sys
import warnings

import numpy as np
import pandas as pd
import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "ml_training", "config"))
sys.path.insert(0, os.path.join(ROOT, "mechanism"))
warnings.filterwarnings("ignore")

psycopg2 = pytest.importorskip("psycopg2")
from ml_training.features import price_features as pf  # noqa: E402


def _conn():
    try:
        from ml_config import ml_config
        return psycopg2.connect(**ml_config.db_config)
    except Exception as e:  # pragma: no cover
        pytest.skip(f"database not reachable: {e}")


def test_inference_path_reproduces_stored_training_features():
    conn = _conn()
    cur = conn.cursor()
    cur.execute("""SELECT symbol, date, direction, features FROM ml_breakout_dataset_v2
                   WHERE feature_set_version = %s AND date >= '2020-01-01'
                   ORDER BY md5(symbol || date::text) LIMIT 150""", (pf.FEATURE_SET_VERSION,))
    rows = cur.fetchall()
    if not rows:
        pytest.skip("ml_breakout_dataset_v2 is empty -- run build_dataset.py")

    from ml_enhancement.ml_signal_enhancer import CombinedDonchianScreener
    enh = CombinedDonchianScreener()

    worst = {}
    for symbol, date, direction, stored in rows:
        stored = stored if isinstance(stored, dict) else json.loads(stored)
        px = enh._load_price_window(symbol, date)
        assert px["date"].iloc[-1] == pd.Timestamp(date), f"{symbol}: window does not end at {date}"
        ind = pf.compute_indicators(px)
        assert pf.detect_breakouts(ind)[-1] == direction, f"{symbol} {date}: inference path does not see the breakout"
        sector = next((k[len('sector_'):] for k, v in stored.items() if k.startswith('sector_') and v == 1.0), None)
        sector = next((s for s in pf.SECTORS if s.lower().replace(' ', '_') == sector), None)
        got = pf.build_breakout_features(ind, np.array([len(px) - 1]), np.array([direction]), sector).iloc[0]
        for name in pf.FEATURE_NAMES:
            a, b = got[name], stored[name]
            if b is None:
                assert pd.isna(a), f"{symbol} {date} {name}: stored NaN, inference {a}"
                continue
            assert not pd.isna(a), f"{symbol} {date} {name}: stored {b}, inference NaN"
            err = abs(a - b) / max(1.0, abs(b))
            worst[name] = max(worst.get(name, 0.0), err)
            assert err < 1e-6, f"{symbol} {date} {name}: stored {b} vs inference {a}"
    assert worst, 'no features were compared'
