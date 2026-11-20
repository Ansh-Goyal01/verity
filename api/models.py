"""Pydantic models that mirror docs/API_CONTRACT.md exactly.

Responses forbid extra fields, so FastAPI's ``response_model`` check fails loudly if the engine ever drifts
from the contract. Requests ignore unknown fields so the frontend can send harmless extras (e.g. a place
name). The two places where the engine needs a looser shape than the contract's example are recorded in
the Changelog in docs/API_CONTRACT.md (``days_since_last`` may be null; ``connectors`` may be empty).
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


class GraphInfo(Resp):
    backend: Literal["neo4j", "memory"]
    nodes: int = Field(ge=0)
    relationships: int = Field(ge=0)


class Health(Resp):
    status: Literal["ok"]
    version: str
    neo4j: Literal["connected", "in_memory"]
    is_simulated: bool
    graph: GraphInfo  # contract v3, additive (W0.7)
    sarvam_key_present: bool  # whether voice can work; never the key itself


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


class NamedLatLon(LatLon):
    """A trip end; ``name`` (optional, WP1) is spoken in the answer. Absent names are left out of the plan id."""

    name: str | None = Field(default=None, min_length=1, max_length=80)


class DispatchRequest(Req):
    vehicle_id: str
    origin: NamedLatLon
    destination: NamedLatLon
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


TrustBasis = Literal["own reports", "operator pool", "prior only"]


class StopTrust(Resp):
    """WP3: how much evidence stands behind a stop's or backup's p_works, and where it comes from."""
    level: TrustLevel
    n_reports: int = Field(ge=0)
    days_since_last: int | None
    basis: TrustBasis


class VerifyCharger(Resp):
    charger_id: str
    name: str
    trust: StopTrust


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
    trust: StopTrust  # WP3
    if_needed: bool = False  # WP3b: insurance stop, charged only when the battery comes in lower than expected


class Alternate(Resp):
    for_stop_index: int
    charger_id: str
    name: str
    lat: float
    lon: float
    p_reach: float
    p_works: float
    trust: StopTrust  # WP3


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


class DataSource(Resp):
    status: Literal["real", "simulated"]
    source: str
    n: int | None = None
    note: str | None = None


class DataSources(Resp):
    route: DataSource
    elevation: DataSource
    weather: DataSource
    vehicle: DataSource
    charger_reports: DataSource


class ReleaseVehicle(Resp):
    id: str
    name: str
    battery_kwh: float
    trips_calibrated: int
    calibration_basis: str  # "public trips" | "class prior" | "simulated trips"
    source_count: int
    is_simulated: bool


class Calibration(Resp):
    trips: int
    basis: str
    label: str  # e.g. "First trip in this car: prior only"


class CatalogVehicle(Resp):
    vehicle_id: str
    make: str
    model: str
    variant: str
    battery_kwh: float
    claimed_range_km: float | None
    range_test_cycle: str | None
    source_quality: str  # "official" | "secondary" | "simulated"
    verify_note: str | None
    trips_calibrated: int
    calibration_basis: str
    curb_mass_basis: str  # "spec sheet" | "class typical"
    is_simulated: bool


class VehiclesResponse(Resp):
    vehicles: list[CatalogVehicle]


class DispatchResponse(Resp):
    plan_id: str
    vehicle_id: str
    decision: Decision
    confidence: float
    confidence_display: str  # "95%+" | "under 5%" | the rounded percent (W0.2)
    reason: str  # one sentence naming the charger that carries the decision (W0.6)
    speech: dict[str, str]  # language -> the exact sentence to speak (W0.5); empty if the engine cannot speak it honestly
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
    verify_chargers: list[VerifyCharger] = []  # WP3: chargers whose verification could clear a refusal
    compute_ms: int
    n_runs: int = Field(ge=1)  # Monte Carlo runs behind every probability here (W0.3): never hardcode it
    data_sources: DataSources  # WP1: where each input came from; is_simulated is derived from it
    vehicle: ReleaseVehicle  # WP2
    calibration: Calibration  # WP2
    is_simulated: bool


# --- geocoder (WP1) ---------------------------------------------------------------------------------------------


class GeoPlace(Resp):
    name: str
    lat: float
    lon: float
    kind: str  # OpenStreetMap place kind from Photon, e.g. "city", "suburb"


class GeocodeResponse(Resp):
    results: list[GeoPlace]
    source: str


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
    n_runs: int = Field(ge=1)  # the runs of the plan being reweighted (W0.3)
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
    trust_basis: TrustBasis  # WP3


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


# --- ride check (contract v2, F8b) ---------------------------------------------------------------------------------


class RideCheckRequest(Req):
    vehicle_id: str
    soc_pct: float = Field(ge=0, le=100)
    current: LatLon
    pickup: LatLon
    drop: LatLon
    earning_rs_per_hour: float | None = Field(default=None, gt=0, le=10000)  # None = the cited example rate
    passengers: int = Field(default=1, ge=0, le=8)
    ac_on: bool = True
    target_confidence: float = Field(default=0.9, gt=0.5, le=0.99)
    departure_time: str | None = None


class RideSummary(Resp):
    distance_km: float
    pickup_km: float
    duration_min: int
    p_complete: float
    drop_soc: ArrivalSoc


class ConsideredCharger(Resp):
    charger_id: str
    name: str
    lat: float | None
    lon: float | None
    site_id: str
    road_km: float
    drive_min: int
    p_works: float
    evidence: Evidence
    recent_failure_days: int | None
    confidence_as_primary: float
    expected_lost_min: int
    expected_lost_rs: int
    chosen: bool
    note: str


class RideBackup(Resp):
    charger_id: str
    name: str
    same_site_as_primary: bool


class MinutePart(Resp):
    part: Literal["drive", "backup_drive", "failed_stops", "waiting", "charging", "stranding"]
    label: str
    minutes: float


class AfterDrop(Resp):
    charger: ConsideredCharger
    backups: list[RideBackup]
    expected_lost_min: int
    expected_lost_rs: int
    minutes: list[MinutePart]


class RideBingoPoint(Resp):
    km: float
    bingo_soc_pct: int
    expected_soc_pct: int


class RideLever(Resp):
    lever_id: str
    label: str
    new_confidence: float
    time_cost_min: int
    rupee_cost: int


class EarningRate(Resp):
    rs_per_hour: float
    is_example: bool
    source: str


class RideRoute(Resp):
    polyline: list[list[float]]
    source: str


class RideCheckResponse(Resp):
    vehicle_id: str
    decision: Decision
    confidence: float
    confidence_display: str  # "95%+" | "under 5%" | the rounded percent (W0.2)
    reason: str
    ride: RideSummary
    after_drop: AfterDrop | None
    considered: list[ConsideredCharger]
    bingo_line: list[RideBingoPoint]
    levers: list[RideLever]
    earning_rs_per_hour: float
    compute_ms: int
    n_runs: int = Field(ge=1)  # Monte Carlo runs behind the confidence (W0.3)
    speech: dict[str, str]  # language -> the exact sentence to speak (W3.2); empty if it cannot be said honestly
    route: RideRoute
    earning_rate: EarningRate
    data_notes: list[str]
    is_simulated: bool


class PinnedCharger(Resp):
    charger_id: str
    name: str
    role: str
    simulated_reports: int


class DriverDemo(Resp):
    story: str
    request: RideCheckRequest  # POST it unchanged to /api/ride-check
    pinned_chargers: list[PinnedCharger]
    is_simulated: bool


# --- voice and errors --------------------------------------------------------------------------------------------


class TtsRequest(Req):
    text: str = Field(min_length=1, max_length=1000)
    language: str


class TtsResponse(Resp):
    audio_base64: str
    format: Literal["wav"]
    cached: bool  # true: pre-generated clip served because live text-to-speech failed (W0.5)


class SttResponse(Resp):
    text: str
    soc_pct: int | None


# --- the spoken ride-check (W3.2) ----------------------------------------------------------------------------------


class PlaceRef(Resp):
    id: str
    name: str
    lat: float
    lon: float


class RideSlotsOut(Resp):
    soc_pct: int | None
    pickup: PlaceRef | None
    drop: PlaceRef | None
    missing: list[str]  # what the rules and the model could not find: the driver is asked again
    unresolved: list[str]  # place names heard but not in the gazetteer (never guessed)
    parsed_by: Literal["rules", "llm", "rules+llm"]


class SpokenClip(Resp):
    audio_base64: str
    format: Literal["wav"]
    cached: bool  # true: a pre-generated clip, served because live text-to-speech failed


class VoiceRideTimings(Resp):
    """Milliseconds per stage; null for a stage this call did not run. ``total`` is the handler's own time."""

    hear: int | None
    parse: int | None
    llm: int | None
    decide: int | None
    speak: int | None
    total: int


class VoiceRideHeard(Resp):
    stage: Literal["confirm", "clarify"]  # confirm: read back, wait for yes; clarify: something was not understood
    transcript: str  # in the script it was spoken in
    language_code: str | None  # as detected by speech-to-text
    reply_language: Literal["en-IN", "hi-IN"]
    slots: RideSlotsOut
    needs_confirmation: bool
    prompt: str  # the read-back or the question, as text
    prompt_audio: SpokenClip | None
    timings_ms: VoiceRideTimings
    is_simulated: bool


class VoiceRideDecided(Resp):
    stage: Literal["decided", "not_confirmed"]
    reply_language: Literal["en-IN", "hi-IN"]
    ride_check: RideCheckResponse | None
    speech: str | None  # the sentence spoken: a fixed template filled with the ride-check's numbers
    audio: SpokenClip | None
    timings_ms: VoiceRideTimings
    is_simulated: bool


class VoiceRideConfirm(Req):
    """Second call: the driver said yes (or no) to the read-back. Nothing runs unless ``confirmed`` is true."""

    confirmed: bool
    soc_pct: int = Field(ge=0, le=100)
    pickup_id: str
    drop_id: str
    language: Literal["en-IN", "hi-IN"]
    vehicle_id: str | None = None  # default: the demo driver's vehicle
    current: LatLon | None = None  # default: the demo driver's position
    speak: bool = True


class ErrorBody(Resp):
    error: str
    message: str


# --- graph audit endpoints and live reports (contract v4, additive; W2.6) -----------------------------------------


class AuditNode(Resp):
    id: str
    label: str
    props: dict


class AuditEdge(Resp):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    from_: str = Field(alias="from")
    to: str
    type: str
    props: dict


class TrustBlock(Resp):
    p_works: float | None  # rounded to 5 %; null when nothing is known
    trust_level: TrustLevel
    n_reports: int = Field(ge=0)
    days_since_last: int | None


class AuditView(Resp):
    nodes: list[AuditNode]
    edges: list[AuditEdge]
    cypher: str  # the query that produced this view (when ``executed`` is false: the one Neo4j would have run)
    query_ms: float = Field(ge=0)
    backend: Literal["neo4j", "memory"]
    executed: bool
    is_simulated: bool


class WhyResponse(AuditView):
    plan_id: str


class ChargerGraphResponse(AuditView):
    charger_id: str
    trust: TrustBlock


class ReportRequest(Req):
    charger_id: str = Field(min_length=1, max_length=64)
    status: ReportStatus
    consent: bool  # must be true: the report joins the charger's public history
    channel: Literal["driver_voice"] = "driver_voice"
    note: str | None = Field(default=None, max_length=200)


class ReportResponse(Resp):
    charger_id: str
    status: ReportStatus
    source_id: str
    t: str
    trust_before: TrustBlock
    trust_after: TrustBlock
    backend: Literal["neo4j", "memory"]
    is_simulated: bool


# --- spoken charger report (contract v5, additive; W3.3) -------------------------------------------------------------


class ReportCandidate(Resp):
    charger_id: str
    name: str
    score: float  # share of the spoken name words found in this charger's name


class VoiceReportText(Req):
    """First call without audio: the transcript the app already has (e.g. from /api/voice/stt)."""

    transcript: str = Field(min_length=1, max_length=300)
    language: Literal["unknown", "en-IN", "hi-IN"] = "unknown"
    speak: bool = True


class VoiceReportHeard(Resp):
    """What was heard, read back. Nothing is written by this call."""

    stage: Literal["confirm", "clarify"]  # confirm: one charger + one status, waiting for yes; clarify: a question
    transcript: str
    language_code: str | None
    reply_language: Literal["en-IN", "hi-IN"]
    status: ReportStatus | None
    charger: ReportCandidate | None  # set only when exactly one charger fits every distinctive spoken word
    candidates: list[ReportCandidate]  # the few best fits when it was not clear
    needs_confirmation: bool
    prompt: str
    prompt_audio: SpokenClip | None
    timings_ms: dict[str, int | None]
    is_simulated: Literal[False] = False


class VoiceReportConfirm(Req):
    """Second call: the driver said yes (or no) to the read-back. Nothing is stored unless ``confirmed`` and ``consent``."""

    confirmed: bool
    charger_id: str = Field(min_length=1, max_length=64)
    status: ReportStatus
    consent: bool  # the report and its transcript join the charger's history; the audio is never stored
    language: Literal["en-IN", "hi-IN"]
    transcript: str | None = Field(default=None, max_length=300)
    from_real_person: bool = True  # false = a scripted demo note: stored as simulated, under its own source
    speak: bool = True


class PlanChange(Resp):
    plan_id: str
    vehicle_id: str
    decision_before: str
    decision_after: str
    confidence_before: str
    confidence_after: str
    moved: bool  # the decision or the chargers it relies on changed
    changed: list[Literal["decision", "stops", "backups"]]


class VoiceReportDecided(Resp):
    stage: Literal["recorded", "not_recorded"]
    reply_language: Literal["en-IN", "hi-IN"]
    report: ReportResponse | None  # includes trust before and after
    plans_checked: int  # cached plans that use this charger and were re-planned (at most 2)
    plans_pending: int  # plans that use it but were not re-checked now (over the limit, trust unchanged, or busy)
    replan_failed: bool  # the report is stored; only the re-check failed
    plans: list[PlanChange]
    speech: str
    audio: SpokenClip | None
    timings_ms: dict[str, int | None]
    is_simulated: bool
