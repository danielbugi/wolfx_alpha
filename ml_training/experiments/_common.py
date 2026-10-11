"""Shared helpers for the offline research scripts (read-only database session, market panel from the price table, month-block bootstrap)."""
import os

import numpy as np
import pandas as pd
import psycopg2


def connect():
    return psycopg2.connect(host=os.environ["DB_HOST"], port=os.environ["DB_PORT"], dbname=os.environ["DB_NAME"], user=os.environ["DB_USER"], password=os.environ["DB_PASSWORD"],
                            options="-c default_transaction_read_only=on -c statement_timeout=1800000")


MARKET_SQL = """
WITH p AS (
  SELECT symbol, date, close,
         close / NULLIF(lag(close) OVER w, 0) - 1 AS ret,
         avg(close) OVER (PARTITION BY symbol ORDER BY date ROWS BETWEEN 49 PRECEDING AND CURRENT ROW) AS sma50,
         avg(close) OVER (PARTITION BY symbol ORDER BY date ROWS BETWEEN 199 PRECEDING AND CURRENT ROW) AS sma200,
         count(*) OVER (PARTITION BY symbol ORDER BY date ROWS BETWEEN 199 PRECEDING AND CURRENT ROW) AS n200
  FROM stock_prices WHERE date >= DATE '2016-09-01' AND close > 0
  WINDOW w AS (PARTITION BY symbol ORDER BY date))
SELECT date, count(*) AS n, avg(CASE WHEN ret BETWEEN -0.5 AND 0.5 THEN ret END) AS ew_ret,
       avg((close > sma50)::int) FILTER (WHERE n200 >= 50) AS b50, avg((close > sma200)::int) FILTER (WHERE n200 >= 200) AS b200
FROM p GROUP BY date HAVING count(*) > 1500 ORDER BY date"""


def market_panel(conn):
    """Equal-weight index, breadth and trend flags from the price table itself. Point-in-time: every value uses data up to and including its own date."""
    mk = pd.read_sql(MARKET_SQL, conn)
    mk["date"] = pd.to_datetime(mk["date"])
    mk = mk.set_index("date")
    mk["ew_ret"] = mk["ew_ret"].fillna(0.0)
    mk["idx"] = (1 + mk["ew_ret"]).cumprod()
    mk["idx_sma200"] = mk["idx"].rolling(200, min_periods=150).mean()
    mk["idx_above_200"] = (mk["idx"] > mk["idx_sma200"]).astype(float).where(mk["idx_sma200"].notna())
    return mk


def block_ci(values, months, n_boot=400, seed=7):
    """Mean with a month-block bootstrap 95% interval (observations in the same month are not independent)."""
    v = np.asarray(values, dtype=float)
    if len(v) == 0:
        return {"n": 0}
    df = pd.DataFrame({"v": v, "m": np.asarray(months)})
    blocks = [g["v"].to_numpy() for _, g in df.groupby("m")]
    rng = np.random.default_rng(seed)
    means = [np.concatenate([blocks[i] for i in rng.integers(0, len(blocks), len(blocks))]).mean() for _ in range(n_boot)]
    lo, hi = np.percentile(means, [2.5, 97.5])
    return {"n": int(len(v)), "mean": round(float(v.mean()), 5), "ci95": [round(float(lo), 5), round(float(hi), 5)]}
