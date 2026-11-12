"""E1-India (F4): forecast held-out PUBLIC, SELF-REPORTED Indian EV trips through the normal planner path.

    uv run python experiments/e1_india_real.py

Reads data/processed/trips_india_enriched.json (verified rows only, from scripts/enrich_trips.py) and
data/corpus/vehicles_india.csv. Held-out split: 30 % of source pages by a fixed hash rule, frozen in
data/splits/india_trips_split.json (a source is never in both sets). Calibration and the regression
baseline use training records only; the held-out set is scored once per run and reported as it comes out.
Outputs results/e1_india_real.json and .png, labelled REAL with n; PRELIMINARY when held-out n < 15.
"""

from __future__ import annotations

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

from verity import india_eval as ie  # noqa: E402
from verity.corpus import VEHICLES, read_csv  # noqa: E402
from verity.paths import CORPUS_DIR, PROCESSED_DIR, RESULTS_DIR, SPLITS_DIR  # noqa: E402

ENRICHED = PROCESSED_DIR / "trips_india_enriched.json"
SPLIT_FILE = SPLITS_DIR / "india_trips_split.json"
OUT_JSON = RESULTS_DIR / "e1_india_real.json"
OUT_PNG = RESULTS_DIR / "e1_india_real.png"
PRELIMINARY_BELOW = 15
SEED = 20261112
METHODS = ("verity_prior", "verity_calibrated", "claimed_linear", "flat_minus_25", "regression")


def case_rng(record_id: str, method: str) -> np.random.Generator:
    """Per-record seed, so a result never depends on record order."""
    digest = hashlib.sha256(f"{SEED}|{method}|{record_id}".encode("utf-8")).hexdigest()
    return np.random.default_rng(int(digest[:12], 16))


def load_cases(enriched: Path, corpus: Path) -> tuple[list[ie.Case], Counter, int]:
    doc = json.loads(enriched.read_text(encoding="utf-8")) if enriched.exists() else {"records": []}
    _, vehicle_rows = read_csv(corpus / VEHICLES.filename)
    vehicles = {v["vehicle_id"]: v for v in vehicle_rows}
    cases, skipped = [], Counter()
    for record in doc["records"]:
        case, reason = ie.case_from_record(record, vehicles)
        if case is None:
            skipped[reason] += 1
        else:
            cases.append(case)
    return cases, skipped, len(doc["records"])


def predict_all(train: list[ie.Case], test: list[ie.Case], n_runs: int) -> tuple[dict, dict]:
    calibrated = {vid: ie.calibrated_posterior([c for c in train if c.vehicle_id == vid])
                  for vid in sorted({c.vehicle_id for c in test})}
    regression = ie.fit_regression(train)
    preds: dict[str, dict] = {m: {} for m in METHODS}
    for case in test:
        rid = case.record_id
        preds["verity_prior"][rid] = ie.predictive_samples(case, ie.prior_posterior(case), case_rng(rid, "prior"),
                                                           n_runs)
        post = calibrated.get(case.vehicle_id)
        preds["verity_calibrated"][rid] = None if post is None else ie.predictive_samples(
            case, post, case_rng(rid, "calibrated"), n_runs)
        preds["claimed_linear"][rid] = ie.claimed_point(case, 1.0)
        preds["flat_minus_25"][rid] = ie.claimed_point(case, ie.FLAT_RULE_FACTOR)
        preds["regression"][rid] = None if regression is None else ie.regression_samples(
            case, regression, case_rng(rid, "regression"), n_runs)
    info = {"calibrated_vehicles": sorted(v for v, p in calibrated.items() if p is not None),
            "regression": ("not fit: fewer than 7 training records with a Wh/km equivalent"
                           if regression is None else "fit on training records only")}
    return preds, info


def calibration_gain(test: list[ie.Case], preds: dict) -> dict:
    out = {}
    for vid in sorted({c.vehicle_id for c in test}):
        cases = [c for c in test if c.vehicle_id == vid and preds["verity_calibrated"][c.record_id] is not None]
        if cases:
            out[vid] = {"n_test": len(cases), "prior": ie.score(cases, preds["verity_prior"])["mae_by_type"],
                        "calibrated": ie.score(cases, preds["verity_calibrated"])["mae_by_type"]}
    return out


def assumptions() -> dict:
    names = ("SELF_REPORT_SD_PP", "WHKM_REPORT_REL_SD", "UNKNOWN_TEMP_C", "UNKNOWN_PASSENGERS", "CALIBRATION_TEMP_C",
             "HIGHWAY_80_100_CAP_MPS", "FLAT_RULE_FACTOR", "N_SCENARIOS", "MIN_CALIBRATION_RECORDS", "TEST_FRACTION")
    return {name: getattr(ie, name) for name in names}


def _status(n_test: int) -> tuple[str, str]:
    if n_test == 0:
        return "NOT_RUN", "no verified held-out records yet"
    if n_test < PRELIMINARY_BELOW:
        return "PRELIMINARY", f"held-out n < {PRELIMINARY_BELOW}"
    return "FINAL", ""


NOTES = [
    "Self-reported public data: SoC is whole percent; distances and conditions are as published.",
    "Point baselines (claimed_linear, flat_minus_25) have no band, so no coverage.",
    "highway_100_plus is capped by the planner's free highway speed (26 m/s) and may be under-predicted.",
    "Wilson intervals treat records as independent; several records can share one vehicle or author.",
]


def run(enriched: Path = ENRICHED, corpus: Path = CORPUS_DIR, split_file: Path = SPLIT_FILE,
        n_runs: int = 2000) -> dict:
    cases, skipped, n_records = load_cases(enriched, corpus)
    split = ie.update_split(split_file, {c.group for c in cases})["assignments"]
    usable = [c for c in cases if not c.distance_flag]
    train = [c for c in usable if split[c.group] == "train"]
    test = [c for c in usable if split[c.group] == "test"]
    status, reason = _status(len(test))
    result = {
        "experiment": "E1-India", "is_simulated": False,
        "label": f"REAL: public self-reported Indian trips, n={len(test)}", "status": status, "reason": reason,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "counts": {"enriched_records": n_records, "evaluable": len(cases), "skipped": dict(skipped),
                   "excluded_distance_mismatch": [c.record_id for c in cases if c.distance_flag],
                   "train": len(train), "test": len(test), "test_by_type": dict(Counter(c.record_type for c in test)),
                   "source_groups_train": len({c.group for c in train}),
                   "source_groups_test": len({c.group for c in test})},
        "assumptions": assumptions(), "methods": {}, "per_model_calibration": {}, "notes": NOTES}
    if test:
        preds, info = predict_all(train, test, n_runs)
        result.update(methods={m: ie.score(test, preds[m]) for m in METHODS},
                      per_model_calibration=calibration_gain(test, preds), fit_info=info)
    return result


def plot(result: dict, path: Path) -> None:
    fig, ax = plt.subplots(figsize=(8, 4.5))
    title = f"E1-India held-out coverage ({result['label']})"
    banded = [m for m, s in result["methods"].items() if s.get("coverage")]
    if not banded:
        ax.axis("off")
        message = ("Not run: no verified held-out records yet.\nRe-run after rows are verified."
                   if result["status"] == "NOT_RUN" else "No method produced predictive bands.")
        ax.text(0.5, 0.5, message, ha="center", va="center", fontsize=12)
    else:
        levels = list(result["methods"][banded[0]]["coverage"])
        for i, m in enumerate(banded):
            cov = result["methods"][m]["coverage"]
            ys = np.array([cov[lv]["coverage"] for lv in levels])
            lo = np.array([cov[lv]["wilson95"][0] for lv in levels])
            hi = np.array([cov[lv]["wilson95"][1] for lv in levels])
            ax.errorbar(np.arange(len(levels)) + 0.08 * i, ys, yerr=[ys - lo, hi - ys], fmt="o", capsize=3, label=m)
        ax.plot(range(len(levels)), [int(lv[:-1]) / 100 for lv in levels], "k--", lw=1, label="nominal")
        ax.set_xticks(range(len(levels)), levels)
        ax.set_ylim(0, 1.05)
        ax.set_ylabel("held-out coverage (Wilson 95%)")
        ax.legend(fontsize=8)
    if result["status"] == "PRELIMINARY":
        title += " PRELIMINARY"
    ax.set_title(title, fontsize=10)
    fig.tight_layout()
    fig.savefig(path, dpi=120)
    plt.close(fig)


def main() -> int:
    result = run()
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(json.dumps(result, indent=1), encoding="utf-8")
    plot(result, OUT_PNG)
    print(json.dumps({k: result[k] for k in ("label", "status", "reason", "counts")}, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
