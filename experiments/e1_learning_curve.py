"""E1: learning curve on held-out trips (SIMULATED data).

Question: how accurate is the calibrated physics model on trips it never saw, after very few trips of
that vehicle, compared with simple alternatives? Calibrate each vehicle on 1, 2, 3, 5 and 10 of its
TRAINING trips, predict the SoC drop of its HELD-OUT test trips.

Methods: verity_class_prior (Bayesian physics, generic class prior), verity_pooled (same, with a prior
pooled from the other vehicles of the class), nominal_spec (physics at class defaults, no learning),
flat_minus_25 (claimed range minus 25 %), regression (numpy least squares on trip features, no physics).

Metrics: MAE averaged over 20 subsets; 50/80/90% mixture-band coverage and unique-trip Wilson 95%
intervals, plus per-subset coverage. Physics discrepancy uses training-only LOO residual variance.
Baselines retain their original RMS training-error bands. No engine or likelihood changes.

Honesty notes: the data is SIMULATED by our own truth simulator; it is neither German nor Indian driving.
The dataset named "dev" (different seed) is the only one used for choosing settings; "main" is evaluated once.
BMW i3 real-data part is separate; see engine/verity/datasets/bmw_i3.py.
"""

from __future__ import annotations

import argparse
import hashlib
from pathlib import Path
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


LEVELS = (0.50, 0.80, 0.90)
PROTOCOL = "R4a-training-LOO-extra-variance-v1"


def wilson(k: int, n: int) -> list[float]:
    """Wilson 95% interval over unique trips, never repeated subset predictions."""
    if n <= 0 or not 0 <= k <= n:
        raise ValueError("Wilson requires 0 <= k <= n and n > 0")
    z2 = 1.96 ** 2
    centre = (k / n + z2 / (2 * n)) / (1 + z2 / n)
    half = 1.96 * np.sqrt((k / n) * (1 - k / n) / n + z2 / (4 * n ** 2)) / (1 + z2 / n)
    return [float(max(0, centre - half)), float(min(1, centre + half))]


def extra_relative_variance(rows: list[dict]) -> float:
    """Training LOO squared errors minus existing predictive variance (method of moments).

    One additional Gaussian discrepancy draw is shared by the whole trip. Scale it by
    predicted trip consumption, floored at one SoC point to handle nearly-zero predictions.
    The zero lower bound is a variance constraint, not a held-out-tuned hyperparameter.
    """
    return max(0.0, float(np.mean([
        ((r["observed"] - r["predicted"]) ** 2 - r["predictive_variance"]) /
        max(abs(r["predicted"]), 1.0) ** 2 for r in rows
    ])))


def protocol_fingerprint(dataset: str, split: dict, rho: float, resamples: int, seed: int, draws: int) -> str:
    """Hash code, settings, split and TRAIN data; never open a held-out trip here."""
    from verity.paths import REPO_ROOT, SIM_DIR
    digest = hashlib.sha256(json.dumps([PROTOCOL, dataset, split, rho, resamples, seed, draws], sort_keys=True).encode())
    paths = [Path(__file__), *[REPO_ROOT / "engine" / "verity" / f"{m}.py"
                              for m in ("calibrate", "physics", "vehicles", "trips")]]
    for v in split["vehicles"].values():
        for tid in v["train"]:
            paths.extend(SIM_DIR / dataset / kind / f"{tid}.json" for kind in ("trips", "context"))
    for path in paths:
        digest.update(path.read_bytes())
    return digest.hexdigest()


def summarise_predictions(samples: list[np.ndarray], y: np.ndarray, medians: list[np.ndarray]) -> dict:
    """Mixture bands give ONE Bernoulli outcome per held-out trip; also retain subset coverage.

    The mixture describes a randomly chosen training subset, not a fitted single model.
    Wilson intervals are descriptive: trips from one vehicle remain dependent.
    """
    mixture = np.concatenate(samples, axis=1)
    out = {"mae_pp": round(float(np.mean(np.abs(np.stack(medians) - y))), 3),
           "n_predictions": len(samples) * len(y), "unique_test_trips": len(y), "bands": {}}
    for level in LEVELS:
        qs = [(1 - level) / 2, 1 - (1 - level) / 2]
        lo, hi = np.quantile(mixture, qs, axis=1)
        covered = (y >= lo) & (y <= hi)
        subset = []
        for draw in samples:
            a, b = np.quantile(draw, qs, axis=1)
            k = int(np.sum((y >= a) & (y <= b)))
            subset.append({"covered": k, "n": len(y), "coverage": k / len(y), "wilson95": wilson(k, len(y))})
        k = int(covered.sum())
        out["bands"][str(int(level * 100))] = {
            "coverage": k / len(y), "covered": k, "n": len(y), "wilson95": wilson(k, len(y)),
            "mean_width_pp": float(np.mean(hi - lo)), "below_nominal": bool(k / len(y) < level),
            "mean_subset_coverage": float(np.mean([v["coverage"] for v in subset])), "per_subset": subset}
    out["coverage_90"] = out["bands"]["90"]["coverage"]
    out["mean_band_width_pp"] = out["bands"]["90"]["mean_width_pp"]
    return out


def run_e1(dataset: str = "main", rho_rel: float = DEFAULT_RHO_REL, resamples: int = 20, seed: int = 11,
           n_draws: int = 400) -> dict:
    """Freeze every training fit and discrepancy estimate before loading any test observation."""
    t0 = time.time()
    _, split = load_eval_dataset(dataset)
    out_dir = RESULTS_DIR if dataset == "main" else RESULTS_DIR / "dev"
    out_dir.mkdir(parents=True, exist_ok=True)
    evaluation = out_dir / "e1_learning_curve_simulated_evaluation.json"
    if evaluation.exists():
        raise FileExistsError("Final evaluation already exists; use --reuse-evaluation (never silently re-evaluate).")
    if resamples < 20:
        raise ValueError("R4a requires at least 20 training subset draws per k")
    fingerprint = protocol_fingerprint(dataset, split, rho_rel, resamples, seed, n_draws)
    train_obs = {tid: load_sim_observation(dataset, tid) for v in split["vehicles"].values() for tid in v["train"]}
    full_posts, by_class = {}, {}
    for vid, v in split["vehicles"].items():
        prior = get_prior(v["class_key"])
        full_posts[vid] = calibrate(prior.u_mean, prior.u_cov, [train_obs[t] for t in v["train"]],
                                    prior.base, prior.nominal_kwh, rho_rel)
        by_class.setdefault(v["class_key"], []).append(vid)
    priors, residuals = {}, {m: [] for m in METHODS[:2]}
    for vi, (vid, v) in enumerate(split["vehicles"].items()):
        prior = get_prior(v["class_key"])
        pooled = pool_fleet([full_posts[o] for o in by_class[v["class_key"]] if o != vid], prior.u_mean, prior.u_cov)
        priors[vid] = dict(zip(METHODS[:2], (pooled, (prior.u_mean, prior.u_cov))))
        train = [train_obs[t] for t in v["train"]]
        for mi, method in enumerate(METHODS[:2]):
            mu, cov = priors[vid][method]
            for i, held in enumerate(train):
                post = calibrate(mu, cov, train[:i] + train[i+1:], prior.base, prior.nominal_kwh, rho_rel)
                draws = predictive_samples(post, held, np.random.default_rng([seed, vi, mi, i, 901]), n_draws)
                residuals[method].append({"trip_id": held.trip_id, "observed": held.delta_soc_pct,
                                         "predicted": float(np.median(draws)), "predictive_variance": float(np.var(draws, ddof=1))})
    discrepancy = {m: extra_relative_variance(rows) for m, rows in residuals.items()}
    prepared, selections = {}, {}
    for vi, (vid, v) in enumerate(split["vehicles"].items()):
        prior = get_prior(v["class_key"])
        train = [train_obs[t] for t in v["train"]]
        prepared[vid], selections[vid] = {}, {}
        for n in N_GRID:
            prepared[vid][n], selections[vid][str(n)] = [], []
            for r in range(resamples):
                rng = np.random.default_rng([seed, vi, n, r])
                sub = [train[i] for i in np.sort(rng.choice(len(train), size=n, replace=False))]
                selections[vid][str(n)].append([o.trip_id for o in sub])
                y = np.array([o.delta_soc_pct for o in sub])
                posts = {m: calibrate(*priors[vid][m], sub, prior.base, prior.nominal_kwh, rho_rel) for m in METHODS[:2]}
                nominal = np.array([predict_delta_soc(prior.base, prior.nominal_kwh, prior.u_mean, o)[0] for o in sub])
                flat = np.array([100 * o.distance_km / (0.75 * prior.claimed_range_km) for o in sub])
                x = np.stack([_features(o) for o in sub])
                coef, *_ = np.linalg.lstsq(x, y, rcond=None)
                prepared[vid][n].append((posts, {"nominal_spec": _baseline_band(y - nominal) / Z90,
                                                "flat_minus_25": _baseline_band(y - flat) / Z90,
                                                "regression": _baseline_band(y - x @ coef) / Z90}, coef))
    audit = {"protocol": PROTOCOL, "fingerprint": fingerprint, "is_simulated": True,
             "relative_extra_variance": discrepancy, "training_loo": residuals, "training_subsets": selections,
             "settings": {"rho_rel": rho_rel, "resamples": resamples, "seed": seed, "n_draws": n_draws},
             "note": "Written before test observations loaded. Full ten-trip subsets necessarily repeat at k=10."}
    audit_path = out_dir / "e1_training_audit_simulated.json"
    audit_path.write_text(json.dumps(audit, indent=1), encoding="utf-8")
    print("E1: training-only fits/discrepancy frozen; opening held-out observations once", flush=True)
    # FINAL evaluation starts here. No settings, variance estimates or fits depend on these observations.
    test_obs = {tid: load_sim_observation(dataset, tid) for v in split["vehicles"].values() for tid in v["test"]}
    table = {m: {} for m in METHODS}
    for n in N_GRID:
        all_samples = {m: [[] for _ in range(resamples)] for m in METHODS}
        all_medians = {m: [[] for _ in range(resamples)] for m in METHODS}
        targets = []
        for vi, (vid, v) in enumerate(split["vehicles"].items()):
            prior = get_prior(v["class_key"])
            test = [test_obs[t] for t in v["test"]]
            targets.extend(o.delta_soc_pct for o in test)
            for r, (posts, baseline_sds, coef) in enumerate(prepared[vid][n]):
                for mi, method in enumerate(METHODS):
                    rng = np.random.default_rng([seed, vi, n, r, mi, 902])
                    draws, meds = [], []
                    for o in test:
                        if method in posts:
                            s = predictive_samples(posts[method], o, rng, n_draws)
                            med = float(np.median(s))
                            s += rng.normal(size=n_draws) * np.sqrt(discrepancy[method]) * max(abs(med), 1.0)
                        else:
                            med = (predict_delta_soc(prior.base, prior.nominal_kwh, prior.u_mean, o)[0]
                                   if method == "nominal_spec" else
                                   100 * o.distance_km / (0.75 * prior.claimed_range_km)
                                   if method == "flat_minus_25" else float(_features(o) @ coef))
                            s = med + rng.normal(size=n_draws) * baseline_sds[method]
                        draws.append(s)
                        meds.append(med)  # widening affects uncertainty, not the point estimator
                    all_samples[method][r].append(np.stack(draws))
                    all_medians[method][r].extend(meds)
        for method in METHODS:
            samples = [np.concatenate(parts, axis=0) for parts in all_samples[method]]
            table[method][str(n)] = summarise_predictions(samples, np.array(targets), [np.array(v) for v in all_medians[method]])
    return {"experiment": "E1 learning curve", "protocol": PROTOCOL, "fingerprint": fingerprint,
            "training_audit_sha256": hashlib.sha256(audit_path.read_bytes()).hexdigest(),
            "is_simulated": True, "dataset": dataset, "rho_rel": rho_rel, "n_grid": list(N_GRID),
            "resamples": resamples, "seed": seed, "n_draws": n_draws, "methods": table,
            "relative_extra_variance": discrepancy, "test_trips": len(test_obs), "vehicles": len(split["vehicles"]),
            "runtime_s": round(time.time() - t0, 1), "bmw_i3": bmw_i3.status(),
            "notes": ["SIMULATED; no real Indian validation. Experiment-only widening; engine forecasts unchanged.",
                      "Extra whole-trip variance estimated by training leave-one-trip-out residuals minus existing predictive variance.",
                      "MAE averages 20 subset point predictions; k=10 repeats the only possible full-training subset.",
                      "Coverage/Wilson use one mixture prediction per unique test trip, n=36, not 720 independent trials.",
                      "Per-subset coverage/Wilson also reported. Within-vehicle dependence means Wilson is descriptive, not cluster-adjusted.",
                      "Any below_nominal=true is remaining undercoverage. No tuning after this evaluation.",
                      "BMW: NOT DONE (dataset not on this machine)."]}


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
    ax2.set(xlabel="trips used to calibrate the vehicle", ylabel="share inside the 90% mixture band", ylim=(0, 1.02),
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
    parser.add_argument("--resamples", type=int, default=20)
    parser.add_argument("--replot", action="store_true", help="redraw the chart from the saved JSON (no evaluation)")
    parser.add_argument("--reuse-evaluation", action="store_true", help="verify frozen artifact and replot, never reopen test trips")
    args = parser.parse_args()
    out = RESULTS_DIR if args.dataset == "main" else RESULTS_DIR / "dev"
    out.mkdir(parents=True, exist_ok=True)
    stem = "e1_learning_curve_simulated"
    if args.replot:
        plot(json.loads((out / f"{stem}.json").read_text(encoding="utf-8")), out / f"{stem}.png")
        print(f"re-plotted {out / (stem + '.png')} from saved results")
        return
    evaluation = out / "e1_learning_curve_simulated_evaluation.json"
    if args.reuse_evaluation:
        result = json.loads(evaluation.read_text(encoding="utf-8"))
        _, split = load_eval_dataset(args.dataset)
        expected = protocol_fingerprint(args.dataset, split, args.rho, args.resamples, result["seed"], result["n_draws"])
        audit = out / "e1_training_audit_simulated.json"
        if result["fingerprint"] != expected or result["training_audit_sha256"] != hashlib.sha256(audit.read_bytes()).hexdigest():
            raise ValueError("Frozen evaluation does not match code/training/settings; do not silently rerun held-out evaluation")
        print("E1: verified frozen final evaluation; no held-out trips opened", flush=True)
    else:
        result = run_e1(args.dataset, args.rho, args.resamples)
        evaluation.write_text(json.dumps(result, indent=1), encoding="utf-8")
    (out / f"{stem}.json").write_text(json.dumps(result, indent=1), encoding="utf-8")
    plot(result, out / f"{stem}.png")
    print(f"E1 ({args.dataset}, SIMULATED) rho_rel={args.rho} runtime {result['runtime_s']} s; BMW i3: {result['bmw_i3']['status']}")
    for m in METHODS:
        row = "  ".join(f"n={n}: MAE {result['methods'][m][str(n)]['mae_pp']:.2f} cov {result['methods'][m][str(n)]['coverage_90']:.2f}"
                        for n in result["n_grid"] if str(n) in result["methods"][m])
        print(f"{LABELS[m]:38s} {row}")


if __name__ == "__main__":
    main()
