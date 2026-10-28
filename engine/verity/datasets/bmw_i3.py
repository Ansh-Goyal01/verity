"""BMW i3 real-driving dataset (IEEE DataPort 10.21227/6jr9-5235, TU Munich): loader and fixed split.

Read ``docs/BMW_I3_NOTES.md`` first: it documents the files and every data problem found in them.
This is German driving data with a heater (winter) or A/C (summer) load, not Indian driving: say so whenever
a result built on it is shown.

The loader maps each trip to one common table (``COMMON_COLUMNS``) and NEVER cleans it: no rows are dropped, no
values repaired, the source sign convention is kept (battery current is negative while discharging). Problems found
in the data are reported as ``BmwTrip.flags`` and the caller decides what to do with them, the same way for
train and test trips.

The held-out test trips are sacred: ``load_split_part("test")`` refuses unless the caller says it is the final E1
evaluation. The raw files live in ``data/raw/bmw_i3/`` (gitignored, never committed).
"""

from __future__ import annotations

import argparse
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Final

import numpy as np
import pandas as pd

from ..paths import DATA_DIR, SPLITS_DIR
from ..splits import SPLIT_SEED, load_real_split, make_trip_id_split, real_split_path, save_split

DATASET: Final = "bmw_i3"
BMW_DIR: Path = DATA_DIR / "raw" / "bmw_i3"

SEPARATOR: Final = ";"
ENCODING: Final = "latin-1"  # the degree sign in the headers is the single byte 0xB0

# common column -> (regex that must match exactly ONE stripped source header, factor to the common unit)
SIGNALS: Final[dict[str, tuple[str, float]]] = {
    "time_s": (r"Time \[s\]", 1.0),
    "speed_mps": (r"Velocity \[km/h\]\]*", 1.0 / 3.6),  # TripB38's header is misspelled "Velocity [km/h]]]"
    "elevation_m": (r"Elevation \[m\]", 1.0),
    "ambient_c": (r"Ambient Temperature \[.*C\]", 1.0),  # not "Ambient Temperature Sensor" (B trips only)
    "voltage_v": (r"Battery Voltage \[V\]", 1.0),
    "current_a": (r"Battery Current \[A\]", 1.0),
    "soc_pct": (r"SoC \[%\]", 1.0),  # the BMS value; "displayed SoC", "min. SoC" and "max. SoC" are other columns
    "heating_w": (r"Heating Power CAN \[kW\]", 1000.0),  # present in all 70 trips; the LIN [W] twin is in only 50
}
COMMON_COLUMNS: Final = tuple(SIGNALS)

# Thresholds behind the flags. Every number is read off the 70 files (docs/BMW_I3_NOTES.md): approximate - verify.
SOC_TAIL_ROWS: Final = 10  # more trailing NaN SoC rows than this -> "soc_missing_tail" (1-2 trailing NaN rows are routine)
SOC_JUMP_PCT: Final = 2.0  # a larger SoC change between two 0.1 s samples -> "soc_jump" (TripB14 drops 34.6 -> 0.0)
HEATER_PEAK_W: Final = 8000.0  # CAN heating power above this -> "heating_spike" (all trips <= 7.04 kW except TripB02)
MIN_DISTINCT_SOC: Final = 20  # trips with fewer distinct SoC values are not fingerprinted for copies
# Found by comparing all trips before the split existed (find_soc_copies); kept as data so that the flag does not
# depend on which trips happen to be loaded together.
KNOWN_SOC_COPIES: Final = {"TripB07": "TripB03"}

_TRIP_ID = re.compile(r"Trip([A-Z])(\d+)")


@dataclass(frozen=True, eq=False)
class BmwTrip:
    trip_id: str  # "TripA01"
    season: str  # "A" = summer 2019 (A/C), "B" = winter 2019-20 (heater)
    data: pd.DataFrame  # COMMON_COLUMNS, one row per 0.1 s sample
    flags: tuple[str, ...]  # see _flags(); empty means none of the known problems was detected


def season_of(trip_id: str) -> str:
    match = _TRIP_ID.fullmatch(trip_id)
    if match is None:
        raise ValueError(f"not a BMW i3 trip id: {trip_id!r} (expected e.g. 'TripA01')")
    return match.group(1)


def _source_columns(header: list[str], trip_id: str) -> dict[str, int]:
    """Common name -> column POSITION. Matching on the raw header line (not on pandas' names) keeps a duplicated
    header from being silently renamed ``X.1`` and hiding an ambiguity."""
    found: dict[str, int] = {}
    for name, (pattern, _) in SIGNALS.items():
        hits = [i for i, col in enumerate(header) if re.fullmatch(pattern, col.strip())]
        if len(hits) != 1:
            raise ValueError(f"{trip_id}: expected exactly one column for '{name}' (/{pattern}/), "
                             f"found {[header[i] for i in hits]}")
        found[name] = hits[0]
    return found


def _soc_fingerprint(soc: np.ndarray) -> bytes | None:
    """The SoC staircase with repeated values collapsed: two trips with the same one share a SoC channel."""
    valid = soc[~np.isnan(soc)].round(3)
    if valid.size == 0:
        return None
    collapsed = valid[np.concatenate(([True], np.diff(valid) != 0))]
    return collapsed.tobytes() if collapsed.size >= MIN_DISTINCT_SOC else None


def _flags(trip_id: str, data: pd.DataFrame) -> tuple[str, ...]:
    flags: list[str] = []
    soc = data["soc_pct"].to_numpy()
    valid = np.flatnonzero(~np.isnan(soc))
    tail = len(soc) if valid.size == 0 else len(soc) - 1 - int(valid[-1])
    if tail > SOC_TAIL_ROWS:
        flags.append("soc_missing_tail")
    if np.any(np.abs(np.diff(soc[valid])) > SOC_JUMP_PCT):
        flags.append("soc_jump")
    power_w = (data["voltage_v"] * data["current_a"]).to_numpy()
    if np.nansum(power_w[1:] * np.diff(data["time_s"].to_numpy())) > 0:  # net energy INTO the battery
        flags.append("charging_session")
    if bool((data["heating_w"] > HEATER_PEAK_W).any()):
        flags.append("heating_spike")
    if trip_id in KNOWN_SOC_COPIES:
        flags.append(f"soc_copy_of:{KNOWN_SOC_COPIES[trip_id]}")
    return tuple(flags)


def read_trip(path: str | Path) -> BmwTrip:
    """One ``TripXnn.csv`` -> the common table. Raises ``ValueError`` if a signal is missing, ambiguous or non-numeric,
    or if the time axis is not strictly increasing."""
    path = Path(path)
    trip_id = path.stem
    season = season_of(trip_id)
    with path.open("r", encoding=ENCODING, newline="") as handle:
        header = handle.readline().rstrip("\r\n").split(SEPARATOR)
    source = _source_columns(header, trip_id)
    raw = pd.read_csv(path, sep=SEPARATOR, encoding=ENCODING, header=None, skiprows=1, usecols=sorted(source.values()))
    data = pd.DataFrame({name: raw[i].astype("float64") * SIGNALS[name][1] for name, i in source.items()})
    if len(data) < 2 or not np.all(np.diff(data["time_s"].to_numpy()) > 0):
        raise ValueError(f"{trip_id}: the time axis must have at least 2 rows and be strictly increasing")
    return BmwTrip(trip_id, season, data, _flags(trip_id, data))


def find_soc_copies(trips: list[BmwTrip]) -> dict[str, str]:
    """{later trip id: first trip id} for trips whose SoC channel is identical. Used to audit KNOWN_SOC_COPIES."""
    first: dict[bytes, str] = {}
    copies: dict[str, str] = {}
    for trip in sorted(trips, key=lambda t: t.trip_id):
        fingerprint = _soc_fingerprint(trip.data["soc_pct"].to_numpy())
        if fingerprint is None:
            continue
        if fingerprint in first:
            copies[trip.trip_id] = first[fingerprint]
        else:
            first[fingerprint] = trip.trip_id
    return copies


# --- finding and loading trips ---------------------------------------------------------------------------------------


def available() -> bool:
    """True when the dataset folder exists and holds at least one trip file."""
    return BMW_DIR.is_dir() and any(BMW_DIR.glob("Trip*.csv"))


def trip_ids(directory: Path | None = None) -> list[str]:
    """Sorted trip ids from the FILE NAMES only; nothing is opened, so this never inspects a trip."""
    folder = Path(directory) if directory is not None else BMW_DIR
    if not folder.is_dir():
        raise FileNotFoundError(f"{folder} is missing: download the dataset into data/raw/bmw_i3/ (docs/BMW_I3_NOTES.md)")
    return sorted(p.stem for p in folder.glob("Trip*.csv") if _TRIP_ID.fullmatch(p.stem))


def load_trips(ids: list[str], directory: Path | None = None) -> list[BmwTrip]:
    folder = Path(directory) if directory is not None else BMW_DIR
    return [read_trip(folder / f"{trip_id}.csv") for trip_id in ids]


def load_split_part(part: str, *, final_evaluation: bool = False, directory: Path | None = None,
                    split_root: Path = SPLITS_DIR) -> list[BmwTrip]:
    """The train or test trips of the fixed split. ``"test"`` is refused unless this is the final E1 evaluation."""
    if part not in ("train", "test"):
        raise ValueError("part must be 'train' or 'test'")
    if part == "test" and not final_evaluation:
        raise PermissionError("the held-out test trips may only be loaded by the final E1 evaluation "
                              "(pass final_evaluation=True there; never to inspect or tune)")
    return load_trips(load_real_split(DATASET, split_root)[part], directory)


def status() -> dict:
    if not available():
        return {"status": "blocked", "reason": f"{BMW_DIR} is missing or empty; download the dataset (free login)"}
    return {"status": "loader_ready", "n_trips": len(trip_ids()),
            "note": "German driving data with heater / A/C load, not Indian conditions"}


# --- the fixed split -----------------------------------------------------------------------------------------------


def fix_split(directory: Path | None = None, root: Path = SPLITS_DIR, seed: int = SPLIT_SEED,
              test_frac: float = 0.3) -> dict:
    """Write the ONE fixed split (ids only, stratified by season). Refuses to overwrite an existing split."""
    split = make_trip_id_split(trip_ids(directory), season_of, dataset=DATASET, test_frac=test_frac, seed=seed)
    save_split(split, real_split_path(DATASET, root))
    return split


def main() -> None:
    parser = argparse.ArgumentParser(description="BMW i3 dataset: availability, and fixing the held-out split (once)")
    parser.add_argument("--make-split", action="store_true", help="write data/splits/bmw_i3_split.json (refuses to overwrite)")
    args = parser.parse_args()
    if not args.make_split:
        print(status())
        return
    split = fix_split()
    print(f"split fixed for '{DATASET}': {len(split['train'])} train / {len(split['test'])} test trips "
          f"(seed {split['split_seed']}) -> {real_split_path(DATASET)}")


if __name__ == "__main__":
    main()
