"""FastAPI app serving the Verity engine exactly as specified in docs/API_CONTRACT.md.

Run:  uv run uvicorn api.main:app --reload     (then open http://localhost:8000/api/health)

The engine contracts are defined in ``api/models.py``. Production errors include a request ID.
"""

from __future__ import annotations

import json
import logging
import math
import os
import re
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from pydantic import ValidationError
from starlette.concurrency import run_in_threadpool

from verity.audio_cache import AudioCache
from verity.trust.graph import load_env
from verity.voice import SarvamClient, VoiceUnavailable, parse_multipart, parse_soc_percent, sarvam_key_present

from verity.paths import RESULTS_DIR

from .ledger import build_ledger
from .limits import RateLimiter, client_ip, parse_origins, positive_int, rate_key
from .voice_plan import decide_plan_stage, hear_plan_stage
from .demo_ring import ring_attack, ring_reset
from .middleware import AUDIO_LIMIT, error_response, install
from .models import (
    GeocodeResponse,
    VehiclesResponse,
    ChargerGraphResponse,
    ChargersResponse,
    DemoScenario,
    DispatchRequest,
    DispatchResponse,
    PlanRequest,
    PlanResponse,
    PlanOption,
    FleetBookingRequest,
    FleetBookingResponse,
    ShareResponse,
    VoicePlanConfirm,
    RingAttackRequest,
    RingAttackResponse,
    RingResetResponse,
    NeighbourhoodResponse,
    VoicePlanDecided,
    VoicePlanHeard,
    VoicePlanText,
    DriverDemo,
    ForecastUpdateRequest,
    ForecastUpdateResponse,
    Health,
    ReplayResponse,
    ReportRequest,
    ReportResponse,
    RideCheckRequest,
    RideCheckResponse,
    SttResponse,
    TtsRequest,
    TtsResponse,
    VoiceRideConfirm,
    VoiceReportConfirm,
    VoiceReportDecided,
    VoiceReportHeard,
    VoiceReportText,
    VoiceRideDecided,
    VoiceRideHeard,
    WhyResponse,
)
from .service import ApiError, EngineService
from .demo_cache import offline_enabled
from . import routes_cpo, routes_fleet, routes_me, routes_share, routes_trips
from .operator_graph import restore_reports
from .speech import VOICE_MESSAGE, synthesise
from .voice_lab import capture_stage, lab_enabled, token_ok, validate as validate_lab_form
from .voice_ride import decide_stage, hear_stage
from .voice_report import hear_report_stage, record_report_stage

# Origins her pages use: "null" (file:// double-click), VS Code Live Server, and GitHub Pages.
DEFAULT_ORIGINS = ["null", "http://localhost:5500", "http://127.0.0.1:5500", "https://ansh-goyal01.github.io"]
DEFAULT_REPORT_RATE_PER_MIN = 10  # live reports per address: each one writes to the graph that trust is computed from
REPORT_COOLDOWN_S = 600.0  # one address may report on one charger once per 10 minutes (one anonymous caller must not stack reports)
DEFAULT_VOICE_RATE_PER_MIN = 20  # tts + stt together, per address: every call is paid, a demo needs a handful
VOICE_WINDOW_S = 60.0
MAX_RECORDING_BYTES = AUDIO_LIMIT
MULTIPART_OVERHEAD_BYTES = 100_000  # the form's other fields and boundaries
MAX_CONFIRM_JSON_BYTES = 16_384  # the second call is a handful of fields
LANGUAGE_FIELD = re.compile(r"unknown|[a-z]{2,3}-[A-Z]{2}")  # matched with fullmatch: only a code-shaped value reaches a paid call


def env_setting(name: str) -> str | None:
    """A setting from the environment, else from .env (the environment wins, as for the Neo4j settings)."""
    return os.environ.get(name) or load_env().get(name) or None


def cors_origins() -> list[str]:
    """VERITY_CORS_ORIGINS (comma-separated) REPLACES the defaults when set; unset or blank keeps them."""
    return parse_origins(env_setting("VERITY_CORS_ORIGINS"), DEFAULT_ORIGINS)


def voice_rate_per_min() -> int:
    return positive_int(env_setting("VERITY_VOICE_RATE_PER_MIN"), DEFAULT_VOICE_RATE_PER_MIN,
                        "VERITY_VOICE_RATE_PER_MIN")


def trust_proxy_enabled() -> bool:
    """Opt in only when exactly one proxy that appends X-Forwarded-For sits in front: otherwise the header is forgeable."""
    return (env_setting("VERITY_TRUST_PROXY") or "").strip().lower() in {"1", "true", "yes"}


ALLOWED_ORIGINS = cors_origins()
TRUST_PROXY = trust_proxy_enabled()
voice_limiter = RateLimiter(voice_rate_per_min(), VOICE_WINDOW_S)
report_cooldown = RateLimiter(1, REPORT_COOLDOWN_S)
report_limiter = RateLimiter(positive_int(env_setting("VERITY_REPORT_RATE_PER_MIN"), DEFAULT_REPORT_RATE_PER_MIN,
                                          "VERITY_REPORT_RATE_PER_MIN"), VOICE_WINDOW_S)

_service: EngineService | None = None


def get_service() -> EngineService:
    global _service
    if _service is None:
        svc = EngineService()
        if os.environ.get("DATABASE_URL") and not svc.offline:
            try:
                restore_reports(svc)
            except HTTPException as exc:
                if exc.status_code != 503:
                    raise
                logging.getLogger(__name__).warning("Account observations unavailable during engine startup")
        _service = svc
    return _service


def get_voice_client() -> SarvamClient | None:
    return None if offline_enabled() else SarvamClient.from_env()


def scripted_reports_allowed() -> bool:
    """Scripted (simulated) voice notes are a demo feature: off unless the server operator turns them on. The flag is
    client-supplied, so without this switch anyone could plant a simulated report that never ages."""
    return os.environ.get("VERITY_ALLOW_SCRIPTED_REPORTS", "").strip().lower() in {"1", "true", "yes"}


def voice_key_present() -> bool:
    return sarvam_key_present()


audio_cache = AudioCache()  # data/audio_cache: the demo's spoken answers, made ahead of time (W0.5)


def get_audio_cache() -> AudioCache:
    return audio_cache


def get_voice_limiter() -> RateLimiter:
    return voice_limiter


class RateLimited(ApiError):
    def __init__(self, retry_after_s: float, what: str = "voice", code: str = "rate_limited",
                 message: str | None = None) -> None:
        self.retry_after = max(1, math.ceil(retry_after_s))  # whole seconds, never "0"
        super().__init__(429, code, message or f"Too many {what} requests from this address; try again in "
                                               f"{self.retry_after} s.")


def limit_voice(request: Request, limiter: RateLimiter = Depends(get_voice_limiter)) -> None:
    """Both voice endpoints draw on one budget per address: it is the paid Sarvam calls that are being rationed."""
    retry_after = limiter.hit(rate_key(client_ip(request, TRUST_PROXY)))
    if retry_after is not None:
        raise RateLimited(retry_after)


DEFAULT_GEOCODE_RATE_PER_MIN = 60  # autocomplete: about one call a second per address; Photon asks for fair use
geocode_limiter = RateLimiter(positive_int(env_setting("VERITY_GEOCODE_RATE_PER_MIN"), DEFAULT_GEOCODE_RATE_PER_MIN,
                                           "VERITY_GEOCODE_RATE_PER_MIN"), VOICE_WINDOW_S)


def get_geocode_limiter() -> RateLimiter:
    return geocode_limiter


def limit_geocode(request: Request, limiter: RateLimiter = Depends(get_geocode_limiter)) -> None:
    retry_after = limiter.hit(rate_key(client_ip(request, TRUST_PROXY)))
    if retry_after is not None:
        raise RateLimited(retry_after, "place search")


def get_report_limiter() -> RateLimiter:
    return report_limiter


def get_report_cooldown() -> RateLimiter:
    return report_cooldown


def limit_reports(request: Request, limiter: RateLimiter = Depends(get_report_limiter)) -> None:
    retry_after = limiter.hit(rate_key(client_ip(request, TRUST_PROXY)))
    if retry_after is not None:
        raise RateLimited(retry_after, "report")


@asynccontextmanager
async def lifespan(_: FastAPI):
    svc = get_service()  # build the demo fleet and trust estimates once, before the first request
    yield
    svc.close()  # release the Neo4j driver


PRODUCTION = env_setting("VERITY_ENV") == "production"
app = FastAPI(title="Verity API", version="0.1.0", lifespan=lifespan,
              openapi_url=None if PRODUCTION else "/openapi.json")
origins = parse_origins(env_setting("VERITY_CORS_ORIGINS"), []) if PRODUCTION else ALLOWED_ORIGINS
install(app, origins, PRODUCTION, TRUST_PROXY, voice_rate_per_min(),
        planning_limit=positive_int(env_setting("VERITY_PLANNING_RATE_PER_MIN"), 30, "VERITY_PLANNING_RATE_PER_MIN"))
app.include_router(routes_me.router)
app.include_router(routes_trips.build_router(get_service))
app.include_router(routes_share.router)
app.include_router(routes_fleet.build_router(get_service))
app.include_router(routes_cpo.router)


@app.exception_handler(ApiError)
async def _api_error(request: Request, exc: ApiError) -> JSONResponse:
    if exc.code == "offline":
        return error_response(request, exc.status, exc.code, exc.message,
                              extra={"code": "offline", "cached_trips": exc.cached_trips or []})
    return error_response(request, exc.status, exc.code, exc.message)


@app.exception_handler(RateLimited)  # the most specific handler wins, so this one adds the Retry-After header
async def _rate_limited(request: Request, exc: RateLimited) -> JSONResponse:
    return error_response(request, 429, exc.code, exc.message, {"Retry-After": str(exc.retry_after)})


@app.get("/api/health", response_model=Health)
def health(svc: EngineService = Depends(get_service), key_present: bool = Depends(voice_key_present)) -> dict:
    return {**svc.health(), "sarvam_key_present": key_present}


@app.get("/api/demo/scenario", response_model=DemoScenario)
def demo_scenario(svc: EngineService = Depends(get_service)) -> dict:
    return svc.demo_scenario()


@app.post("/api/dispatch", response_model=DispatchResponse)
def post_dispatch(req: DispatchRequest, svc: EngineService = Depends(get_service)) -> dict:
    return svc.dispatch(req.model_dump())


@app.post("/api/plan", response_model=PlanResponse)
def post_plan(req: PlanRequest, svc: EngineService = Depends(get_service)) -> dict:
    """WP4: up to three full releases (fastest, fewest stops, safest), each with its own plan id."""
    return svc.plan(req.model_dump())


@app.post("/api/fleet/booking", response_model=FleetBookingResponse)
def post_fleet_booking(req: FleetBookingRequest, svc: EngineService = Depends(get_service)) -> dict:
    """WP6: every car ranked for one booking (GO first, then confidence, then minutes); one GO recommended at the full
    Monte Carlo count. Without ``vehicles`` the demo fleet (real models, SIMULATED battery % and depots) is used."""
    return svc.fleet_booking(req.model_dump())


@app.get("/api/release/{plan_id}", response_model=PlanOption | DispatchResponse)
def get_release(plan_id: str, svc: EngineService = Depends(get_service)) -> dict:
    """WP6: any stored release (a dispatch, a WP4 option or a fleet row) by its plan id, for the release views."""
    return svc.release(plan_id)


@app.get("/api/release/{plan_id}/share", response_model=ShareResponse, dependencies=[Depends(limit_voice)])
def get_release_share(plan_id: str, audio: bool = Query(True), svc: EngineService = Depends(get_service),
                      voice: SarvamClient | None = Depends(get_voice_client),
                      cache: AudioCache = Depends(get_audio_cache)) -> dict:
    """WP6: the release as a message for the driver: text, link, and its spoken sentences (live, else cached: true)."""
    return svc.share(plan_id, voice if audio else None, cache, audio=audio)


@app.get("/api/vehicles", response_model=VehiclesResponse)
def get_vehicles(svc: EngineService = Depends(get_service)) -> dict:
    """WP2: every real Indian car (vehicles_india.csv, cited, approximate) plus the simulated demo fleet."""
    return svc.vehicles()


@app.get("/api/geocode", response_model=GeocodeResponse, dependencies=[Depends(limit_geocode)])
def get_geocode(q: str = Query(min_length=2, max_length=100), limit: int = Query(5, ge=1, le=10),
                lat: float | None = Query(None, ge=-90, le=90), lon: float | None = Query(None, ge=-180, le=180),
                svc: EngineService = Depends(get_service)) -> dict:
    """WP1: India-only place search for the trip form (Photon, cached). ``lat``/``lon`` bias results towards a place."""
    near = (lat, lon) if lat is not None and lon is not None else None
    return svc.geocode(q, limit, near)


@app.get("/api/evidence/ledger")
def evidence_ledger(svc: EngineService = Depends(get_service)) -> dict:
    """W1.5: how much real evidence and how much simulation stand behind the answers, read from the saved result files."""
    return build_ledger(RESULTS_DIR, n_runs=svc.cfg.n_runs)


@app.get("/api/demo/driver", response_model=DriverDemo)
def demo_driver(svc: EngineService = Depends(get_service)) -> dict:
    return svc.driver_demo()


@app.post("/api/ride-check", response_model=RideCheckResponse)
def post_ride_check(req: RideCheckRequest, svc: EngineService = Depends(get_service)) -> dict:
    return svc.ride_check(req.model_dump())


@app.post("/api/forecast/update", response_model=ForecastUpdateResponse)
def post_forecast_update(req: ForecastUpdateRequest, svc: EngineService = Depends(get_service)) -> dict:
    return svc.forecast_update(req.model_dump())


@app.get("/api/chargers", response_model=ChargersResponse)
def get_chargers(min_lat: float = Query(ge=-90, le=90), min_lon: float = Query(ge=-180, le=180),
                 max_lat: float = Query(ge=-90, le=90), max_lon: float = Query(ge=-180, le=180),
                 svc: EngineService = Depends(get_service)) -> dict:
    if min_lat > max_lat or min_lon > max_lon:
        raise ApiError(422, "invalid_bbox", "min_lat/min_lon must not exceed max_lat/max_lon")
    return svc.chargers_in_bbox(min_lat, min_lon, max_lat, max_lon)


@app.get("/api/graph/why/{plan_id}", response_model=WhyResponse)
def get_graph_why(plan_id: str, svc: EngineService = Depends(get_service)) -> dict:
    return svc.graph_why(plan_id)


@app.get("/api/graph/neighborhood/{charger_id}", response_model=NeighbourhoodResponse)
def get_graph_neighbourhood(charger_id: str, depth: int = Query(2, ge=1, le=2),
                            svc: EngineService = Depends(get_service)) -> dict:
    """WP8b: a charger's graph neighbourhood as nodes + edges (depth 2 adds rings and the nearest substation)."""
    return svc.graph_neighbourhood(charger_id, depth)


def demo_enabled() -> bool:
    return os.environ.get("VERITY_DEMO", "").strip().lower() in {"1", "true", "yes"}


def require_demo() -> None:
    if not demo_enabled():
        raise ApiError(403, "demo_disabled", "The ring-attack demo is switched off on this server (VERITY_DEMO=1).")


@app.post("/api/demo/ring-attack", response_model=RingAttackResponse, dependencies=[Depends(require_demo)])
def post_ring_attack(req: RingAttackRequest, svc: EngineService = Depends(get_service)) -> dict:
    """WP8a: N SIMULATED sources report one charger broken within minutes; the graph finds the ring, trust holds."""
    return ring_attack(svc, req.charger_id, req.n_sources)


@app.post("/api/demo/ring-attack/reset", response_model=RingResetResponse, dependencies=[Depends(require_demo)])
def post_ring_reset(svc: EngineService = Depends(get_service)) -> dict:
    """WP8a: remove every report (and source) the attack added."""
    return ring_reset(svc)


@app.get("/api/graph/charger/{charger_id}", response_model=ChargerGraphResponse)
def get_graph_charger(charger_id: str, svc: EngineService = Depends(get_service)) -> dict:
    return svc.graph_charger(charger_id)


@app.post("/api/reports", response_model=ReportResponse, dependencies=[Depends(limit_reports)])
def post_report(req: ReportRequest, request: Request, svc: EngineService = Depends(get_service),
                cooldown: RateLimiter = Depends(get_report_cooldown)) -> dict:
    if req.consent:  # a refused request must not use up the cooldown
        retry_after = cooldown.hit(f"{rate_key(client_ip(request, TRUST_PROXY))}|{req.charger_id}")
        if retry_after is not None:
            raise RateLimited(retry_after, code="already_reported",
                              message=f"This address already reported on this charger; try again in {math.ceil(retry_after)} s.")
    return svc.add_report(req.model_dump())


@app.get("/api/demo/replay/{plan_id}", response_model=ReplayResponse)
def get_replay(plan_id: str, svc: EngineService = Depends(get_service)) -> dict:
    return svc.replay(plan_id)


@app.post("/api/voice/tts", response_model=TtsResponse, dependencies=[Depends(limit_voice)])
def voice_tts(req: TtsRequest, request: Request, voice: SarvamClient | None = Depends(get_voice_client),
              cache: AudioCache = Depends(get_audio_cache)):
    """Live text-to-speech; when there is no key or Sarvam cannot be reached, the pre-generated clip of exactly this
    sentence (flagged ``cached: true``), and only if there is one. Anything else is the honest 503."""
    try:
        return synthesise(req.text, req.language, voice, cache)
    except VoiceUnavailable as exc:
        return error_response(request, 503, "voice_unavailable", str(exc))


@app.post("/api/voice/stt", response_model=SttResponse, dependencies=[Depends(limit_voice)])
async def voice_stt(request: Request, voice: SarvamClient | None = Depends(get_voice_client)):
    if voice is None:
        return error_response(request, 503, "voice_unavailable", VOICE_MESSAGE)
    try:
        fields, files = parse_multipart(request.headers.get("content-type", ""), await request.body())
    except ValueError as exc:
        return error_response(request, 422, "invalid_request", str(exc))
    upload = files.get("audio") or files.get("file")
    if upload is None:
        return error_response(request, 422, "invalid_request", "multipart field 'audio' (the recording) is required")
    filename, content_type, data = upload
    try:
        text = await run_in_threadpool(voice.stt, data, filename, content_type, fields.get("language", "en-IN"))
    except VoiceUnavailable as exc:
        return error_response(request, 503, "voice_unavailable", str(exc))
    return {"text": text, "soc_pct": parse_soc_percent(text)}


async def read_body_limited(request: Request, limit: int) -> bytes | None:
    """The request body, or ``None`` as soon as it is known to exceed ``limit`` (by its declared length, else by counting
    the stream), so an oversized upload is never buffered whole."""
    declared = request.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > limit:
        return None
    chunks: list[bytes] = []
    size = 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > limit:
            return None
        chunks.append(chunk)
    return b"".join(chunks)


def _validation_detail(exc: ValidationError) -> str:
    return "; ".join(f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in exc.errors()) or "invalid request"


@app.post("/api/voice/ride-check", response_model=VoiceRideHeard | VoiceRideDecided, dependencies=[Depends(limit_voice)])
async def voice_ride_check(request: Request, svc: EngineService = Depends(get_service),
                           voice: SarvamClient | None = Depends(get_voice_client),
                           cache: AudioCache = Depends(get_audio_cache)):
    """The spoken ride-check in two calls (W3.2). Multipart audio (+ ``language``, ``speak``) -> hear and read back, or ask
    again; it never decides. JSON ``VoiceRideConfirm`` -> on ``confirmed`` the ordinary ride-check, spoken in a fixed template."""
    content_type = request.headers.get("content-type", "")
    is_json = content_type.startswith("application/json")
    body = await read_body_limited(request, MAX_CONFIRM_JSON_BYTES if is_json else MAX_RECORDING_BYTES + MULTIPART_OVERHEAD_BYTES)
    if body is None:
        return error_response(request, 413, "payload_too_large", f"the request is larger than this endpoint accepts "
                                                f"({MAX_RECORDING_BYTES} bytes of audio, one sentence under 30 s)")
    if is_json:
        try:
            confirm = VoiceRideConfirm.model_validate_json(body)
        except ValidationError as exc:
            return error_response(request, 422, "invalid_request", _validation_detail(exc))
        return await run_in_threadpool(decide_stage, svc, voice, cache, confirm)

    if voice is None:
        return error_response(request, 503, "voice_unavailable", VOICE_MESSAGE)
    try:
        fields, files = await run_in_threadpool(parse_multipart, content_type, body)  # not on the event loop
    except ValueError as exc:
        return error_response(request, 422, "invalid_request", str(exc))
    upload = files.get("audio") or files.get("file")
    if upload is None:
        return error_response(request, 422, "invalid_request", "multipart field 'audio' (the recording) is required")
    filename, audio_type, audio = upload
    if not audio or len(audio) > MAX_RECORDING_BYTES:
        return error_response(request, 422, "invalid_request", f"the recording must be between 1 byte and {MAX_RECORDING_BYTES} bytes")
    language = fields.get("language", "unknown")
    if not LANGUAGE_FIELD.fullmatch(language):
        return error_response(request, 422, "invalid_request", "language must be 'unknown' or a code like hi-IN")
    speak = fields.get("speak", "1").strip().lower() not in {"0", "false", "no"}
    try:
        return await run_in_threadpool(hear_stage, voice, cache, audio, filename, audio_type, language, speak)
    except VoiceUnavailable as exc:
        return error_response(request, 503, "voice_unavailable", str(exc))


@app.post("/api/voice/plan", response_model=VoicePlanHeard | VoicePlanDecided, dependencies=[Depends(limit_voice)])
async def voice_plan(request: Request, svc: EngineService = Depends(get_service),
                     voice: SarvamClient | None = Depends(get_voice_client),
                     cache: AudioCache = Depends(get_audio_cache)):
    """The spoken trip planner (WP7) in two calls. Call 1: multipart audio (+ ``language``, ``speak``, ``lat``, ``lon``,
    ``vehicle_id``) or JSON ``VoicePlanText`` -> read-back or question; it never plans. Call 2: JSON ``VoicePlanConfirm``
    -> on ``confirmed`` the WP4 safest option, its own spoken sentence. Nothing runs on an unconfirmed parse."""
    content_type = request.headers.get("content-type", "")
    is_json = content_type.startswith("application/json")
    body = await read_body_limited(request, MAX_CONFIRM_JSON_BYTES if is_json else MAX_RECORDING_BYTES + MULTIPART_OVERHEAD_BYTES)
    if body is None:
        return error_response(request, 413, "payload_too_large", f"the request is larger than this endpoint accepts "
                                                f"({MAX_RECORDING_BYTES} bytes of audio, one sentence under 30 s)")
    if is_json:
        try:
            data = json.loads(body)
            if isinstance(data, dict) and "confirmed" in data:
                confirm = VoicePlanConfirm.model_validate(data)
                return await run_in_threadpool(decide_plan_stage, svc, voice, cache, confirm.model_dump())
            text = VoicePlanText.model_validate(data)
        except ValidationError as exc:
            return error_response(request, 422, "invalid_request", _validation_detail(exc))
        except ValueError:
            return error_response(request, 422, "invalid_request", "the body is not valid JSON")
        position = {"lat": text.lat, "lon": text.lon} if text.lat is not None and text.lon is not None else None
        return await run_in_threadpool(lambda: hear_plan_stage(
            svc, voice, cache, transcript=text.transcript, language=text.language, speak=text.speak,
            position=position, vehicle_hint=text.vehicle_id))
    if voice is None:
        return error_response(request, 503, "voice_unavailable", VOICE_MESSAGE)
    try:
        fields, files = await run_in_threadpool(parse_multipart, content_type, body)
    except ValueError as exc:
        return error_response(request, 422, "invalid_request", str(exc))
    upload = files.get("audio") or files.get("file")
    if upload is None:
        return error_response(request, 422, "invalid_request", "multipart field 'audio' (the recording) is required")
    filename, audio_type, audio = upload
    if not audio or len(audio) > MAX_RECORDING_BYTES:
        return error_response(request, 422, "invalid_request", f"the recording must be between 1 byte and {MAX_RECORDING_BYTES} bytes")
    language = fields.get("language", "unknown")
    if not LANGUAGE_FIELD.fullmatch(language):
        return error_response(request, 422, "invalid_request", "language must be 'unknown' or a code like hi-IN")
    speak = fields.get("speak", "1").strip().lower() not in {"0", "false", "no"}
    try:
        lat, lon = float(fields["lat"]), float(fields["lon"])
        position = {"lat": lat, "lon": lon} if -90 <= lat <= 90 and -180 <= lon <= 180 else None
    except (KeyError, ValueError):
        position = None
    hint = (fields.get("vehicle_id") or "").strip()[:80] or None
    try:
        return await run_in_threadpool(lambda: hear_plan_stage(
            svc, voice, cache, transcript=None, language=language, speak=speak, position=position, vehicle_hint=hint,
            audio=audio, filename=filename, content_type=audio_type))
    except VoiceUnavailable as exc:
        return error_response(request, 503, "voice_unavailable", str(exc))


@app.post("/api/lab/voice-sample", dependencies=[Depends(limit_voice)])
async def lab_voice_sample(request: Request, svc: EngineService = Depends(get_service),
                           voice: SarvamClient | None = Depends(get_voice_client),
                           limiter: RateLimiter = Depends(get_voice_limiter)):
    """The venue voice kit (W3.5): one consented utterance, heard, scored against what the speaker was asked to say, stored
    (transcript and scores only; never audio, a name or an address). Off unless the operator sets ``VERITY_LAB_MODE=1`` and
    ``VERITY_LAB_TOKEN``; every call must carry that token in ``X-Lab-Token``."""
    if not lab_enabled():
        return error_response(request, 403, "lab_disabled", "The voice lab is switched off on this server (VERITY_LAB_MODE and VERITY_LAB_TOKEN).")
    if not token_ok(request.headers.get("x-lab-token")):
        return error_response(request, 403, "lab_token_invalid", "Send the lab token in the X-Lab-Token header.")
    body = await read_body_limited(request, MAX_RECORDING_BYTES + MULTIPART_OVERHEAD_BYTES)
    if body is None:
        return error_response(request, 413, "payload_too_large", f"the request is larger than this endpoint accepts ({MAX_RECORDING_BYTES} bytes of audio)")
    try:
        fields, files = await run_in_threadpool(parse_multipart, request.headers.get("content-type", ""), body)
    except ValueError as exc:
        return error_response(request, 422, "invalid_request", str(exc))
    upload = files.get("audio") or files.get("file")
    if upload is None:
        return error_response(request, 422, "invalid_request", "multipart field 'audio' (the recording) is required")
    filename, audio_type, audio = upload
    if not audio or len(audio) > MAX_RECORDING_BYTES:
        return error_response(request, 422, "invalid_request", f"the recording must be between 1 byte and {MAX_RECORDING_BYTES} bytes")
    form = validate_lab_form(fields, {c.id for c in svc.chargers})
    if form["compare"]:  # a comparison is five paid calls: charge the voice budget five hits, not one (limit_voice charged the first)
        for _ in range(4):
            retry_after = limiter.hit(rate_key(client_ip(request, TRUST_PROXY)))
            if retry_after is not None:
                raise RateLimited(retry_after)
    if voice is None:
        return error_response(request, 503, "voice_unavailable", VOICE_MESSAGE)
    try:
        return await run_in_threadpool(capture_stage, svc, voice, form, audio, filename, audio_type)
    except VoiceUnavailable as exc:
        return error_response(request, 503, "voice_unavailable", str(exc))


@app.post("/api/voice/report", response_model=VoiceReportHeard, dependencies=[Depends(limit_voice)])
async def voice_report(request: Request, svc: EngineService = Depends(get_service),
                       voice: SarvamClient | None = Depends(get_voice_client),
                       cache: AudioCache = Depends(get_audio_cache)):
    """A spoken charger report, call 1 of 2 (W3.3): hear it and read it back, or ask what was unclear. Writes nothing.
    Multipart audio (+ ``language``, ``speak``) or JSON ``VoiceReportText`` (a transcript the app already has)."""
    content_type = request.headers.get("content-type", "")
    is_json = content_type.startswith("application/json")
    body = await read_body_limited(request, MAX_CONFIRM_JSON_BYTES if is_json else MAX_RECORDING_BYTES + MULTIPART_OVERHEAD_BYTES)
    if body is None:
        return error_response(request, 413, "payload_too_large", f"the request is larger than this endpoint accepts "
                                                f"({MAX_RECORDING_BYTES} bytes of audio, one sentence under 30 s)")
    if is_json:
        try:
            text = VoiceReportText.model_validate_json(body)
        except ValidationError as exc:
            return error_response(request, 422, "invalid_request", _validation_detail(exc))
        return await run_in_threadpool(lambda: hear_report_stage(svc, voice, cache, transcript=text.transcript,
                                                                 language=text.language, speak=text.speak))
    if voice is None:
        return error_response(request, 503, "voice_unavailable", VOICE_MESSAGE)
    try:
        fields, files = await run_in_threadpool(parse_multipart, content_type, body)
    except ValueError as exc:
        return error_response(request, 422, "invalid_request", str(exc))
    upload = files.get("audio") or files.get("file")
    if upload is None:
        return error_response(request, 422, "invalid_request", "multipart field 'audio' (the recording) is required")
    filename, audio_type, audio = upload
    if not audio or len(audio) > MAX_RECORDING_BYTES:
        return error_response(request, 422, "invalid_request", f"the recording must be between 1 byte and {MAX_RECORDING_BYTES} bytes")
    language = fields.get("language", "unknown")
    if not LANGUAGE_FIELD.fullmatch(language):
        return error_response(request, 422, "invalid_request", "language must be 'unknown' or a code like hi-IN")
    speak = fields.get("speak", "1").strip().lower() not in {"0", "false", "no"}
    try:
        return await run_in_threadpool(lambda: hear_report_stage(svc, voice, cache, transcript=None, audio=audio,
                                                                 filename=filename, content_type=audio_type,
                                                                 language=language, speak=speak))
    except VoiceUnavailable as exc:
        return error_response(request, 503, "voice_unavailable", str(exc))


@app.post("/api/voice/report/confirm", response_model=VoiceReportDecided,
          dependencies=[Depends(limit_voice), Depends(limit_reports)])
async def voice_report_confirm(req: VoiceReportConfirm, request: Request, svc: EngineService = Depends(get_service),
                               voice: SarvamClient | None = Depends(get_voice_client),
                               cache: AudioCache = Depends(get_audio_cache),
                               cooldown: RateLimiter = Depends(get_report_cooldown)):
    """Call 2 of 2: the driver said yes or no to the read-back. On ``confirmed`` + ``consent`` the report is written (the same
    path and the same per-address, per-charger cooldown as ``POST /api/reports``), the plans that use the charger are
    re-planned, and the reply says the charger's trust before and after and whether the plan moved."""
    if not req.from_real_person and not scripted_reports_allowed():
        raise ApiError(403, "scripted_reports_disabled", "Scripted demo notes are switched off on this server "
                       "(VERITY_ALLOW_SCRIPTED_REPORTS); send from_real_person=true for a real report.")
    if req.confirmed and req.consent:  # a refused request must not use up the cooldown, nor must an unknown charger
        if req.charger_id not in {c.id for c in svc.chargers}:
            raise ApiError(404, "unknown_charger", f"No charger {req.charger_id!r}")
        retry_after = cooldown.hit(f"{rate_key(client_ip(request, TRUST_PROXY))}|{req.charger_id}")
        if retry_after is not None:
            raise RateLimited(retry_after, code="already_reported",
                              message=f"This address already reported on this charger; try again in {math.ceil(retry_after)} s.")
    return await run_in_threadpool(record_report_stage, svc, voice, cache, req)
