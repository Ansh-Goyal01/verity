"""The real Indian cars a driver can pick (WP2, docs/FINALE_BUILD.md), from data/corpus/vehicles_india.csv.

Each car is planned with the physics model's class prior for its battery size, with its OWN battery size and (when
the spec row has it) its own kerb mass. When at least ``india_eval.MIN_CALIBRATION_RECORDS`` verified TRAINING-split
public trip reports exist for it (data/processed/trips_india_enriched.json), the prior is calibrated on them exactly
as experiment E1-India does. Held-out rows never calibrate anything: a record is used only when the fixed split rule
puts its source in "train" AND the frozen split file (if it lists that source) does not say "test".
Otherwise the car is "prior only" (WP2 option B, docs/DECISIONS.md).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from verity import india_eval as ie
from verity.calibrate import DEFAULT_RHO_REL, Posterior
from verity.corpus import VEHICLES, Row, read_csv, usable_rows
from verity.dispatch import VehicleContext
from verity.paths import CORPUS_DIR, PROCESSED_DIR, SPLITS_DIR
from verity.vehicles import get_prior

ENRICHED_FILE = PROCESSED_DIR / "trips_india_enriched.json"
SPLIT_FILE = SPLITS_DIR / "india_trips_split.json"
BASIS_TRIPS, BASIS_PRIOR = "public trips", "class prior"
URL_COLUMNS = ("battery_source_url", "curb_mass_source_url", "range_source_url", "motor_source_url", "hint_source_url")


@dataclass(frozen=True, eq=False)
class CatalogCar:
    vehicle_id: str
    make: str
    model: str
    variant: str
    battery_kwh: float
    claimed_range_km: float | None
    range_test_cycle: str | None
    source_quality: str
    approximate: bool
    source_count: int
    class_key: str
    curb_mass_kg: float
    curb_mass_basis: str  # "spec sheet" | "class typical"
    post: Posterior
    trips_calibrated: int

    @property
    def name(self) -> str:
        return f"{self.make} {self.model} ({self.variant})"

    @property
    def calibration_basis(self) -> str:
        return BASIS_TRIPS if self.trips_calibrated else BASIS_PRIOR

    @property
    def calibration_label(self) -> str:
        if self.trips_calibrated:
            return f"Calibrated on {self.trips_calibrated} public trip reports"
        return "First trip in this car: prior only"


def _float(text: str) -> float | None:
    return float(text) if text and text.strip() else None


def spec_curb_mass(row: Row) -> float | None:
    lo, hi = _float(row.get("curb_mass_kg_low", "")), _float(row.get("curb_mass_kg_high", ""))
    return None if lo is None or hi is None else 0.5 * (lo + hi)


def held_out_groups(split_path: Path) -> set[str]:
    """Sources the frozen split file assigns to the held-out set (empty when the file lists none)."""
    if not split_path.exists():
        return set()
    doc = json.loads(split_path.read_text(encoding="utf-8"))
    assignments = doc.get("assignments") or {}
    return {g for g, s in assignments.items() if s == "test"} if isinstance(assignments, dict) else set()


def is_training(case: ie.Case, held_out: set[str]) -> bool:
    return ie.assign_group(case.group) == "train" and case.group not in held_out


def training_cases(enriched_path: Path, vehicles: dict[str, Row], split_path: Path) -> dict[str, list[ie.Case]]:
    """Verified, evaluable, TRAINING-split trip reports per vehicle id (never a held-out one)."""
    doc = json.loads(enriched_path.read_text(encoding="utf-8")) if enriched_path.exists() else {"records": []}
    held_out = held_out_groups(split_path)
    out: dict[str, list[ie.Case]] = {}
    for record in doc.get("records", []):
        case, _ = ie.case_from_record(record, vehicles)
        if case is not None and is_training(case, held_out):
            out.setdefault(case.vehicle_id, []).append(case)
    return out


def _prior_post(class_key: str, nominal_kwh: float) -> Posterior:
    prior = get_prior(class_key)
    return Posterior(prior.u_mean.copy(), prior.u_cov, 0, DEFAULT_RHO_REL, prior.base, nominal_kwh)


def car_from_row(row: Row, cases: list[ie.Case]) -> CatalogCar:
    class_key = row["engine_prior_class"]
    kwh = float(row["battery_nominal_kwh"])
    mass = spec_curb_mass(row)
    calibrated = ie.calibrated_posterior(cases) if cases else None
    n_used = len([c for c in cases if c.delta_soc is not None and c.record_type in ("soc_trip", "range_test")])
    return CatalogCar(
        vehicle_id=row["vehicle_id"], make=row["make"], model=row["model"], variant=row["variant"], battery_kwh=kwh,
        claimed_range_km=_float(row.get("claimed_range_km", "")), range_test_cycle=row.get("range_test_cycle") or None,
        source_quality=row["source_quality"], approximate="approximate - verify" in row.get("notes", ""),
        source_count=len({row[c] for c in URL_COLUMNS if row.get(c)}), class_key=class_key,
        curb_mass_kg=mass if mass is not None else float(get_prior(class_key).base.curb_mass_kg),
        curb_mass_basis="spec sheet" if mass is not None else "class typical",
        post=calibrated if calibrated is not None else _prior_post(class_key, kwh),
        trips_calibrated=n_used if calibrated is not None else 0)


def load_catalog(corpus_dir: Path = CORPUS_DIR, enriched_path: Path = ENRICHED_FILE,
                 split_path: Path = SPLIT_FILE) -> dict[str, CatalogCar]:
    """Every usable vehicles_india.csv row as a plannable car, keyed by vehicle_id (file order)."""
    header, rows = read_csv(corpus_dir / VEHICLES.filename)
    rows = usable_rows(VEHICLES, rows)
    by_id = {r["vehicle_id"]: r for r in rows}
    cases = training_cases(enriched_path, by_id, split_path)
    return {vid: car_from_row(row, cases.get(vid, [])) for vid, row in by_id.items()}


def vehicle_context(car: CatalogCar, passengers: int, ac_on: bool) -> VehicleContext:
    return VehicleContext(car.vehicle_id, car.class_key, car.post, passengers, ac_on, car.trips_calibrated,
                          curb_mass_kg=car.curb_mass_kg, prior_only=car.trips_calibrated == 0)
