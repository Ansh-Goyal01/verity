"""Pydantic models for every data contract in CLAUDE.md.

Bump the schema string (``verity.trip.v1`` -> ``v2``) on any breaking change. Unknown extra fields
are ignored so that the logger can add metadata without making the engine reject a trip.
"""

from __future__ import annotations

import uuid
from typing import Literal

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

TRIP_SCHEMA_V1 = "verity.trip.v1"

VehicleType = Literal["car", "2w", "3w"]
SocSource = Literal["tap", "voice"]
ReportStatus = Literal["working", "broken", "blocked", "occupied", "no_power"]


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore", populate_by_name=True)


class VehicleInfo(_Frozen):
    make: str
    model: str
    variant: str = ""
    nominal_kwh: float = Field(gt=0)
    type: VehicleType


class GpsPoint(_Frozen):
    t: AwareDatetime
    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)
    acc_m: float = Field(ge=0)
    speed_mps: float | None = Field(default=None, ge=0)


class SocReading(_Frozen):
    t: AwareDatetime
    pct: float = Field(ge=0, le=100)
    source: SocSource


class TripLog(_Frozen):
    """One logged trip, ``verity.trip.v1``. A usable trip needs >= 2 points and >= 2 SoC readings."""

    schema_id: Literal["verity.trip.v1"] = Field(alias="schema")
    trip_id: str
    vehicle: VehicleInfo
    driver_id: str = Field(min_length=1)
    passengers: int = Field(ge=0)
    cargo_kg: float = Field(ge=0)
    ac_on: bool
    started_at: AwareDatetime
    ended_at: AwareDatetime
    points: list[GpsPoint] = Field(min_length=2)
    soc: list[SocReading] = Field(min_length=2)
    notes: str = ""
    is_simulated: bool

    @field_validator("trip_id")
    @classmethod
    def _trip_id_is_uuid(cls, value: str) -> str:
        uuid.UUID(value)  # raises ValueError (-> validation error) when malformed
        return value

    @model_validator(mode="after")
    def _ends_after_start(self) -> TripLog:
        if self.ended_at < self.started_at:
            raise ValueError("ended_at must not be earlier than started_at")
        return self

    @property
    def start_soc_pct(self) -> float:
        return min(self.soc, key=lambda r: r.t).pct

    @property
    def end_soc_pct(self) -> float:
        return max(self.soc, key=lambda r: r.t).pct

    @property
    def duration_s(self) -> float:
        return (self.ended_at - self.started_at).total_seconds()

    def to_json_dict(self) -> dict:
        """JSON-ready dict using the contract field names (``schema``, not ``schema_id``)."""
        return self.model_dump(mode="json", by_alias=True)


class Connector(_Frozen):
    type: str = Field(min_length=1)
    kw: float = Field(gt=0)


class ChargerRecord(_Frozen):
    """Charger directory entry. ``source_ids`` look like ``osm:node/123`` or ``ocm:456``."""

    id: str = Field(min_length=1)
    name: str
    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)
    operator: str = ""
    site_type: str = "unknown"
    connectors: list[Connector] = Field(default_factory=list)
    source_ids: list[str] = Field(default_factory=list)


class Report(_Frozen):
    """One status report about one charger from one source."""

    charger_id: str = Field(min_length=1)
    source_id: str = Field(min_length=1)
    status: ReportStatus
    t: AwareDatetime
    evidence: str | None = None
