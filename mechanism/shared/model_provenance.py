# mechanism/shared/model_provenance.py
"""
Single definition of "which model scored this signal" for every record that stores a model version
(`signal_ledger.model_version`, `candidate_observation.ml_model_version`).

Semantics: the value names the model that actually produced THIS signal's ML score. If no validated model scored
the signal -- no model loaded, the signal was not processed (near-breakout, illiquid, ...), the prediction failed,
or the model reported no version -- the answer is None (SQL NULL). It is never a placeholder such as 'unknown',
and a model that merely happened to be loaded while the signal went unscored is not credited with it.
"""
from __future__ import annotations

import math
from typing import Any, Mapping, Optional

# Strings that mean "no version was recorded"; treated as absence, never stored.
_NOT_A_VERSION = frozenset({"", "unknown", "none", "null", "n/a", "na", "nan", "not_loaded", "not loaded"})


def is_scored(signal: Mapping[str, Any]) -> bool:
    """True only for a real, available ML score (the same predicate the research observer uses for ml_status)."""
    if not signal.get("ml_prediction_available"):
        return False
    probability = signal.get("ml_momentum_probability")
    if probability is None or isinstance(probability, bool):
        return False
    try:
        return math.isfinite(float(probability))
    except (TypeError, ValueError):
        return False


def scoring_model_version(signal: Mapping[str, Any]) -> Optional[str]:
    """The model version that scored `signal`, or None when no validated model did."""
    if not is_scored(signal):
        return None
    version = signal.get("ml_model_version")
    if not isinstance(version, str):
        return None
    version = version.strip()
    return None if version.lower() in _NOT_A_VERSION else version
