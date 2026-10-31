"""Fundamental Inflection Engine: detects meaningful improvements in company fundamentals (profitability turnarounds, revenue acceleration, margin expansion, operating
leverage, free-cash-flow turnarounds) and how much of it the market has already priced in. Deterministic, explainable, point-in-time, versioned.

The score and the events are RESEARCH SIGNALS. Nothing here is labelled predictive; whether inflection events carry forward excess returns is a question the Quant Lab tests
(research.strategy_lab) and answers with its own verdict. Pure modules (config, features, events, context, pipeline) use the standard library only; `store` and `updater` touch the database.
"""
