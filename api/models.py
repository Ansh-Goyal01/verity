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


class HealthBands(Resp):
    """WP5: the band calibration releases of real cars use (conformal k and the real n behind it)."""
    method: Literal["split conformal", "model only"]
    n: int | None
    k: float | None
    k_applied: float
    calibrated_at: str | None


class Health(Resp):
    status: Literal["ok"]
    version: str
    neo4j: Literal["connected", "in_memory"]
    is_simulated: bool
    graph: GraphInfo  # contract v3, additive (W0.7)
    bands: HealthBands  # WP5
    sarvam_key_present: bool  # whether voice can work; never the key itself
    offline: bool
    cached_route_count: int = Field(ge=0)
    charger_count: int = Field(ge=0)


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
    vehicle_state: DataSource | None = None  # WP6: present when the battery % and position are demo scenario inputs


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


class BandMethod(Resp):
    """WP5: what a release's bands are built from. Coverage holds on average over trips like the calibration set."""
    name: Literal["split conformal", "model only"]
    n: int | None  # real calibration trips behind k
    k: float | None  # the conformal quantile of the normalised residuals
    k_applied: float = Field(ge=1.0)  # the widening used (never below 1: bands are never narrowed)
    sources: list[str]
    note: str


class AssuredBlock(Resp):
    """WP9: an ILLUSTRATIVE stranding-cover price. No rupee figure without a sourced tow cost in config/assured.json."""
    eligible: bool
    reason: str | None
    p_strand: float = Field(ge=0, le=1)  # rounded to 5 %
    p_strand_basis: str
    tow_cost_rs: float | None
    tow_cost_source: str | None
    margin: float | None
    price_rs: int | None
    formula: str
    is_illustrative: Literal[True]
    note: str


class RewardBlock(Resp):
    """WP9: whether a charger report earns the ILLUSTRATIVE reward, and why not when it is withheld."""
    eligible: bool
    amount_rs: float | None
    withheld_reason: str | None
    is_illustrative: Literal[True]


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
    band_method: BandMethod  # WP5
    assured: AssuredBlock | None = None  # WP9 (illustrative cover price)
    is_simulated: bool


# --- trip options (WP4) -----------------------------------------------------------------------------------------

Objective = Literal["fastest", "fewest_stops", "safest"]
DEFAULT_ARRIVAL_BUFFER_PCT = 10.0  # = verity.forecast.RESERVE_PCT (a test keeps the two equal)


class PlanPreferences(Req):
    objectives: list[Objective] = Field(default_factory=lambda: ["fastest", "fewest_stops", "safest"], min_length=1)
    preferred_operators: list[Annotated[str, Field(min_length=1, max_length=80)]] = Field(default_factory=list,
                                                                                          max_length=10)
    avoid_operators: list[Annotated[str, Field(min_length=1, max_length=80)]] = Field(default_factory=list,
                                                                                      max_length=10)
    arrival_buffer_pct: float = Field(default=DEFAULT_ARRIVAL_BUFFER_PCT, ge=5, le=50)
    max_stops: int | None = Field(default=None, ge=0, le=6)


StateSource = Literal["entered", "demo_fleet"]


class PlanRequest(DispatchRequest):
    preferences: PlanPreferences = Field(default_factory=PlanPreferences)
    start: LatLon | None = None  # WP6: where the car is now; the plan then runs start -> origin (pickup) -> destination
    state_source: StateSource = "entered"  # WP6: "demo_fleet" = battery % and position are scenario inputs (simulated)


class PlanOption(DispatchResponse):
    labels: list[Objective]  # every objective that chose this plan (WP4 step 4: one option, both labels)
    meets_target: bool  # its displayed confidence meets the target; False = the best available, said so
    stop_count: int = Field(ge=0)


class UnsupportedPreference(Resp):
    preference: str
    reason: str


class PlanResponse(Resp):
    options: list[PlanOption]
    unsupported_preferences: list[UnsupportedPreference]
    preferences: PlanPreferences
    n_candidates: int = Field(ge=0)  # candidate plans scored (once) to choose the options
    avoided_chargers: int = Field(ge=0)
    compute_ms: int
    n_runs: int = Field(ge=1)
    is_simulated: bool


# --- fleet booking (WP6) ----------------------------------------------------------------------------------------


class FleetCar(Req):
    vehicle_id: str
    soc_pct: float = Field(ge=0, le=100)
    location: LatLon | None = None  # None = the car is at the pickup
    label: str | None = Field(default=None, max_length=80)


class FleetBookingRequest(Req):
    origin: NamedLatLon
    destination: NamedLatLon
    departure_time: str
    passengers: int = Field(ge=0, le=8)
    ac_on: bool
    target_confidence: float = Field(gt=0.5, le=0.99)
    vehicles: list[FleetCar] | None = Field(default=None, min_length=1, max_length=8)  # None = the demo fleet


class FleetStart(Resp):
    name: str | None
    lat: float
    lon: float


class FleetRow(Resp):
    rank: int = Field(ge=1)
    fleet_id: str
    label: str
    vehicle: ReleaseVehicle
    start: FleetStart | None  # None = at the pickup
    soc_pct: float
    deadhead_km: float | None  # road km from the car to the pickup
    decision: Decision
    confidence: float
    confidence_display: str
    arrival_soc: ArrivalSoc
    charging_minutes: int = Field(ge=0)
    total_minutes: int  # door to door from the car's position, charging included
    plan_id: str
    n_runs: int = Field(ge=1)
    why_not: str | None  # the engine's reason for a NO_GO or a refusal
    recommended: bool


class FleetBookingResponse(Resp):
    booking: dict
    rows: list[FleetRow]
    recommended_plan_id: str | None
    n_runs_rank: int = Field(ge=1)
    n_runs_full: int = Field(ge=1)
    full_reruns: int = Field(ge=0)
    fleet_source: str
    compute_ms: int
    is_simulated: bool


class ShareAudio(Resp):
    audio_base64: str
    format: str
    cached: bool


class ShareResponse(Resp):
    plan_id: str
    text: dict[str, str]  # language -> the message to send the driver
    link: str
    audio: dict[str, ShareAudio | None]  # language -> clip, or None when it cannot be spoken (see audio_note)
    audio_note: str | None
    is_simulated: bool


# --- Neo4j showpieces (WP8) ----------------------------------------------------------------------------------------


class RingAttackRequest(Req):
    charger_id: str | None = Field(default=None, max_length=40)
    n_sources: int = Field(default=6, ge=3, le=12)


class RingFound(Resp):
    found: bool
    members: list[str]
    attack_sources_caught: int
    shared: int
    jaccard_min: float | None
    cypher: str
    query_ms: float
    backend: Literal["neo4j", "memory"]
    executed: bool


class RingLimit(Resp):
    text: str
    recall: float | None
    source: str | None


class RingAttackResponse(Resp):
    charger_id: str
    charger_name: str
    co_targets: list[str]
    n_sources: int
    reports_added: int
    ring: RingFound
    trust_before: TrustBlock
    trust_after: TrustBlock
    trust_without_ring_detection: TrustBlock
    plans: dict
    limit: RingLimit
    is_simulated: bool
    reward: RewardBlock | None = None  # WP9: the ring's reports earn nothing


class RingResetResponse(Resp):
    reports_removed: int
    attack_reports_left: int
    attack_sources_left: int
    backend: Literal["neo4j", "memory"]
    is_simulated: bool


class NeighbourhoodResponse(Resp):
    charger_id: str
    depth: int
    nodes: list[dict]
    edges: list[dict]
    cypher: str
    query_ms: float
    backend: str
    executed: bool
    is_simulated: bool


# --- spoken trip planner (WP7) -----------------------------------------------------------------------------------


class VoicePlanText(Req):
    """Call 1 with a transcript the app already has (browser speech, or Sarvam STT down)."""
    transcript: str = Field(min_length=1, max_length=500)
    language: str = Field(default="unknown", pattern=r"^(unknown|[a-z]{2}-[A-Z]{2})$")
    speak: bool = True
    lat: float | None = Field(default=None, ge=-90, le=90)  # the phone's position: the default origin, biases places
    lon: float | None = Field(default=None, ge=-180, le=180)
    vehicle_id: str | None = Field(default=None, max_length=80)  # the app's own car: used when no car is spoken


class VoicePlanConfirm(Req):
    """Call 2: the slots exactly as read back, and the driver's answer."""
    confirmed: bool
    language: Literal["en-IN", "hi-IN"] = "en-IN"
    speak: bool = True
    origin: NamedLatLon
    destination: NamedLatLon
    vehicle_id: str
    soc_pct: float = Field(ge=0, le=100)
    departure_time: str
    ac_on: bool
    passengers: int = Field(ge=1, le=8)
    target_confidence: float = Field(default=0.9, gt=0.5, le=0.99)


class VoicePlanTimings(Resp):
    hear: int | None
    parse: int | None
    llm: int | None
    geocode: int | None
    decide: int | None
    speak: int | None
    total: int


class VoicePlanHeard(Resp):
    stage: Literal["confirm", "clarify"]
    transcript: str
    language_code: str | None
    reply_language: Literal["en-IN", "hi-IN"]
    needs_confirmation: bool
    prompt: str
    prompt_audio: ShareAudio | None
    slots: dict
    timings_ms: VoicePlanTimings
    is_simulated: bool


class VoicePlanDecided(Resp):
    stage: Literal["decided", "not_confirmed"]
    reply_language: Literal["en-IN", "hi-IN"]
    release: PlanOption | None
    speech: str | None
    audio: ShareAudio | None
    timings_ms: VoicePlanTimings
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
    reward: RewardBlock | None = None  # WP9 (illustrative reward)


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
