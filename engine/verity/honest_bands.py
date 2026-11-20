"""WP5 calibration set and learned correction on REAL Indian trips (training split only).

Everything here takes cases in and returns numbers; ``scripts/build_conformal.py`` writes data/processed/conformal.json
from the TRAINING split and ``experiments/e7_honest_bands.py`` scores the held-out split once per run.

Honesty rules (docs/FINALE_BUILD.md WP5, rule 4):
* the calibration set is training-split rows only; ``calibration_cases`` is the one place that selects them;
* every calibration prediction is OUT OF SAMPLE: a trip's posterior is fitted on training trips of the same car from
  OTHER source pages (leave-one-source-out), and the learned correction is cross-fitted by source group, so ``k`` is
  not shrunk by in-sample residuals;
* BMW i3 trips are not used (different predictive path, see docs/DECISIONS.md WP5), VED was dropped.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Sequence

import numpy as np

from . import conformal as cf
from . import india_eval as ie

Array = np.ndarray
RngFor = Callable[[str, str], np.random.Generator]  # (record_id, method) -> generator

N_FOLDS = 5
BAND_CODE = {"city": 0.0, "mixed": 1.0, "highway_80_100": 2.0, "highway_100_plus": 3.0}
TYPE_CODE = {"soc_trip": 0.0, "consumption": 1.0, "range_test": 2.0}
FEATURES = ("distance_km", "speed_band", "temp_c", "ac", "passengers", "battery_kwh", "record_type")
# Small on purpose: a few dozen self-reported trips. Shallow trees, strong shrinkage, a fixed seed.
HGB_PARAMS = {"max_iter": 60, "learning_rate": 0.05, "max_depth": 2, "min_samples_leaf": 8,
              "l2_regularization": 1.0, "random_state": 0}


def calibration_cases(cases: Sequence[ie.Case], split: dict[str, str]) -> list[ie.Case]:
    """THE calibration set: training-split cases with a usable distance. Held-out rows can never enter it."""
    return [c for c in cases if split.get(c.group) == "train" and not c.distance_flag]


def heldout_cases(cases: Sequence[ie.Case], split: dict[str, str]) -> list[ie.Case]:
    return [c for c in cases if split.get(c.group) == "test" and not c.distance_flag]


def posterior_for(case: ie.Case, train: Sequence[ie.Case]):
    """The car's posterior from training trips of OTHER source pages (the case's own page is never used for its own
    prediction), else the class prior: what the catalog would know about this car without this report."""
    pool = [c for c in train if c.vehicle_id == case.vehicle_id and c.group != case.group]
    return ie.calibrated_posterior(pool) or ie.prior_posterior(case)


def physics_samples(case: ie.Case, train: Sequence[ie.Case], rng_for: RngFor, n_runs: int) -> Array:
    return ie.predictive_samples(case, posterior_for(case, train), rng_for(case.record_id, "physics"), n_runs)


def features(case: ie.Case) -> Array:
    """Trip features for the correction (NaN = unknown; gradient boosting handles missing values natively)."""
    nan = float("nan")
    return np.array([case.distance_km if case.distance_km else nan, BAND_CODE.get(case.speed_band or "", nan),
                     nan if case.temp_c is None else float(case.temp_c), nan if case.ac is None else float(case.ac),
                     nan if case.passengers is None else float(case.passengers), case.nominal_kwh,
                     TYPE_CODE[case.record_type]])


@dataclass(frozen=True)
class Correction:
    """A fitted correction and the feature columns it uses (a column unknown for every training trip is dropped)."""

    model: object
    keep: Array  # bool mask over FEATURES

    @property
    def used_features(self) -> list[str]:
        return [f for f, k in zip(FEATURES, self.keep) if k]

    def predict(self, x: Array) -> Array:
        return self.model.predict(np.atleast_2d(x)[:, self.keep])


def fit_correction(x: Array, y: Array) -> Correction:
    """HistGradientBoostingRegressor on the physics model's energy log-ratio residual (needs scikit-learn)."""
    from sklearn.ensemble import HistGradientBoostingRegressor

    keep = ~np.all(np.isnan(x), axis=0)
    return Correction(HistGradientBoostingRegressor(**HGB_PARAMS).fit(x[:, keep], y), keep)


@dataclass
class Calibration:
    rows: list[cf.CalibrationRow]  # physics only
    k: float | None
    k_iqr: float | None
    corrected_rows: list[cf.CalibrationRow]  # physics + out-of-fold correction
    k_corrected: float | None
    n_correction_train: int

    @property
    def n(self) -> int:
        return len(self.rows)


def calibrate_bands(train: Sequence[ie.Case], rng_for: RngFor, n_runs: int, n_folds: int = N_FOLDS,
                    samples: dict[str, Array] | None = None) -> Calibration:
    """Out-of-sample physics predictions for every training trip, their conformal k, then the same with a
    cross-fitted learned correction. ``samples`` lets a caller pass precomputed physics samples (tests, reuse)."""
    train = list(train)
    phys = samples if samples is not None else {c.record_id: physics_samples(c, train, rng_for, n_runs) for c in train}
    rows = [cf.calibration_row(c.record_id, c.group, c.record_type, phys[c.record_id], c.observed) for c in train]
    targets = [cf.energy_log_ratio(c.record_type, c.observed, float(np.median(phys[c.record_id]))) for c in train]
    ok = [i for i, t in enumerate(targets) if t is not None]
    corrected: list[cf.CalibrationRow] = []
    if len(ok) >= 2 * HGB_PARAMS["min_samples_leaf"]:
        x = np.vstack([features(train[i]) for i in ok])
        y = np.array([targets[i] for i in ok])
        folds = np.array(cf.group_folds([train[i].group for i in ok], n_folds))
        for f in sorted(set(folds)):
            fit_idx, pred_idx = np.flatnonzero(folds != f), np.flatnonzero(folds == f)
            if len(fit_idx) < 2 * HGB_PARAMS["min_samples_leaf"]:
                continue
            model = fit_correction(x[fit_idx], y[fit_idx])
            for j, ratio in zip(pred_idx, model.predict(x[pred_idx])):
                c = train[ok[j]]
                s = cf.apply_correction(c.record_type, phys[c.record_id], float(ratio))
                corrected.append(cf.calibration_row(c.record_id, c.group, c.record_type, s, c.observed))
    return Calibration(rows, cf.conformal_quantile([r.score for r in rows]),
                       cf.conformal_quantile([r.score_iqr for r in rows]), corrected,
                       cf.conformal_quantile([r.score for r in corrected]) if corrected else None, len(ok))


def correction_model(train: Sequence[ie.Case], phys: dict[str, Array]) -> Correction | None:
    """The final correction, fitted on every training trip (used on held-out trips); None when too few."""
    pairs = [(features(c), cf.energy_log_ratio(c.record_type, c.observed, float(np.median(phys[c.record_id]))))
             for c in train]
    pairs = [(x, y) for x, y in pairs if y is not None]
    if len(pairs) < 2 * HGB_PARAMS["min_samples_leaf"]:
        return None
    return fit_correction(np.vstack([p[0] for p in pairs]), np.array([p[1] for p in pairs]))
