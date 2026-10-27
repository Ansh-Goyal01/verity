"""The data contracts accept a valid trip and reject malformed ones."""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from verity.schemas import ChargerRecord, Report, TripLog

FIXTURE = Path(__file__).parent / "fixtures" / "sample_trip.json"


@pytest.fixture()
def sample() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def _mutated(sample: dict, mutate) -> dict:
    data = copy.deepcopy(sample)
    mutate(data)
    return data


def test_valid_sample_trip_parses(sample: dict) -> None:
    trip = TripLog.model_validate(sample)
    assert trip.vehicle.nominal_kwh == 45
    assert trip.start_soc_pct == 80
    assert trip.end_soc_pct == 74
    assert trip.duration_s == 20 * 60
    assert trip.is_simulated is True


def test_roundtrip_keeps_contract_field_names(sample: dict) -> None:
    out = TripLog.model_validate(sample).to_json_dict()
    assert out["schema"] == "verity.trip.v1"
    assert "schema_id" not in out
    assert TripLog.model_validate(out) == TripLog.model_validate(sample)


def test_extra_logger_fields_are_ignored(sample: dict) -> None:
    sample["app_version"] = "9.9"
    assert TripLog.model_validate(sample).trip_id == sample["trip_id"]


MALFORMED = {
    "wrong schema string": lambda d: d.update(schema="verity.trip.v0"),
    "soc above 100": lambda d: d["soc"][0].update(pct=120),
    "soc below 0": lambda d: d["soc"][1].update(pct=-1),
    "ends before it starts": lambda d: d.update(ended_at="2026-10-27T09:00:00+05:30"),
    "latitude out of range": lambda d: d["points"][0].update(lat=91),
    "unknown vehicle type": lambda d: d["vehicle"].update(type="bike"),
    "missing is_simulated label": lambda d: d.pop("is_simulated"),
    "naive timestamp": lambda d: d.update(started_at="2026-10-27T10:00:00"),
    "single soc reading": lambda d: d.update(soc=d["soc"][:1]),
    "trip_id not a uuid": lambda d: d.update(trip_id="trip-1"),
    "negative cargo": lambda d: d.update(cargo_kg=-5),
    "zero battery size": lambda d: d["vehicle"].update(nominal_kwh=0),
    "unknown soc source": lambda d: d["soc"][0].update(source="guess"),
}


@pytest.mark.parametrize("name", list(MALFORMED))
def test_malformed_trip_is_rejected(sample: dict, name: str) -> None:
    with pytest.raises(ValidationError):
        TripLog.model_validate(_mutated(sample, MALFORMED[name]))


def test_charger_record_defaults_and_validation() -> None:
    rec = ChargerRecord.model_validate(
        {"id": "CH-1", "name": "Test charger (synthetic)", "lat": 28.5, "lon": 77.4,
         "connectors": [{"type": "CCS2", "kw": 60}], "source_ids": ["osm:node/1"]}
    )
    assert rec.site_type == "unknown"
    with pytest.raises(ValidationError):
        ChargerRecord.model_validate({"id": "CH-1", "name": "x", "lat": 95, "lon": 77.4})


def test_report_status_must_be_known() -> None:
    ok = {"charger_id": "CH-1", "source_id": "S-1", "status": "broken", "t": "2026-10-27T10:00:00+05:30"}
    assert Report.model_validate(ok).status == "broken"
    with pytest.raises(ValidationError):
        Report.model_validate({**ok, "status": "maybe"})
