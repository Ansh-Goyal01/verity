"""The held-out split is deterministic, disjoint, fixed once, and consistent with the committed data."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from verity import paths
from verity.datasets import bmw_i3
from verity.splits import load_split, make_split, save_split, split_path

MANIFEST = {
    "name": "t", "seed": 1,
    "vehicles": [
        {"vehicle_id": f"v{i}", "class_key": "compact_esuv", "trip_ids": [f"v{i}-t{j}" for j in range(14)]}
        for i in range(3)
    ],
}


def test_split_is_disjoint_complete_and_stratified_per_vehicle() -> None:
    split = make_split(MANIFEST)
    for veh in MANIFEST["vehicles"]:
        part = split["vehicles"][veh["vehicle_id"]]
        assert set(part["train"]).isdisjoint(part["test"])
        assert sorted(part["train"] + part["test"]) == sorted(veh["trip_ids"])
        assert len(part["test"]) == 4  # round(0.3 * 14)
        assert part["train"] == [t for t in veh["trip_ids"] if t in part["train"]]  # time order kept
    assert split["is_simulated"] is True


def test_split_is_deterministic_and_seed_dependent() -> None:
    assert make_split(MANIFEST, seed=5) == make_split(MANIFEST, seed=5)
    assert make_split(MANIFEST, seed=5) != make_split(MANIFEST, seed=6)


def test_a_fixed_split_is_never_silently_rewritten(tmp_path: Path) -> None:
    path = split_path("t", tmp_path)
    save_split(make_split(MANIFEST), path)
    with pytest.raises(FileExistsError):
        save_split(make_split(MANIFEST, seed=99), path)
    assert load_split("t", tmp_path) == make_split(MANIFEST)


def test_committed_main_split_matches_the_committed_dataset() -> None:
    manifest_path = paths.SIM_DIR / "main" / "manifest.json"
    if not manifest_path.exists():
        pytest.skip("data/sim/main not generated")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    split = load_split("main")
    in_split = {t for v in split["vehicles"].values() for t in v["train"] + v["test"]}
    assert in_split == {t["trip_id"] for t in manifest["trips"]}
    n_test = sum(len(v["test"]) for v in split["vehicles"].values())
    assert 0.25 <= n_test / len(in_split) <= 0.35


def test_bmw_i3_reports_blocked_when_the_dataset_is_absent(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(bmw_i3, "BMW_DIR", tmp_path / "missing")
    assert bmw_i3.available() is False
    assert bmw_i3.status()["status"] == "blocked"
    with pytest.raises(FileNotFoundError):
        bmw_i3.trip_ids()
