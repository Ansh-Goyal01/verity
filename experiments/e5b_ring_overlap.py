"""E5b: the attack our first ring detector missed, and what happened when we tried again (SIMULATED, W2.4).

E5's detector needs a ring's reports to fall inside a short burst (a 12 h mean time gap), so a ring that spreads its
posts over ~10 days walks past it (E5: recall 0). E5b drops the time window and keeps only the overlap rule the graph
computes (``ring_edges``: shared chargers, Jaccard overlap, same verdicts), then asks the question honestly: what does
that cost in HONEST REPEAT REPORTERS (drivers who all use the same few chargers and report on them again and again)?

Detectors, all on the same worlds:
  * burst_window      - E5's detector (overlap + 12 h window), unchanged
  * overlap_default   - the same thresholds with no time window
  * overlap_tuned     - no time window, min_shared and min_jaccard chosen on DEV seeds 0-7 only (a small grid)
Evaluation: the same 20 worlds as E5 (seeds 100-119), 100 honest users, a 12-source ring on 30 chargers where present.
Reported whatever they are: ring precision and recall (sources), the share of honest repeat reporters flagged, and the
number of other honest users flagged. The tuned thresholds depend on the simulated ring and group sizes (ring targets
30, repeat targets 6 or 15), which real life will not match: treat that row as an upper bound, not a promise.

    uv run python experiments/e5b_ring_overlap.py
"""

from __future__ import annotations

import argparse
import itertools
import json
import math
import time

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402

from verity.paths import RESULTS_DIR  # noqa: E402
from verity.trust.reports_sim import NOW, WorldConfig, simulate_world  # noqa: E402
from verity.trust.rings import detect_rings  # noqa: E402
from verity.trust.source_trust import ReportSet  # noqa: E402

EVAL_SEEDS = tuple(range(100, 120))
DEV_SEEDS = tuple(range(0, 8))
BASE = dict(n_honest=100, reports_per_honest=(6, 24))
N_REPEAT = 8
SCENARIOS = {
    "evasive ring of 12 (10 days)": dict(ring_size=12, ring_window_h=120.0),
    "evasive ring + 8 repeat reporters on 6 chargers": dict(ring_size=12, ring_window_h=120.0, n_repeat=N_REPEAT, repeat_targets=6),
    "evasive ring + 8 repeat reporters on 15 chargers": dict(ring_size=12, ring_window_h=120.0, n_repeat=N_REPEAT, repeat_targets=15),
    "no ring, 8 repeat reporters on 15 chargers": dict(n_repeat=N_REPEAT, repeat_targets=15),
    "burst ring of 12 (6 h) + 8 repeat reporters on 15 chargers": dict(ring_size=12, n_repeat=N_REPEAT, repeat_targets=15),
}
DEV_SCENARIOS = ("evasive ring + 8 repeat reporters on 6 chargers", "evasive ring + 8 repeat reporters on 15 chargers",
                 "no ring, 8 repeat reporters on 15 chargers")
GRID = list(itertools.product((4, 8, 12, 16, 20), (0.5, 0.7, 0.9)))  # (min_shared, min_jaccard)
DETECTORS = {"burst_window": dict(max_hours=12.0), "overlap_default": dict(max_hours=math.inf)}
LABELS = {"burst_window": "E5 detector (12 h window)", "overlap_default": "Overlap, no window (same thresholds)",
          "overlap_tuned": "Overlap, no window (tuned on dev seeds)"}


def flagged_sources(world, kwargs: dict) -> set[str]:
    rs = ReportSet.from_reports(world.reports, [c.id for c in world.chargers], NOW)
    return set(detect_rings(rs, **kwargs).flagged_ids(rs.source_ids))


def score(world, flagged: set[str]) -> dict:
    ring = set(world.ring_sources)
    repeat = {s for s, k in world.source_kind.items() if k == "honest_repeat"}
    honest_other = {s for s, k in world.source_kind.items() if k == "honest"}
    tp = len(flagged & ring)
    return {"ring_tp": tp, "ring_n": len(ring), "flagged": len(flagged), "repeat_flagged": len(flagged & repeat),
            "repeat_n": len(repeat), "honest_other_flagged": len(flagged & honest_other)}


def evaluate(extra: dict, seeds, kwargs: dict) -> list[dict]:
    return [score(w, flagged_sources(w, kwargs)) for w in (simulate_world(WorldConfig(seed=s, **BASE, **extra)) for s in seeds)]


def dev_objective(kwargs: dict) -> float:
    """Mean over the dev scenarios of F1 (ring present) or 1 - share of repeat reporters flagged (no ring)."""
    vals = []
    for name in DEV_SCENARIOS:
        rows = evaluate(SCENARIOS[name], DEV_SEEDS, kwargs)
        if rows[0]["ring_n"]:
            f1 = []
            for r in rows:
                p = r["ring_tp"] / r["flagged"] if r["flagged"] else 0.0
                rec = r["ring_tp"] / r["ring_n"]
                f1.append(2 * p * rec / (p + rec) if p + rec else 0.0)
            vals.append(float(np.mean(f1)))
        else:
            vals.append(1.0 - float(np.mean([r["repeat_flagged"] / r["repeat_n"] for r in rows])))
    return float(np.mean(vals))


def tune() -> tuple[dict, list[dict]]:
    table = []
    for min_shared, min_jaccard in GRID:
        kw = dict(max_hours=math.inf, min_shared=min_shared, min_jaccard=min_jaccard)
        table.append({"min_shared": min_shared, "min_jaccard": min_jaccard, "dev_score": round(dev_objective(kw), 4)})
    best = max(table, key=lambda r: (r["dev_score"], r["min_shared"], r["min_jaccard"]))  # ties: the stricter setting
    return dict(max_hours=math.inf, min_shared=best["min_shared"], min_jaccard=best["min_jaccard"]), table


def summarise(rows: list[dict]) -> dict:
    ring_rows = [r for r in rows if r["ring_n"]]
    precision = [r["ring_tp"] / r["flagged"] for r in ring_rows if r["flagged"]]
    return {
        "ring_recall": round(float(np.mean([r["ring_tp"] / r["ring_n"] for r in ring_rows])), 3) if ring_rows else None,
        "ring_precision": round(float(np.mean(precision)), 3) if precision else None,
        "worlds_with_any_flag": sum(r["flagged"] > 0 for r in rows),
        "repeat_reporters_flagged_share": (round(float(np.mean([r["repeat_flagged"] / r["repeat_n"] for r in rows])), 3)
                                           if rows and rows[0]["repeat_n"] else None),
        "other_honest_flagged_mean": round(float(np.mean([r["honest_other_flagged"] for r in rows])), 2),
    }


def run_e5b() -> dict:
    t0 = time.time()
    tuned, table = tune()
    detectors = {**DETECTORS, "overlap_tuned": tuned}
    scenarios = {name: {d: summarise(evaluate(extra, EVAL_SEEDS, kw)) for d, kw in detectors.items()}
                 for name, extra in SCENARIOS.items()}
    return {"experiment": "E5b ring detection without a time window", "is_simulated": True,
            "eval_seeds": [EVAL_SEEDS[0], EVAL_SEEDS[-1]], "dev_seeds": [DEV_SEEDS[0], DEV_SEEDS[-1]],
            "worlds_per_scenario": len(EVAL_SEEDS), "world": {**BASE, "repeat_reporters": N_REPEAT},
            "tuned_thresholds": {"min_shared": tuned["min_shared"], "min_jaccard": tuned["min_jaccard"], "min_agree": 0.9},
            "dev_grid": table, "scenarios": scenarios, "runtime_s": round(time.time() - t0, 1),
            "notes": ["All reports, rings and repeat reporters are SIMULATED (verity.trust.reports_sim).",
                      "Thresholds were chosen on dev seeds 0-7 and evaluated on seeds 100-119.",
                      "The tuned thresholds are fitted to the simulated sizes (ring 30 targets, repeat groups 6 or 15) "
                      "and need not transfer: an upper bound.",
                      "Precision is None where nothing was flagged in any world."]}


def plot(result: dict, path) -> None:
    names = list(result["scenarios"])
    x = np.arange(len(names))
    fig, axes = plt.subplots(1, 2, figsize=(12, 4.8), sharex=True)
    for i, d in enumerate(LABELS):
        rec = [result["scenarios"][s][d]["ring_recall"] or 0.0 for s in names]
        rep = [result["scenarios"][s][d]["repeat_reporters_flagged_share"] or 0.0 for s in names]
        axes[0].bar(x + (i - 1) * 0.27, rec, 0.27, label=LABELS[d])
        axes[1].bar(x + (i - 1) * 0.27, rep, 0.27, label=LABELS[d])
    for ax, title in zip(axes, ("share of the ring caught (recall)", "share of honest repeat reporters wrongly flagged")):
        ax.set_xticks(x, [n.replace(" + ", "\n+ ").replace(", ", ",\n") for n in names], fontsize=6)
        ax.set(title=title, ylim=(0, 1.05))
    axes[0].legend(fontsize=7)
    fig.suptitle("E5b: ring detection without a time window (simulated)", fontsize=11)
    fig.text(0.5, 0.005, f"SIMULATED reports: {result['worlds_per_scenario']} worlds per scenario (seeds "
             f"{result['eval_seeds'][0]}-{result['eval_seeds'][1]}); thresholds tuned on seeds "
             f"{result['dev_seeds'][0]}-{result['dev_seeds'][1]}.", ha="center", fontsize=7)
    fig.tight_layout(rect=(0, 0.02, 1, 0.95))
    fig.savefig(path, dpi=140)
    plt.close(fig)


def main() -> None:
    parser = argparse.ArgumentParser(description="E5b ring overlap without a time window (simulated)")
    parser.add_argument("--replot", action="store_true")
    args = parser.parse_args()
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    stem = "e5b_ring_overlap_simulated"
    if args.replot:
        plot(json.loads((RESULTS_DIR / f"{stem}.json").read_text(encoding="utf-8")), RESULTS_DIR / f"{stem}.png")
        return
    result = run_e5b()
    (RESULTS_DIR / f"{stem}.json").write_text(json.dumps(result, indent=1), encoding="utf-8")
    plot(result, RESULTS_DIR / f"{stem}.png")
    print(f"E5b (SIMULATED) runtime {result['runtime_s']} s; tuned thresholds {result['tuned_thresholds']}")
    for name, per in result["scenarios"].items():
        print(name)
        for d, s in per.items():
            print(f"   {LABELS[d]:42s} recall {s['ring_recall']} precision {s['ring_precision']} | repeat reporters flagged "
                  f"{s['repeat_reporters_flagged_share']} | other honest flagged {s['other_honest_flagged_mean']}")


if __name__ == "__main__":
    main()
