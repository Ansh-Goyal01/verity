"""E5: does the trust engine recover true charger reliability from noisy and partly fake reports? (SIMULATED)

Worlds come from verity.trust.reports_sim: 120 chargers with known reliabilities (simulation parameters),
100 noisy honest users, and optionally a lying operator feed and a coordinated fake-report ring that
posts "broken" 1-star reports on one rival network inside a six-hour burst. Reports and usage traces are
fed through four estimators:

* avg_rating   - mean star rating per charger (the baseline of the plan's E5)
* ds           - Dawid-Skene source trust only
* ds_usage     - + usage traces
* full         - + fake-report ring detection (the Verity trust engine)

Metrics: mean absolute error of the recovered reliability against the known truth (all chargers, and
only the chargers the ring attacks); ring-detection precision and recall. Seeds 100-119 (settings were
chosen on seeds 0-7). Honest caveat: each report is a single noisy sample of an intermittent charger, so
~9 reports per charger leave an error floor near 0.15 for every method.
"""

from __future__ import annotations

import argparse
import json
import time

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402

from verity.paths import RESULTS_DIR  # noqa: E402
from verity.trust.engine import TrustEngine  # noqa: E402
from verity.trust.reports_sim import NOW, WorldConfig, simulate_world  # noqa: E402
from verity.trust.source_trust import ReportSet, average_rating_baseline  # noqa: E402

SEEDS = tuple(range(100, 120))
SCENARIOS = {
    "clean": {},
    "lying operator feed": {"operator_feed": True},
    "ring of 5": {"ring_size": 5},
    "ring of 12": {"ring_size": 12},
    "ring of 12 + lying feed": {"ring_size": 12, "operator_feed": True},
    # an adversary who knows the detector: reports spread over ~10 days instead of one burst
    "evasive ring of 12 (10 days)": {"ring_size": 12, "ring_window_h": 120.0},
}
METHODS = ("avg_rating", "ds", "ds_usage", "full")
LABELS = {"avg_rating": "Average star rating", "ds": "Dawid-Skene source trust",
          "ds_usage": "+ usage traces", "full": "+ ring detection (Verity)"}
BASE = dict(n_honest=100, reports_per_honest=(6, 24))


def _estimates(world, rs) -> dict[str, np.ndarray]:
    ids = [c.id for c in world.chargers]
    out = {"avg_rating": average_rating_baseline(rs, world.stars, 0.5)}
    for name, kw in (("ds", dict(use_rings=False, use_usage=False)), ("ds_usage", dict(use_rings=False)), ("full", {})):
        rep = TrustEngine(world.chargers, **kw).estimate(world.reports, NOW, world.usage)
        out[name] = np.array([rep.chargers[i].p_works for i in ids])
        if name == "full":
            out["_flagged"] = set(rep.flagged_sources)
    return out


def run_e5() -> dict:
    t0 = time.time()
    scenarios = {}
    for name, extra in SCENARIOS.items():
        mae = {m: [] for m in METHODS}
        mae_attacked = {m: [] for m in METHODS}
        precision, recall = [], []
        for seed in SEEDS:
            world = simulate_world(WorldConfig(seed=seed, **BASE, **extra))
            rs = ReportSet.from_reports(world.reports, [c.id for c in world.chargers], NOW)
            est = _estimates(world, rs)
            attacked = np.array([c.id in world.ring_targets for c in world.chargers])
            for m in METHODS:
                err = np.abs(est[m] - world.true_r)
                mae[m].append(float(err.mean()))
                if attacked.any():
                    mae_attacked[m].append(float(err[attacked].mean()))
            if world.ring_sources:
                flagged = est["_flagged"]
                tp = len(flagged & world.ring_sources)
                if flagged:  # precision is undefined (not 1.0) when nothing is flagged
                    precision.append(tp / len(flagged))
                recall.append(tp / len(world.ring_sources))
        scenarios[name] = {
            "mae": {m: {"mean": round(float(np.mean(v)), 4), "sd": round(float(np.std(v)), 4)} for m, v in mae.items()},
            "mae_on_attacked_chargers": ({m: round(float(np.mean(v)), 4) for m, v in mae_attacked.items()}
                                         if mae_attacked["full"] else None),
            "ring_detection": ({"precision": round(float(np.mean(precision)), 3) if precision else None,
                                "recall": round(float(np.mean(recall)), 3), "worlds_with_any_flag": len(precision)}
                               if recall else None),
        }
    return {"experiment": "E5 trust recovery", "is_simulated": True, "seeds": [SEEDS[0], SEEDS[-1]],
            "worlds_per_scenario": len(SEEDS), "chargers_per_world": WorldConfig().n_chargers,
            "world": {**{k: v for k, v in BASE.items()}}, "scenarios": scenarios, "runtime_s": round(time.time() - t0, 1),
            "notes": ["All reports, reliabilities and attacks are SIMULATED (verity.trust.reports_sim).",
                      "Each report is one noisy sample of an intermittent charger: a ~0.15 error floor applies to every method."]}


def plot(result: dict, path) -> None:
    names = list(result["scenarios"])
    x = np.arange(len(names))
    width = 0.2
    fig, ax = plt.subplots(figsize=(10, 4.6))
    for i, m in enumerate(METHODS):
        means = [result["scenarios"][s]["mae"][m]["mean"] for s in names]
        sds = [result["scenarios"][s]["mae"][m]["sd"] for s in names]
        ax.bar(x + (i - 1.5) * width, means, width, yerr=sds, capsize=2, label=LABELS[m])
    ax.set_xticks(x, [n.replace(" + ", "\n+ ") for n in names], fontsize=8)
    ax.set(ylabel="mean absolute error of recovered reliability", title="E5: recovering charger reliability from noisy and fake reports (simulated)")
    ax.legend(fontsize=8)
    fig.text(0.5, 0.005, f"SIMULATED reports: {result['worlds_per_scenario']} worlds per scenario (seeds {result['seeds'][0]}-{result['seeds'][1]}), "
             "bars = mean, whiskers = sd across worlds.", ha="center", fontsize=7)
    fig.tight_layout(rect=(0, 0.02, 1, 1))
    fig.savefig(path, dpi=140)
    plt.close(fig)


def main() -> None:
    parser = argparse.ArgumentParser(description="E5 trust recovery (simulated)")
    parser.add_argument("--replot", action="store_true")
    args = parser.parse_args()
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    stem = "e5_trust_recovery_simulated"
    if args.replot:
        plot(json.loads((RESULTS_DIR / f"{stem}.json").read_text(encoding="utf-8")), RESULTS_DIR / f"{stem}.png")
        return
    result = run_e5()
    (RESULTS_DIR / f"{stem}.json").write_text(json.dumps(result, indent=1), encoding="utf-8")
    plot(result, RESULTS_DIR / f"{stem}.png")
    print(f"E5 (SIMULATED) runtime {result['runtime_s']} s")
    for name, s in result["scenarios"].items():
        row = " | ".join(f"{LABELS[m]} {s['mae'][m]['mean']:.3f}" for m in METHODS)
        print(f"{name:26s} {row}")
        if s["mae_on_attacked_chargers"]:
            print(f"{'':26s} on attacked chargers: " + " | ".join(f"{m} {v:.3f}" for m, v in s["mae_on_attacked_chargers"].items()))
        if s["ring_detection"]:
            print(f"{'':26s} ring detection: {s['ring_detection']}")


if __name__ == "__main__":
    main()
