"""Big Winner research harness (offline, read-only, research only).

Nothing here is imported by the pipeline, the tracker, the collector, the bot, a timer or a service. It reads production tables through a read-only session (or, in tests, synthetic data) and writes only files
chosen by its caller. No model is trained. Every number it produces about history is hypothesis-generation or replication evidence: the history has already been examined by earlier lift tables, so there is no
untouched historical holdout.

Modules
  config      one frozen configuration (and its hash): label definition, splits, matching, resampling, thresholds
  labels      the canonical Big Winner label (entry at the next open, 1R = 2 x ATR14, 3R / 5R / 10R, 20 / 60 / 120 bars), MFE kept apart from realised returns
  episodes    independent-episode identifiers for overlapping signals
  splits      chronological segments with embargo and purge
  provenance  the provenance class of every dataset column
  features    the decision-time features and the dataset builder (pure: takes price frames, returns a frame)
  matching    matched controls
  inference   cluster bootstrap, permutation test, Mantel-Haenszel within-date estimate, Holm
  hypotheses  the five pre-registered hypotheses, the negative control, the verdict rules
  survivorship  sensitivity of every effect to missing (delisted) companies
  posthoc     labelled, non-pre-registered sensitivity analyses (no verdict)
  io_read     read-only database access and file output with hashes
  forward     the read-only forward report: labels, coverage and maturity, never an incomplete horizon as a negative
  run         the command line entry point
"""
