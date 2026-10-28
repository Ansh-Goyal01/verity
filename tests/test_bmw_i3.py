"""BMW i3 loader and fixed split, tested on tiny SYNTHETIC fixtures that copy the real column layouts.

The raw dataset is never needed here (it is gitignored). The one test that touches it loads a single TRAIN trip and
skips when the data is absent; no test ever loads a held-out test trip.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from verity import paths
from verity.datasets import bmw_i3
from verity.datasets.bmw_i3 import COMMON_COLUMNS, find_soc_copies, read_trip, season_of
from verity.splits import SPLIT_SEED, load_real_split, make_trip_id_split, real_split_path, save_split

FIXTURES = Path(__file__).parent / "fixtures" / "bmw_i3"
FIXTURE_IDS = ["TripA01", "TripB01", "TripB02", "TripB03", "TripB04", "TripB07", "TripB14"]
REAL_IDS = [f"TripA{i:02d}" for i in range(1, 33)] + [f"TripB{i:02d}" for i in range(1, 39)]  # 32 + 38 = 70 trips


def fixture(name: str) -> Path:
    return FIXTURES / f"{name}.csv"


# --- mapping to the common format ------------------------------------------------------------------------------------


def test_summer_layout_maps_to_the_common_columns_and_units() -> None:
    trip = read_trip(fixture("TripA01"))
    d = trip.data
    assert trip.trip_id == "TripA01" and trip.season == "A" and trip.flags == ()
    assert tuple(d.columns) == COMMON_COLUMNS and len(d) == 12
    assert np.allclose(np.diff(d["time_s"]), 0.1)
    assert d["speed_mps"].iloc[5] == pytest.approx(10.0)  # 36 km/h
    assert (d["elevation_m"].iloc[0], d["ambient_c"].iloc[0], d["voltage_v"].iloc[0]) == (500.0, 20.0, 390.0)
    assert d["current_a"].iloc[0] == -20.0  # the source sign is kept: discharge is negative
    assert d["soc_pct"].iloc[0] == 80.0 and (d["heating_w"] == 0.0).all()


def test_heating_power_is_converted_from_kw_to_watts() -> None:
    assert (read_trip(fixture("TripB01")).data["heating_w"] == 3500.0).all()


def test_the_misspelled_velocity_header_of_tripb38_is_still_read() -> None:
    assert read_trip(fixture("TripB02")).data["speed_mps"].iloc[5] == pytest.approx(10.0)


def test_every_real_layout_gives_the_same_float_columns() -> None:
    frames = [read_trip(fixture(t)).data for t in FIXTURE_IDS]
    assert all(tuple(f.columns) == COMMON_COLUMNS for f in frames)
    assert all((f.dtypes == "float64").all() for f in frames)


def edited(tmp_path: Path, old: bytes, new: bytes, name: str = "TripA01") -> Path:
    raw = fixture(name).read_bytes()
    assert old in raw
    path = tmp_path / f"{name}.csv"
    path.write_bytes(raw.replace(old, new, 1))
    return path


def test_a_missing_signal_fails_loudly_and_names_it(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="voltage_v"):
        read_trip(edited(tmp_path, b"Battery Voltage [V]", b"Voltage"))


def test_an_ambiguous_signal_fails_loudly(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="exactly one column for 'ambient_c'"):
        read_trip(edited(tmp_path, b"Cabin Temperature Sensor [", b"Ambient Temperature ["))


def test_a_non_increasing_time_axis_fails_loudly(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="strictly increasing"):
        read_trip(edited(tmp_path, b"\r\n0.2;", b"\r\n0.0;"))


def test_a_file_that_is_not_a_trip_id_is_refused() -> None:
    with pytest.raises(ValueError, match="not a BMW i3 trip id"):
        season_of("notes")


# --- flags: problems are reported, never repaired --------------------------------------------------------------------


@pytest.mark.parametrize(("trip_id", "flags"), [
    ("TripA01", ()),
    ("TripB01", ()),
    ("TripB03", ()),
    ("TripB02", ("soc_missing_tail", "heating_spike")),  # 12 empty SoC rows at the end; 9 kW heating
    ("TripB04", ("charging_session",)),  # net energy flows into the battery
    ("TripB14", ("soc_jump",)),  # 34.6 -> 0.0 in the last row
    ("TripB07", ("soc_copy_of:TripB03",)),  # known copied SoC channel
])
def test_known_problems_are_flagged_and_clean_trips_are_not(trip_id: str, flags: tuple[str, ...]) -> None:
    assert read_trip(fixture(trip_id)).flags == flags


def test_flagged_trips_are_not_modified() -> None:
    d = read_trip(fixture("TripB14")).data
    assert len(d) == 12 and d["soc_pct"].iloc[-1] == 0.0  # the glitch row is still there
    assert read_trip(fixture("TripB02")).data["soc_pct"].isna().sum() == 12  # NaN stays NaN


def test_the_audit_finds_the_copied_soc_channel_and_agrees_with_the_known_table() -> None:
    trips = bmw_i3.load_trips(FIXTURE_IDS, FIXTURES)
    assert find_soc_copies(trips) == {"TripB07": "TripB03"} == bmw_i3.KNOWN_SOC_COPIES


# --- finding trips, availability, the held-out guard -----------------------------------------------------------------


def test_trip_ids_come_from_file_names_and_a_missing_folder_is_an_error(tmp_path: Path) -> None:
    assert bmw_i3.trip_ids(FIXTURES) == FIXTURE_IDS
    with pytest.raises(FileNotFoundError, match="download the dataset"):
        bmw_i3.trip_ids(tmp_path / "missing")


def test_status_reports_loader_ready_when_the_folder_has_trips(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(bmw_i3, "BMW_DIR", FIXTURES)
    assert bmw_i3.available() and bmw_i3.status()["status"] == "loader_ready" and bmw_i3.status()["n_trips"] == 7
    monkeypatch.setattr(bmw_i3, "BMW_DIR", tmp_path)
    assert not bmw_i3.available() and bmw_i3.status()["status"] == "blocked"


def test_test_trips_are_refused_unless_it_is_the_final_evaluation(tmp_path: Path) -> None:
    split = make_trip_id_split(FIXTURE_IDS, season_of, dataset="bmw_i3")
    save_split(split, real_split_path("bmw_i3", tmp_path))
    with pytest.raises(PermissionError, match="final E1 evaluation"):
        bmw_i3.load_split_part("test", directory=FIXTURES, split_root=tmp_path)
    train = bmw_i3.load_split_part("train", directory=FIXTURES, split_root=tmp_path)
    assert [t.trip_id for t in train] == split["train"]
    final = bmw_i3.load_split_part("test", final_evaluation=True, directory=FIXTURES, split_root=tmp_path)
    assert [t.trip_id for t in final] == split["test"]
    with pytest.raises(ValueError, match="'train' or 'test'"):
        bmw_i3.load_split_part("validation", directory=FIXTURES, split_root=tmp_path)


# --- the split function ---------------------------------------------------------------------------------------------------


def test_split_is_stratified_by_season_disjoint_complete_and_deterministic() -> None:
    split = make_trip_id_split(REAL_IDS, season_of, dataset="bmw_i3")
    assert (len(split["strata"]["A"]["test"]), len(split["strata"]["B"]["test"])) == (10, 11)  # round(0.3 * 32), round(0.3 * 38)
    assert len(split["test"]) == 21 and len(split["train"]) == 49
    assert set(split["train"]).isdisjoint(split["test"]) and sorted(split["train"] + split["test"]) == REAL_IDS
    assert split["is_simulated"] is False and split["split_seed"] == SPLIT_SEED
    assert split == make_trip_id_split(list(reversed(REAL_IDS)), season_of, dataset="bmw_i3")  # depends on the id SET only
    assert split != make_trip_id_split(REAL_IDS, season_of, dataset="bmw_i3", seed=SPLIT_SEED + 1)


def test_split_rejects_duplicate_ids_and_a_silly_fraction() -> None:
    with pytest.raises(ValueError, match="unique"):
        make_trip_id_split(["TripA01", "TripA01"], season_of, dataset="x")
    with pytest.raises(ValueError, match="between 0 and 1"):
        make_trip_id_split(REAL_IDS, season_of, dataset="x", test_frac=1.0)


def test_fixing_the_split_works_once_and_never_overwrites(tmp_path: Path) -> None:
    split = bmw_i3.fix_split(FIXTURES, tmp_path)
    assert load_real_split("bmw_i3", tmp_path) == split
    with pytest.raises(FileExistsError):
        bmw_i3.fix_split(FIXTURES, tmp_path, seed=99)


# --- the committed split ---------------------------------------------------------------------------------------------------


def test_the_committed_split_is_the_fixed_one_and_regenerates_from_its_own_seed() -> None:
    assert real_split_path("bmw_i3").exists(), "data/splits/bmw_i3_split.json must be committed (never re-split)"
    split = load_real_split("bmw_i3")
    assert sorted(split["train"] + split["test"]) == REAL_IDS and set(split["train"]).isdisjoint(split["test"])
    assert len(split["test"]) == 21 and split["split_seed"] == SPLIT_SEED and split["is_simulated"] is False
    again = make_trip_id_split(REAL_IDS, season_of, dataset="bmw_i3", test_frac=split["test_frac"], seed=split["split_seed"])
    assert again == split, "the committed split no longer matches make_trip_id_split: do NOT re-split; find what changed"


def test_one_real_train_trip_loads_when_the_dataset_is_present() -> None:
    if not bmw_i3.available():
        pytest.skip("data/raw/bmw_i3 is not on this machine")
    split = load_real_split("bmw_i3")
    (trip,) = bmw_i3.load_trips(split["train"][:1])  # TRAIN only: a test trip is never opened here
    assert tuple(trip.data.columns) == COMMON_COLUMNS and len(trip.data) > 100
    assert not trip.data[["time_s", "speed_mps", "voltage_v", "current_a"]].isna().any().any()
    assert paths.DATA_DIR in bmw_i3.BMW_DIR.parents
