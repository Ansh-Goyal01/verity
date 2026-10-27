"""Pydantic models that mirror docs/API_CONTRACT.md exactly.

Responses forbid extra fields, so FastAPI's ``response_model`` check fails loudly if the engine ever drifts
from the contract. Requests ignore unknown fields so the frontend can send harmless extras (e.g. a place
name). The two places where the engine needs a looser shape than the contract's example are recorded in
docs/API_CONTRACT_CHANGES.md (``days_since_last`` may be null; ``connectors`` may be empty).
"""

from __future__ import annotations

from typing import Annotated, Literal, Union

from pydantic import BaseModel, ConfigDict, Field

TrustLevel = Literal["high", "medium", "low", "unknown"]
Decision = Literal["GO", "NO_GO", "INSUFFICIENT_EVIDENCE"]
Action = Literal["CONTINUE", "DIVERT", "SLOW_DOWN"]
Term = Literal["rolling", "aero", "grade", "inertia", "regen", "aux", "charging"]
ReportStatus = Literal["working", "broken", "blocked", "occupied", "no_power"]


class Resp(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Req(BaseModel):
    model_config = ConfigDict(extra="ignore")


# --- health and demo ---------------------------------------------------------------------------------------


class Health(Resp):
    status: Literal["ok"]
    version: str
    neo4j: Literal["connected", "in_memory"]
    is_simulated: bool


class Place(Resp):
    name: str
    lat: float
    lon: float


class Booking(Resp):
    booking_id: str
    origin: Place
    destination: Place
    departure_time: str
    passengers: int
    ac_on: bool
    target_confidence: float


class UsableKwh(Resp):
    p10: float
    p50: float
    p90: float


class DemoVehicle(Resp):
    vehicle_id: str
    label: str
    model_class: str
    soc_pct: int
    usable_kwh_estimate: UsableKwh
    health_note: str
    trips_calibrated: int


class DemoScenario(Resp):
    booking: Booking
    vehicles: list[DemoVehicle]
    is_simulated: bool


# --- dispatch ---------------------------------------------------------------------------------------------------


class LatLon(Req):
    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)


class DispatchRequest(Req):
    vehicle_id: str
    origin: LatLon
    destination: LatLon
    departure_time: str
    start_soc_pct: float = Field(ge=0, le=100)
    passengers: int = Field(ge=0, le=8)
    ac_on: bool
    target_confidence: float = Field(gt=0.5, le=0.99)


class ArrivalSoc(Resp):
    p10: int
    p50: int
    p90: int


class Route(Resp):
    distance_km: float
    duration_min: int
    polyline: list[list[float]]


class Evidence(Resp):
    n_reports: int
    days_since_last: int | None
    trust_level: TrustLevel


class Stop(Resp):
    charger_id: str
    name: str
    lat: float
    lon: float
    km_along_route: float
    arrive_soc_p50: int
    charge_minutes: int
    p_works: float
    evidence: Evidence


class Alternate(Resp):
    for_stop_index: int
    charger_id: str
    name: str
    lat: float
    lon: float
    p_reach: float
    p_works: float


class BingoPoint(Resp):
    km: float
    bingo_soc_pct: int
    expected_soc_pct: int
    expected_soc_p10: int
    expected_soc_p90: int


class BreakdownRow(Resp):
    term: Term
    label: str
    soc_pct: int


class Lever(Resp):
    lever_id: str
    label: str
    new_confidence: float
    time_cost_min: int


class RiskSource(Resp):
    kind: Literal["unknown", "unpredictable"]
    label: str


class DispatchResponse(Resp):
    plan_id: str
    vehicle_id: str
    decision: Decision
    confidence: float
    arrival_soc: ArrivalSoc
    route: Route
    stops: list[Stop]
    alternates: list[Alternate]
    bingo_line: list[BingoPoint]
    point_of_no_return_km: float
    breakdown: list[BreakdownRow]
    levers: list[Lever]
    risk_sources: list[RiskSource]
    refusal_reason: str | None
    compute_ms: int
    is_simulated: bool


# --- live update ----------------------------------------------------------------------------------------------


class ForecastUpdateRequest(Req):
    plan_id: str
    t: str
    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)
    soc_pct: float = Field(ge=0, le=100)


class DivertTo(Resp):
    charger_id: str
    name: str
    lat: float
    lon: float
    p_works: float


class ForecastUpdateResponse(Resp):
    plan_id: str
    km_along_route: float
    confidence: float
    arrival_soc: ArrivalSoc
    bingo_soc_pct_here: int
    action: Action
    divert_to: DivertTo | None
    message: str
    is_simulated: bool


# --- chargers and replay -------------------------------------------------------------------------------------------


class Connector(Resp):
    type: str
    kw: float


class ApiCharger(Resp):
    charger_id: str
    name: str
    lat: float
    lon: float
    operator: str
    site_type: str
    connectors: list[Connector]
    p_works: float
    trust_level: TrustLevel
    n_reports: int
    days_since_last: int | None
    source: Literal["osm", "ocm", "audit", "simulated"]


class ChargersResponse(Resp):
    chargers: list[ApiCharger]
    is_simulated: bool


class SocReadingEvent(Resp):
    t_offset_s: int
    type: Literal["soc_reading"]
    km: float
    soc_pct: int


class ChargerReportEvent(Resp):
    t_offset_s: int
    type: Literal["charger_report"]
    charger_id: str
    status: ReportStatus
    source_trust: TrustLevel


class ForecastEvent(Resp):
    t_offset_s: int
    type: Literal["forecast"]
    action: Action
    message: str
    divert_to: DivertTo | None


ReplayEvent = Annotated[Union[SocReadingEvent, ChargerReportEvent, ForecastEvent], Field(discriminator="type")]


class ReplayResponse(Resp):
    plan_id: str
    events: list[ReplayEvent]
    is_simulated: bool


# --- voice and errors --------------------------------------------------------------------------------------------


class TtsRequest(Req):
    text: str = Field(min_length=1, max_length=1000)
    language: str


class TtsResponse(Resp):
    audio_base64: str
    format: Literal["wav"]


class SttResponse(Resp):
    text: str
    soc_pct: int | None


class ErrorBody(Resp):
    error: str
    message: str
