"""Research observation layer (Release B / B1): immutable candidate observations and T0 feature snapshots.

Import rule (tested): nothing outside this package, the screener's default-off observer hook and the ledger
writer imports it, and nothing in here imports the Telegram, post-market or backend code. The observer is
an additive, fail-safe side channel -- it can never change what the screener, the ledger or the channel see.
"""
