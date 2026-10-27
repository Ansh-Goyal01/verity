"""Fixed train / held-out test splits, defined by trip ids only (never by looking at results).

The split is written ONCE, before any model is fitted, and then treated as read-only. Test trips are
evaluated in the final experiment run and never used for tuning or inspection while building.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np

from .paths import SIM_DIR, SPLITS_DIR
from .sim import load_manifest

SPLIT_SEED = 31


def split_path(name: str, root: Path = SPLITS_DIR) -> Path:
    return Path(root) / f"sim_{name}_split.json"


def make_split(manifest: dict, test_frac: float = 0.3, seed: int = SPLIT_SEED) -> dict:
    """Per vehicle, hold out ``round(test_frac * n)`` randomly chosen trips; train keeps time order."""
    rng = np.random.default_rng(seed)
    vehicles = {}
    for veh in manifest["vehicles"]:
        ids = list(veh["trip_ids"])
        n_test = int(round(test_frac * len(ids)))
        test_idx = set(rng.choice(len(ids), size=n_test, replace=False).tolist())
        vehicles[veh["vehicle_id"]] = {
            "class_key": veh["class_key"],
            "train": [t for i, t in enumerate(ids) if i not in test_idx],
            "test": [t for i, t in enumerate(ids) if i in test_idx],
        }
    return {
        "is_simulated": True, "dataset": manifest["name"], "dataset_seed": manifest["seed"],
        "split_seed": seed, "test_frac": test_frac, "vehicles": vehicles,
        "note": "Ids only. Fixed before any fitting. Test trips are never used for tuning or inspection.",
    }


def save_split(split: dict, path: Path, *, overwrite: bool = False) -> None:
    path = Path(path)
    if path.exists() and not overwrite:
        raise FileExistsError(f"{path} already exists; a held-out split is fixed once and not rewritten")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(split, indent=1, sort_keys=True), encoding="utf-8")


def load_split(name: str, root: Path = SPLITS_DIR) -> dict:
    return json.loads(split_path(name, root).read_text(encoding="utf-8"))


DEV_SEED = 777  # the only dataset used for choosing settings


def load_eval_dataset(dataset: str) -> tuple[dict, dict]:
    """(manifest, split) of a simulated dataset. ``dev`` is generated on demand and split in memory only
    (it is for tuning); any other name is read from disk with its fixed, committed held-out split."""
    from .sim import generate_dataset

    if dataset == "dev":
        if not (SIM_DIR / "dev" / "manifest.json").exists():
            generate_dataset("dev", DEV_SEED)
        manifest = load_manifest("dev")
        return manifest, make_split(manifest)
    return load_manifest(dataset), load_split(dataset)


def main() -> None:
    parser = argparse.ArgumentParser(description="Fix the held-out split for a simulated dataset")
    parser.add_argument("--name", default="main")
    parser.add_argument("--seed", type=int, default=SPLIT_SEED)
    parser.add_argument("--test-frac", type=float, default=0.3)
    args = parser.parse_args()
    split = make_split(load_manifest(args.name), args.test_frac, args.seed)
    save_split(split, split_path(args.name))
    n_test = sum(len(v["test"]) for v in split["vehicles"].values())
    n_train = sum(len(v["train"]) for v in split["vehicles"].values())
    print(f"split fixed for '{args.name}': {n_train} train / {n_test} test trips -> {split_path(args.name)}")


if __name__ == "__main__":
    main()
