"""model_version semantics: the version names the model that actually scored the signal, otherwise None.
Pure unit tests, no database."""
import math
import os
import sys

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "mechanism"))

from shared.model_provenance import is_scored, scoring_model_version  # noqa: E402


def scored(**kw):
    s = {"ml_prediction_available": True, "ml_momentum_probability": 61.0, "ml_model_version": "momentum_v1"}
    s.update(kw)
    return s


def test_a_scored_signal_carries_its_models_version():
    assert scoring_model_version(scored()) == "momentum_v1"


def test_version_is_stripped():
    assert scoring_model_version(scored(ml_model_version="  momentum_v1 ")) == "momentum_v1"


@pytest.mark.parametrize("signal", [
    {},                                                                        # key missing (the _unavailable() shape)
    {"ml_prediction_available": False, "ml_model_version": "momentum_v1"},     # model loaded, signal not scored
    {"ml_prediction_available": False, "ml_momentum_probability": None, "ml_model_version": "momentum_v1"},
    scored(ml_momentum_probability=None),                                     # flagged available but no score
    scored(ml_momentum_probability=float("nan")),
    scored(ml_momentum_probability="n/a"),
    scored(ml_model_version=None),
    scored(ml_model_version=""),
    scored(ml_model_version="   "),
    scored(ml_model_version=123),
])
def test_unscored_or_unversioned_is_none(signal):
    assert scoring_model_version(signal) is None


@pytest.mark.parametrize("placeholder", ["unknown", "UNKNOWN", "None", "null", "n/a", "not_loaded", "Not Loaded", "nan"])
def test_placeholder_strings_are_never_a_version(placeholder):
    assert scoring_model_version(scored(ml_model_version=placeholder)) is None


def test_a_probability_of_zero_is_still_a_score():
    assert is_scored(scored(ml_momentum_probability=0.0)) is True
    assert scoring_model_version(scored(ml_momentum_probability=0)) == "momentum_v1"


def test_bool_probability_is_not_a_score():
    assert is_scored(scored(ml_momentum_probability=True)) is False


def test_inf_probability_is_not_a_score():
    assert not is_scored(scored(ml_momentum_probability=math.inf))
