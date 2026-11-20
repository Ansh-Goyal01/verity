"""WP5: honest bands. Split conformal calibration of the Monte Carlo's predictive spread on REAL trips, and a small
learned correction on top of physics (docs/FINALE_BUILD.md WP5, docs/DECISIONS.md WP5).

Score per calibration trip: ``|observed - median| / H90`` where H90 is the half-width of the model's OWN central 90 %
band for that trip (the 90th percentile of ``|sample - median|``). A perfectly calibrated model has a 90 % conformal
quantile ``k`` of about 1; ``k = 2`` means its 90 % band must be twice as wide to cover 90 % of trips like these.
Widening the Monte Carlo around its median by ``k`` therefore turns the model's 90 % band into the conformal band.
The guide's interquartile-range score is reported next to it (``k_iqr``) for reference; it is not the one applied,
because widening by an IQR-normalised quantile would mis-scale the band (a calibrated model would still get k ~ 1.2).

The guarantee is only "on average over trips like the calibration set" (exchangeability): never "90 % guaranteed".
Pure functions: no file or network access here; ``experiments/e7_honest_bands.py`` and ``scripts`` pass data in.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Sequence

import numpy as np

Array = np.ndarray

LEVEL = 0.90
MIN_SPREAD = 1e-6
SCHEMA = "verity.conformal.v1"
GUARANTEE_NOTE = ("Conformal coverage holds on average over trips like the calibration set (public, self-reported "
                  "Indian trips); it is not a guarantee for any single trip.")
# record types whose observable grows with energy use; a range test (km) shrinks when more energy is used
ENERGY_SIGN = {"soc_trip": 1.0, "consumption": 1.0, "range_test": -1.0}


def half_width(samples: Array, level: float = LEVEL) -> float:
    """Half-width of the samples' central ``level`` band around their median (symmetric, by |deviation|)."""
    s = np.asarray(samples, dtype=float)
    return float(np.quantile(np.abs(s - np.median(s)), level))


def score(samples: Array, observed: float, level: float = LEVEL) -> float:
    """Normalised residual: |observed - median| over the model's own ``level`` half-width."""
    s = np.asarray(samples, dtype=float)
    return abs(float(observed) - float(np.median(s))) / max(half_width(s, level), MIN_SPREAD)


def score_iqr(samples: Array, observed: float) -> float:
    """The guide's literal score: |observed - median| over the interquartile range (reported, not applied)."""
    s = np.asarray(samples, dtype=float)
    q25, q50, q75 = np.quantile(s, [0.25, 0.5, 0.75])
    return abs(float(observed) - float(q50)) / max(float(q75 - q25), MIN_SPREAD)


def conformal_quantile(scores: Sequence[float], level: float = LEVEL) -> float | None:
    """Split-conformal quantile: the ceil((n+1) * level)-th smallest score. None when n is too small for that rank
    to exist (n < level / (1 - level), i.e. fewer than 9 trips at 90 %): then there is no finite conformal band."""
    s = sorted(float(x) for x in scores)
    n = len(s)
    rank = math.ceil((n + 1) * level)
    if n == 0 or rank > n:
        return None
    return s[rank - 1]


def widen(samples: Array, k: float) -> Array:
    """Stretch samples around their median by ``k`` (k > 1 widens)."""
    s = np.asarray(samples, dtype=float)
    med = np.median(s, axis=0)
    return med + k * (s - med)


def applied_scale(k: float | None) -> float:
    """The factor releases use: never narrower than the physics model's own spread (k < 1 is reported, not applied)."""
    return 1.0 if k is None else max(1.0, float(k))


# ---------------------------------------------------------------- learned correction


def energy_log_ratio(record_type: str, observed: float, predicted_median: float) -> float | None:
    """Unit-free target: log(observed energy / predicted energy). For a range test the energy ratio is the inverse
    of the range ratio. None when either value is not positive."""
    if observed <= 0 or predicted_median <= 0:
        return None
    return ENERGY_SIGN[record_type] * math.log(observed / predicted_median)


def apply_correction(record_type: str, samples: Array, log_ratio: float) -> Array:
    """Physics + correction: scale the samples by the predicted energy ratio (inverse for range tests)."""
    return np.asarray(samples, dtype=float) * math.exp(ENERGY_SIGN[record_type] * float(log_ratio))


@dataclass(frozen=True)
class CalibrationRow:
    """One calibration trip: what the k is computed from, kept so k can be recomputed from the saved file."""

    record_id: str
    group: str
    record_type: str
    observed: float
    median: float
    h90: float
    score: float
    score_iqr: float

    def as_dict(self) -> dict:
        return {"record_id": self.record_id, "group": self.group, "record_type": self.record_type,
                "observed": round(self.observed, 4), "median": round(self.median, 4), "h90": round(self.h90, 4),
                "score": round(self.score, 6), "score_iqr": round(self.score_iqr, 6)}


def calibration_row(record_id: str, group: str, record_type: str, samples: Array, observed: float) -> CalibrationRow:
    s = np.asarray(samples, dtype=float)
    return CalibrationRow(record_id, group, record_type, float(observed), float(np.median(s)), half_width(s),
                          score(s, observed), score_iqr(s, observed))


def group_folds(groups: Sequence[str], n_folds: int) -> list[int]:
    """Fold index per row, whole source groups per fold (a source page is never split), deterministic."""
    order = sorted(set(groups))
    fold_of = {g: i % n_folds for i, g in enumerate(order)}
    return [fold_of[g] for g in groups]
