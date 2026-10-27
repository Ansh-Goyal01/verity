"""E1: learning curve on held-out trips (SIMULATED data).

Question: how accurate is the calibrated physics model on trips it never saw, after very few trips of
that vehicle, compared with simple alternatives? Calibrate each vehicle on 1, 2, 3, 5 and 10 of its
TRAINING trips, predict the SoC drop of its HELD-OUT test trips.

Methods: verity_class_prior (Bayesian physics, generic class prior), verity_pooled (same, with a prior
pooled from the other vehicles of the class), nominal_spec (physics at class defaults, no learning),
flat_minus_25 (claimed range minus 25 %), regression (numpy least squares on trip features, no physics).

Metrics: mean absolute error in SoC percentage points; share of test trips inside the 90 % band; mean
band width. Baselines get a band of +/-1.645 x RMS error on their own training trips (floored at 0.5 pp).

Honesty notes: the data is SIMULATED by our own truth simulator; it is neither German nor Indian driving.
The dataset named "dev" (different seed) is the only one used for choosing settings; "main" is evaluated once.
BMW i3 real-data part: see engine/verity/datasets/bmw_i3.py (blocked until the dataset is downloaded).
"""

from __future__ import annotations

import argparse
import json
import time

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402

from verity.calibrate import (  # noqa: E402
    DEFAULT_RHO_REL,
    calibrate,
    central_band,
    pool_fleet,
    predict_delta_soc,
    predictive_samples,
)
from verity.datasets import bmw_i3  # noqa: E402
from verity.paths import RESULTS_DIR  # noqa: E402
from verity.splits import load_eval_dataset  # noqa: E402
from verity.trips import TripObservation, load_sim_observation  # noqa: E402
from verity.vehicles import get_prior  # noqa: E402

N_GRID = (1, 2, 3, 5, 10)
METHODS = ("verity_pooled", "verity_class_prior", "nominal_spec", "flat_minus_25", "regression")
LABELS = {
    "verity_pooled": "Verity physics + fleet-pooled prior",
    "verity_class_prior": "Verity physics + class prior",
    "nominal_spec": "Nominal-spec model (no learning)",
    "flat_minus_25": 'Flat "minus 25%" rule',
    "regression": "Plain regression (no physics)",
}
Z90 = 1.6449
BAND_FLOOR_PP = 0.5
Y_CAP_PP = 12.0  # y-axis cap of the error panel; points above it are named in the chart


def _features(o: TripObservation) -> np.ndarray:
    hours = o.duration_s / 3600.0
    return np.array([o.distance_km, hours, o.ascent_m / 1000.0, float(o.ac_on) * hours,
                     o.passengers * o.distance_km / 100.0])


def _baseline_band(train_err: np.ndarray) -> float:
    return max(Z90 * float(np.sqrt(np.mean(train_err**2))), BAND_FLOOR_PP)


def run_e1(dataset: str = "main", rho_rel: float = DEFAULT_RHO_REL, resamples: int = 3, seed: int = 11,
           n_draws: int = 400) -> dict:
    t0 = time.time()
    manifest, split = load_eval_dataset(dataset)
    obs: dict[str, TripObservation] = {
        t["trip_id"]: load_sim_observation(dataset, t["trip_id"]) for t in manifest["trips"]
    }
    by_class: dict[str, list[str]] = {}
    for vid, v in split["vehicles"].items():
        by_class.setdefault(v["class_key"], []).append(vid)

    # one calibration per vehicle on ALL its training trips, only to build leave-one-vehicle-out pooled priors
    full_posts = {}
    for vid, v in split["vehicles"].items():
        prior = get_prior(v["class_key"])
        full_posts[vid] = calibrate(prior.u_mean, prior.u_cov, [obs[t] for t in v["train"]],
                                    prior.base, prior.nominal_kwh, rho_rel)

    errs = {m: {n: [] for n in N_GRID} for m in METHODS}
    cover = {m: {n: [] for n in N_GRID} for m in METHODS}
    width = {m: {n: [] for n in N_GRID} for m in METHODS}
    class_err = {c: {n: [] for n in N_GRID} for c in by_class}

    for vi, (vid, v) in enumerate(split["vehicles"].items()):
        prior = get_prior(v["class_key"])
        others = [full_posts[o] for o in by_class[v["class_key"]] if o != vid]
        pooled_mean, pooled_cov = pool_fleet(others, prior.u_mean, prior.u_cov)
        train = [obs[t] for t in v["train"]]
        test = [obs[t] for t in v["test"]]
        y_test = np.array([o.delta_soc_pct for o in test])
        nominal_all = np.array([predict_delta_soc(prior.base, prior.nominal_kwh, prior.u_mean, o)[0] for o in test])
        flat_all = np.array([100.0 * o.distance_km / (0.75 * prior.claimed_range_km) for o in test])
        for n in N_GRID:
            if n > len(train):
                continue
            for r in range(1 if n == len(train) else resamples):
                rng = np.random.default_rng([seed, vi, n, r])
                idx = np.sort(rng.choice(len(train), size=n, replace=False))
                sub = [train[i] for i in idx]
                y_sub = np.array([o.delta_soc_pct for o in sub])

                def record(method: str, median: np.ndarray, lo: np.ndarray, hi: np.ndarray) -> None:
                    e = np.abs(median - y_test)
                    errs[method][n].extend(e.tolist())
                    cover[method][n].extend(((y_test >= lo) & (y_test <= hi)).tolist())
                    width[method][n].extend((hi - lo).tolist())
                    if method == "verity_pooled":
                        class_err[v["class_key"]][n].extend(e.tolist())

                for method, (mu, cov) in (("verity_pooled", (pooled_mean, pooled_cov)),
                                          ("verity_class_prior", (prior.u_mean, prior.u_cov))):
                    post = calibrate(mu, cov, sub, prior.base, prior.nominal_kwh, rho_rel)
                    med, lo, hi = [], [], []
                    for o in test:
                        s = predictive_samples(post, o, rng, n_draws)
                        a, b = central_band(s)
                        med.append(np.median(s)); lo.append(a); hi.append(b)
                    record(method, np.array(med), np.array(lo), np.array(hi))

                nominal_sub = np.array([predict_delta_soc(prior.base, prior.nominal_kwh, prior.u_mean, o)[0] for o in sub])
                half = _baseline_band(y_sub - nominal_sub)
                record("nominal_spec", nominal_all, nominal_all - half, nominal_all + half)

                flat_sub = np.array([100.0 * o.distance_km / (0.75 * prior.claimed_range_km) for o in sub])
                half = _baseline_band(y_sub - flat_sub)
                record("flat_minus_25", flat_all, flat_all - half, flat_all + half)

                x_sub = np.stack([_features(o) for o in sub])
                coef, *_ = np.linalg.lstsq(x_sub, y_sub, rcond=None)
                half = _baseline_band(y_sub - x_sub @ coef)
                reg = np.stack([_features(o) for o in test]) @ coef
                record("regression", reg, reg - half, reg + half)

    table = {
        m: {str(n): {"mae_pp": round(float(np.mean(errs[m][n])), 3),
                     "coverage_90": round(float(np.mean(cover[m][n])), 3),
                     "mean_band_width_pp": round(float(np.mean(width[m][n])), 3),
                     "n_predictions": len(errs[m][n])}
            for n in N_GRID if errs[m][n]}
        for m in METHODS
    }
    per_class = {c: {str(n): round(float(np.mean(v)), 3) for n, v in d.items() if v} for c, d in class_err.items()}
    return {
        "experiment": "E1 learning curve", "is_simulated": True, "dataset": dataset, "rho_rel": rho_rel,
        "n_grid": list(N_GRID), "resamples": resamples, "seed": seed, "methods": table,
        "verity_pooled_mae_by_class": per_class,
        "test_trips": sum(len(v["test"]) for v in split["vehicles"].values()),
        "vehicles": len(split["vehicles"]), "runtime_s": round(time.time() - t0, 1),
        "bmw_i3": bmw_i3.status(),
        "notes": ["Data is SIMULATED by verity.sim (planner physics differs from the truth by design).",
                  "Baseline bands are +/-1.645 x RMS training error (floor 0.5 pp); Verity bands are posterior-predictive.",
                  "BMW i3 data (German driving with heater load) is not available; that part is blocked."],
    }


def plot(result: dict, path) -> None:
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(11, 4.2))
    ns = result["n_grid"]
    for m in METHODS:
        pts = [(n, result["methods"][m][str(n)]) for n in ns if str(n) in result["methods"][m]]
        x = [p[0] for p in pts]
        ax1.plot(x, [p[1]["mae_pp"] for p in pts], marker="o", label=LABELS[m])
        ax2.plot(x, [p[1]["coverage_90"] for p in pts], marker="o", label=LABELS[m])
    ax1.set(xlabel="trips used to calibrate the vehicle", ylabel="mean absolute error (SoC points)",
            title="Error on held-out trips", xticks=ns, ylim=(0, Y_CAP_PP))
    off_scale = [(n, round(result["methods"]["regression"][str(n)]["mae_pp"], 1)) for n in ns
                 if str(n) in result["methods"]["regression"] and result["methods"]["regression"][str(n)]["mae_pp"] > Y_CAP_PP]
    if off_scale:
        ax1.text(0.02, 0.97, "plain regression off-scale: " + ", ".join(f"n={n}: {v} pp" for n, v in off_scale),
                 transform=ax1.transAxes, fontsize=7, va="top", color="tab:purple")
    ax2.axhline(0.9, color="grey", ls="--", lw=1)
    ax2.set(xlabel="trips used to calibrate the vehicle", ylabel="share inside the 90% band", ylim=(0, 1.02),
            title="Band coverage (target 0.90)", xticks=ns)
    ax1.legend(fontsize=7)
    fig.suptitle(f"E1: learning curve on held-out trips (simulated) - dataset '{result['dataset']}'")
    fig.text(0.5, 0.005, "SIMULATED data from verity.sim; not German or Indian driving data.", ha="center", fontsize=8)
    fig.tight_layout(rect=(0, 0.03, 1, 0.95))
    fig.savefig(path, dpi=140)
    plt.close(fig)


def main() -> None:
    parser = argparse.ArgumentParser(description="E1 learning curve (simulated)")
    parser.add_argument("--dataset", default="main", help="'main' (held-out split) or 'dev' (tuning only)")
    parser.add_argument("--rho", type=float, default=DEFAULT_RHO_REL)
    parser.add_argument("--resamples", type=int, default=3)
    parser.add_argument("--replot", action="store_true", help="redraw the chart from the saved JSON (no evaluation)")
    args = parser.parse_args()
    out = RESULTS_DIR if args.dataset == "main" else RESULTS_DIR / "dev"
    out.mkdir(parents=True, exist_ok=True)
    stem = "e1_learning_curve_simulated"
    if args.replot:
        plot(json.loads((out / f"{stem}.json").read_text(encoding="utf-8")), out / f"{stem}.png")
        print(f"re-plotted {out / (stem + '.png')} from saved results")
        return
    result = run_e1(args.dataset, args.rho, args.resamples)
    (out / f"{stem}.json").write_text(json.dumps(result, indent=1), encoding="utf-8")
    plot(result, out / f"{stem}.png")
    print(f"E1 ({args.dataset}, SIMULATED) rho_rel={args.rho} runtime {result['runtime_s']} s; BMW i3: {result['bmw_i3']['status']}")
    for m in METHODS:
        row = "  ".join(f"n={n}: MAE {result['methods'][m][str(n)]['mae_pp']:.2f} cov {result['methods'][m][str(n)]['coverage_90']:.2f}"
                        for n in result["n_grid"] if str(n) in result["methods"][m])
        print(f"{LABELS[m]:38s} {row}")


if __name__ == "__main__":
    main()
