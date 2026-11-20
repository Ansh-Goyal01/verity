"""E7 (WP5): honest bands. Split-conformal calibration and a learned correction, on REAL public Indian trips.

    uv run python experiments/e7_honest_bands.py                  # calibrate on training, then score held-out once
    uv run python experiments/e7_honest_bands.py --calibrate-only # write data/processed/conformal.json only

1. Calibration (TRAINING split only, out of sample): every training trip is predicted by the planner's Monte Carlo
   (india_eval.predictive_samples) with a posterior fitted on OTHER source pages; its normalised residual
   |observed - median| / (model's own 90 % half-width) feeds the split-conformal quantile k. The same is done with a
   cross-fitted HistGradientBoosting correction of the physics residual. Saved to data/processed/conformal.json with
   every per-trip score, so k is reproducible from the file.
2. Held-out split (scored once per run, never used for tuning): MAE and 50/80/90 % coverage for
   (a) physics only, (b) physics + conformal, (c) physics + correction + conformal, (d) pure ML regression baseline.
Outputs results/e7_honest_bands.json and .png, labelled REAL with n; PRELIMINARY when held-out n < 15.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
from e1_india_real import ENRICHED, SPLIT_FILE, load_cases  # noqa: E402

from verity import conformal as cf  # noqa: E402
from verity import honest_bands as hb  # noqa: E402
from verity import india_eval as ie  # noqa: E402
from verity.paths import CORPUS_DIR, PROCESSED_DIR, RESULTS_DIR  # noqa: E402

CONFORMAL_FILE = PROCESSED_DIR / "conformal.json"
OUT_JSON = RESULTS_DIR / "e7_honest_bands.json"
OUT_PNG = RESULTS_DIR / "e7_honest_bands.png"
PRELIMINARY_BELOW = 15
SEED = 20261121
N_RUNS = 2000
METHODS = {"physics_only": "(a) physics only", "physics_conformal": "(b) physics + conformal",
           "physics_correction_conformal": "(c) physics + correction + conformal",
           "ml_regression": "(d) pure ML regression (baseline)"}


def rng_for(record_id: str, method: str) -> np.random.Generator:
    """Per-record seed, so no result depends on record order."""
    digest = hashlib.sha256(f"{SEED}|{method}|{record_id}".encode("utf-8")).hexdigest()
    return np.random.default_rng(int(digest[:12], 16))


def conformal_doc(cal: hb.Calibration, train: list[ie.Case]) -> dict:
    return {
        "schema": cf.SCHEMA, "is_simulated": False, "method": "split conformal", "level": cf.LEVEL,
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "score": "|observed - median| / half-width of the model's own central 90 % band (out-of-sample prediction)",
        "n": cal.n, "k": cal.k, "k_applied": cf.applied_scale(cal.k), "k_iqr": cal.k_iqr,
        "sources": [f"data/corpus/trips_india.csv: verified TRAINING-split rows ({cal.n} trips, "
                    f"{len({c.group for c in train})} source pages)"],
        "excluded": ["India held-out split (data/splits/india_trips_split.json)", "BMW i3 trips (training and held-out)",
                     "VED (dropped, docs/DECISIONS.md WP5)"],
        "by_record_type": dict(Counter(c.record_type for c in train)),
        "guarantee_note": cf.GUARANTEE_NOTE, "n_runs": N_RUNS, "seed": SEED,
        "rows": [r.as_dict() for r in cal.rows],
        "correction": {"model": "HistGradientBoostingRegressor", "params": hb.HGB_PARAMS, "features": hb.FEATURES,
                       "target": "energy log-ratio log(observed / predicted median); inverse for range tests",
                       "cross_fit_folds": hb.N_FOLDS, "n": len(cal.corrected_rows), "k": cal.k_corrected,
                       "k_applied": cf.applied_scale(cal.k_corrected),
                       "rows": [r.as_dict() for r in cal.corrected_rows],
                       "in_releases": False},
    }


def _status(n_test: int) -> tuple[str, str]:
    if n_test == 0:
        return "NOT_RUN", "no verified held-out records yet"
    if n_test < PRELIMINARY_BELOW:
        return "PRELIMINARY", f"held-out n < {PRELIMINARY_BELOW}"
    return "FINAL", ""


def heldout_predictions(train: list[ie.Case], test: list[ie.Case], phys_train: dict, cal: hb.Calibration) -> dict:
    phys = {c.record_id: hb.physics_samples(c, train, rng_for, N_RUNS) for c in test}
    model = hb.correction_model(train, phys_train)
    regression = ie.fit_regression(train)
    k_b, k_c = cf.applied_scale(cal.k), cf.applied_scale(cal.k_corrected)
    preds: dict[str, dict] = {m: {} for m in METHODS}
    for c in test:
        rid, s = c.record_id, phys[c.record_id]
        preds["physics_only"][rid] = s
        preds["physics_conformal"][rid] = cf.widen(s, k_b)
        preds["physics_correction_conformal"][rid] = None if model is None or cal.k_corrected is None else cf.widen(
            cf.apply_correction(c.record_type, s, float(model.predict(hb.features(c))[0])), k_c)
        preds["ml_regression"][rid] = None if regression is None else ie.regression_samples(
            c, regression, rng_for(rid, "regression"), N_RUNS)
    return preds


def run(enriched: Path = ENRICHED, corpus: Path = CORPUS_DIR, split_file: Path = SPLIT_FILE,
        calibrate_only: bool = False) -> tuple[dict, dict | None]:
    cases, skipped, n_records = load_cases(enriched, corpus)
    split = ie.update_split(split_file, {c.group for c in cases})["assignments"]
    train = hb.calibration_cases(cases, split)
    phys_train = {c.record_id: hb.physics_samples(c, train, rng_for, N_RUNS) for c in train}
    cal = hb.calibrate_bands(train, rng_for, N_RUNS, samples=phys_train)
    doc = conformal_doc(cal, train)
    if calibrate_only:
        return doc, None
    test = hb.heldout_cases(cases, split)  # the held-out split is opened here, after k is fixed
    status, reason = _status(len(test))
    result = {
        "experiment": "E7", "is_simulated": False,
        "label": f"REAL: public self-reported Indian trips, held-out n={len(test)}", "status": status,
        "reason": reason, "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "counts": {"enriched_records": n_records, "evaluable": len(cases), "skipped": dict(skipped),
                   "calibration_train": len(train), "test": len(test),
                   "test_by_type": dict(Counter(c.record_type for c in test)),
                   "source_groups_train": len({c.group for c in train}),
                   "source_groups_test": len({c.group for c in test})},
        "calibration": {"n": cal.n, "k": cal.k, "k_applied": doc["k_applied"], "k_iqr": cal.k_iqr,
                        "n_correction": len(cal.corrected_rows), "k_corrected": cal.k_corrected,
                        "k_corrected_applied": doc["correction"]["k_applied"]},
        "methods": {}, "method_labels": METHODS,
        "notes": [cf.GUARANTEE_NOTE,
                  "k is calibrated at the 90 % level; 50 % and 80 % coverage after widening are reported, not promised.",
                  "Bands are never narrowed: k below 1 is reported and not applied.",
                  "(d) is the existing OLS Wh/km baseline of E1-India.",
                  "Seed sensitivity: india_eval draws only 8 unknown-context scenarios per trip, so physics MAE on "
                  "these 21 trips moves between about 26 and 30 Wh/km with the seed (E1-India's seed gives 25.9); "
                  "k and coverage move with it. Not tuned after seeing held-out results.",
                  "Self-reported public data: SoC is whole percent; Wilson intervals treat records as independent."]}
    if test:
        preds = heldout_predictions(train, test, phys_train, cal)
        result["methods"] = {m: ie.score(test, preds[m]) for m in METHODS}
    return doc, result


def plot(result: dict, path: Path) -> None:
    fig, ax = plt.subplots(figsize=(8.5, 4.5))
    title = f"E7 honest bands: held-out coverage ({result['label']})"
    banded = [m for m, s in result["methods"].items() if s.get("coverage")]
    if not banded:
        ax.axis("off")
        ax.text(0.5, 0.5, "Not run: no verified held-out records yet.", ha="center", va="center", fontsize=12)
    else:
        levels = list(result["methods"][banded[0]]["coverage"])
        for i, m in enumerate(banded):
            cov = result["methods"][m]["coverage"]
            ys = np.array([cov[lv]["coverage"] for lv in levels])
            lo = np.array([cov[lv]["wilson95"][0] for lv in levels])
            hi = np.array([cov[lv]["wilson95"][1] for lv in levels])
            ax.errorbar(np.arange(len(levels)) + 0.08 * i, ys, yerr=[ys - lo, hi - ys], fmt="o", capsize=3,
                        label=METHODS[m])
        ax.plot(range(len(levels)), [int(lv[:-1]) / 100 for lv in levels], "k--", lw=1, label="nominal")
        ax.set_xticks(range(len(levels)), levels)
        ax.set_ylim(0, 1.05)
        ax.set_ylabel("held-out coverage (Wilson 95%)")
        ax.legend(fontsize=7, loc="lower right")
    if result["status"] == "PRELIMINARY":
        title += " PRELIMINARY"
    ax.set_title(title, fontsize=9)
    fig.tight_layout()
    fig.savefig(path, dpi=120)
    plt.close(fig)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--calibrate-only", action="store_true", help="write conformal.json, do not open held-out")
    args = parser.parse_args()
    doc, result = run(calibrate_only=args.calibrate_only)
    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    CONFORMAL_FILE.write_text(json.dumps(doc, indent=1), encoding="utf-8")
    print(json.dumps({k: doc[k] for k in ("n", "k", "k_applied", "k_iqr")}
                     | {"k_corrected": doc["correction"]["k"]}, indent=1))
    if result is not None:
        RESULTS_DIR.mkdir(parents=True, exist_ok=True)
        OUT_JSON.write_text(json.dumps(result, indent=1), encoding="utf-8")
        plot(result, OUT_PNG)
        print(json.dumps({k: result[k] for k in ("label", "status", "counts")}
                         | {"methods": {m: s for m, s in result["methods"].items()}}, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
