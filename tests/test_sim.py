"""The truth simulator is reproducible, contract-compliant, labelled, and does not leak its truth."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from verity.schemas import TripLog
from verity.sim import generate_dataset, load_manifest, load_trip_files, load_truth

FLEET = (("car-1", "compact_esuv"), ("scooter-1", "e_scooter", 0.8))
SEED = 4242


@pytest.fixture(scope="module")
def dataset(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, dict]:
    root = tmp_path_factory.mktemp("sim")
    manifest = generate_dataset("t", SEED, out_root=root, fleet=FLEET, trips_per_vehicle=3)
    return root, manifest


def _tree_bytes(root: Path) -> dict[str, bytes]:
    return {str(p.relative_to(root)): p.read_bytes() for p in sorted(root.rglob("*.json"))}


def test_same_seed_reproduces_identical_files(dataset: tuple[Path, dict], tmp_path: Path) -> None:
    root, _ = dataset
    generate_dataset("t", SEED, out_root=tmp_path, fleet=FLEET, trips_per_vehicle=3)
    assert _tree_bytes(root / "t") == _tree_bytes(tmp_path / "t")


def test_different_seed_changes_the_data(dataset: tuple[Path, dict], tmp_path: Path) -> None:
    root, manifest = dataset
    other = generate_dataset("t", SEED + 1, out_root=tmp_path, fleet=FLEET, trips_per_vehicle=3)
    first_a, first_b = manifest["trips"][0], other["trips"][0]
    assert first_a["distance_km"] != first_b["distance_km"]


def test_every_file_is_labelled_simulated_and_trips_honour_the_contract(dataset: tuple[Path, dict]) -> None:
    root, manifest = dataset
    assert manifest["is_simulated"] is True and len(manifest["trips"]) == 6
    for meta in manifest["trips"]:
        trip, context = load_trip_files("t", meta["trip_id"], root)
        truth = load_truth("t", meta["trip_id"], root)
        assert isinstance(trip, TripLog) and trip.is_simulated is True
        assert context["is_simulated"] is True and truth["is_simulated"] is True


def test_trip_files_do_not_leak_hidden_truth(dataset: tuple[Path, dict]) -> None:
    root, manifest = dataset
    tid = manifest["trips"][0]["trip_id"]
    trip_text = (root / "t" / "trips" / f"{tid}.json").read_text(encoding="utf-8")
    ctx_text = (root / "t" / "context" / f"{tid}.json").read_text(encoding="utf-8")
    for hidden in ("soh", "usable_kwh_true", "cda_m2", "mass_true_kg", "gust_rms", "loss_beta", "driver_g"):
        assert hidden not in trip_text and hidden not in ctx_text


def test_logged_trips_look_like_phone_logs(dataset: tuple[Path, dict]) -> None:
    root, manifest = dataset
    for meta in manifest["trips"]:
        trip, _ = load_trip_files("t", meta["trip_id"], root)
        times = [p.t for p in trip.points]
        assert times == sorted(times)
        gaps = np.diff([t.timestamp() for t in times])
        assert gaps[:-1].max() == pytest.approx(10.0)  # GPS every 10 s (last gap may be shorter)
        assert all(r.pct == int(r.pct) for r in trip.soc)  # dashboards show whole percent
        assert trip.start_soc_pct > trip.end_soc_pct
        assert 1 <= trip.start_soc_pct - trip.end_soc_pct <= 90
        assert trip.soc[0].t == trip.started_at and trip.soc[-1].t == trip.ended_at


def test_truth_energy_per_km_is_physically_plausible(dataset: tuple[Path, dict]) -> None:
    root, manifest = dataset
    bounds = {"compact_esuv": (60.0, 400.0), "e_scooter": (8.0, 100.0)}
    for meta in manifest["trips"]:
        truth = load_truth("t", meta["trip_id"], root)
        t, v = truth["trip"], truth["vehicle"]
        used_kwh = (t["soc_start_true"] - t["soc_end_true"]) / 100.0 * t["capacity_kwh"]
        wh_per_km = 1000.0 * used_kwh / t["distance_km"]
        low, high = bounds[v["class_key"]]
        assert low < wh_per_km < high, (v["class_key"], wh_per_km)


def test_forced_state_of_health_is_applied(dataset: tuple[Path, dict]) -> None:
    root, manifest = dataset
    scooter_trip = next(m for m in manifest["trips"] if m["vehicle_id"] == "scooter-1")
    v = load_truth("t", scooter_trip["trip_id"], root)["vehicle"]
    assert v["soh"] == pytest.approx(0.8)
    assert v["usable_kwh_true"] < 0.8 * v["nominal_kwh"]


def test_manifest_round_trips_and_lists_every_trip_once(dataset: tuple[Path, dict]) -> None:
    root, manifest = dataset
    assert load_manifest("t", root) == json.loads(json.dumps(manifest))
    ids = [m["trip_id"] for m in manifest["trips"]]
    assert len(ids) == len(set(ids)) == sum(len(v["trip_ids"]) for v in manifest["vehicles"])
