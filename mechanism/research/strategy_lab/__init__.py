"""Strategy lab: one versioned strategy -> reproducible backtest -> out-of-sample validation with controls -> immutable run record -> automatic report.

Research only. Nothing here is imported by the pipeline, the backend or the live signal path, writes `strategies` / `signal_ledger`, or places anything.
Pure modules (`specs`, `engine`, `report`) use numpy + stdlib only; `data` reads a hashed extract from disk; `ledger` writes the append-only run record to a research database.
"""
