"""W1.4: BMW i3 held-out evaluation of the energy model on REAL driving (Germany, TU Munich, DOI 10.21227/6jr9-5235).

    uv run python scripts/bmw_heldout.py --dev      # TRAINING trips only; run as often as you like
    uv run python scripts/bmw_heldout.py --final    # opens the held-out split EXACTLY ONCE (refuses a second time)

Both modes fit the posterior on the usable TRAINING trips and score two things:
  * capacity: the Passport's usable-capacity estimate (80 % / 95 % credible interval) against the ground truth, the
    measured battery energy (integrated voltage x current) per 100 BMS SoC points, pooled over the scored trips;
  * per-trip: the predicted SoC drop (percentage points) against the dashboard SoC drop of each scored trip, with the
    share of trips inside the parameter-uncertainty band (central 80 % / 95 % of 1,000 posterior draws; this band does
    NOT include model discrepancy or reading noise, so under-coverage is expected and is reported as it comes).
``--dev`` scores the training trips themselves (in-sample); ``--final`` scores the held-out trips. Both priors are
scored in the same pass: ``baseline`` is F9's published prior (ceiling 1.05 x 21.6 kWh, class CdA) and ``v2`` the frozen
prior of ``bmw_passport.i3_prior_v2``. The held-out trips are never inspected: only these summary numbers are written.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "engine"))

from verity.calibrate import predict_delta_soc  # noqa: E402
from verity.datasets import bmw_i3  # noqa: E402
from verity.datasets.bmw_passport import (  # noqa: E402
    I3_CEILING_SCALE,
    I3_CDA_M2,
    i3_prior,
    i3_prior_v2,
    measured,
    observation,
    truth_kwh_per_100,
    usable_for_passport,
)
from verity.passport import capacity, fit  # noqa: E402
from verity.paths import RESULTS_DIR  # noqa: E402

PRELIMINARY_BELOW = 15  # fewer scored trips than this: the result carries the PRELIMINARY label (guide W1.1)
N_DRAWS = 1000
SEED = 9
LABEL = ("REAL: BMW i3 60 Ah, Germany (TU Munich, DOI 10.21227/6jr9-5235); German driving with heater or A/C, "
         "not Indian conditions")
DEV_FILE = RESULTS_DIR / "bmw_i3_dev_train_only.json"
FINAL_FILE = RESULTS_DIR / "bmw_i3_heldout.json"


def score(prior, train_obs: list, eval_obs: list, eval_meas: list) -> dict:
    post = fit(prior, train_obs)
    cap = capacity(post)
    truth = truth_kwh_per_100(eval_meas)
    est = cap["usable_kwh_p50"]
    u_draws = post.sample_u(np.random.default_rng(SEED), N_DRAWS)
    rows = []
    for o in eval_obs:
        observed = float(o.soc_pct[0] - o.soc_pct[1])
        pred = float(predict_delta_soc(post.base, post.nominal_kwh, post.mean_u, o)[0])
        band = predict_delta_soc(post.base, post.nominal_kwh, u_draws, o)
        lo80, hi80, lo95, hi95 = np.quantile(band, [0.1, 0.9, 0.025, 0.975])
        rows.append((observed, pred, lo80 <= observed <= hi80, lo95 <= observed <= hi95))
    obs_pp, pred_pp = np.array([r[0] for r in rows]), np.array([r[1] for r in rows])
    return {
        "capacity": {"estimate_kwh_p50": est, "ci80_kwh": cap["ci80_kwh"], "ci95_kwh": cap["ci95_kwh"],
                     "truth_kwh_per_100_soc_points": round(truth, 2),
                     "error_pct": round(100.0 * (est - truth) / truth, 1),
                     "truth_in_ci80": cap["ci80_kwh"][0] <= truth <= cap["ci80_kwh"][1],
                     "truth_in_ci95": cap["ci95_kwh"][0] <= truth <= cap["ci95_kwh"][1],
                     "model_ceiling_kwh": round(prior.nominal_kwh * 1.05, 2)},
        "per_trip_soc_drop": {
            "n_trips": len(rows),
            "median_abs_error_pp": round(float(np.median(np.abs(pred_pp - obs_pp))), 2),
            "mean_error_pp": round(float(np.mean(pred_pp - obs_pp)), 2),
            "mean_relative_error_pct": round(100.0 * float(np.sum(pred_pp - obs_pp) / np.sum(obs_pp)), 1),
            "band80_coverage": round(float(np.mean([r[2] for r in rows])), 2),
            "band95_coverage": round(float(np.mean([r[3] for r in rows])), 2),
        },
    }


def both_priors(train_obs: list, eval_obs: list, eval_meas: list) -> dict:
    return {"baseline_f9_prior": score(i3_prior(), train_obs, eval_obs, eval_meas),
            "v2_frozen_prior": score(i3_prior_v2(), train_obs, eval_obs, eval_meas)}


def prepared(part: str, *, final: bool) -> tuple[list, list, list]:
    trips = [t for t in bmw_i3.load_split_part(part, final_evaluation=final) if usable_for_passport(t)]
    prior = i3_prior()  # the observation only uses the prior's curb mass, identical for both priors
    return trips, [observation(t, prior) for t in trips], [measured(t) for t in trips]


def header(n_scored: int, km: float, extra: dict) -> dict:
    return {"is_simulated": False, "label": LABEL, "n_trips_scored": n_scored, "km_scored": round(km, 1),
            "preliminary": n_scored < PRELIMINARY_BELOW, "ceiling_scale": I3_CEILING_SCALE,
            "cda_m2_v2": round(I3_CDA_M2, 3), **extra}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dev", action="store_true", help="training trips only, in-sample")
    mode.add_argument("--final", action="store_true", help="open the held-out split exactly once")
    args = parser.parse_args()
    if args.final and FINAL_FILE.exists():  # first, before anything else: the lock holds on a machine without the raw data too (CI)
        raise SystemExit(f"REFUSED: {FINAL_FILE.name} exists, so the held-out split was already opened once. "
                         "It is never re-run or re-tuned against.")
    if not bmw_i3.available():
        raise SystemExit("data/raw/bmw_i3/ is missing: nothing to evaluate")
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    _, train_obs, train_meas = prepared("train", final=False)
    if args.dev:
        out = {**header(len(train_obs), sum(o.distance_km for o in train_obs),
                        {"mode": "dev: TRAINING trips, in-sample (held-out split not opened)"}),
               "train_trips_used": len(train_obs), "results": both_priors(train_obs, train_obs, train_meas)}
        DEV_FILE.write_text(json.dumps(out, indent=1), encoding="utf-8")
    else:
        _, test_obs, test_meas = prepared("test", final=True)  # the one and only opening
        out = {**header(len(test_obs), sum(o.distance_km for o in test_obs),
                        {"mode": "FINAL: held-out TEST trips, opened once; the model was fitted on training trips only"}),
               "train_trips_used": len(train_obs), "results": both_priors(train_obs, test_obs, test_meas)}
        FINAL_FILE.write_text(json.dumps(out, indent=1), encoding="utf-8")
    print(json.dumps(out, indent=1))


if __name__ == "__main__":
    main()
