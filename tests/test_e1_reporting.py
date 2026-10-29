"""Experiment-only discrepancy and unique-trip coverage bookkeeping; no held-out data."""
from __future__ import annotations

import numpy as np
import pytest

from experiments.e1_learning_curve import extra_relative_variance, summarise_predictions, wilson


def test_discrepancy_subtracts_existing_variance_and_never_goes_negative() -> None:
    assert extra_relative_variance([{"observed": 13, "predicted": 10, "predictive_variance": 1}]) == pytest.approx(0.08)
    assert extra_relative_variance([{"observed": 10, "predicted": 10, "predictive_variance": 1}]) == 0.0


def test_wilson_boundaries_and_invalid_counts() -> None:
    assert wilson(0, 10) == pytest.approx([0, 0.27754], abs=1e-5)
    assert wilson(10, 10) == pytest.approx([0.72246, 1], abs=1e-5)
    with pytest.raises(ValueError):
        wilson(1, 0)


def test_repeated_subsets_do_not_inflate_wilson_sample_size() -> None:
    draws = np.array([np.linspace(-1, 1, 100), np.linspace(9, 11, 100)])
    result = summarise_predictions([draws] * 20, np.array([0, 20]), [np.array([0, 10])] * 20)
    assert result["n_predictions"] == 40 and result["unique_test_trips"] == 2
    assert result["mae_pp"] == 5
    for band in result["bands"].values():
        assert band["n"] == 2 and band["covered"] == 1
        assert band["wilson95"] == wilson(1, 2)
        assert len(band["per_subset"]) == 20
