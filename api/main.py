"""FastAPI app serving the Verity engine exactly as specified in docs/API_CONTRACT.md.

Run:  uv run uvicorn api.main:app --reload     (then open http://localhost:8000/api/health)

Every response carries ``is_simulated`` and is validated against ``api/models.py`` (which mirrors the
contract). Errors always have the shape ``{"error": code, "message": text}``.
"""

from __future__ import annotations

import math
import os
import re
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import ValidationError
from starlette.concurrency import run_in_threadpool
from starlette.exceptions import HTTPException as StarletteHTTPException

from verity.audio_cache import AudioCache
from verity.trust.graph import load_env
from verity.voice import SarvamClient, VoiceUnavailable, parse_multipart, parse_soc_percent, sarvam_key_present

from .limits import RateLimiter, client_ip, parse_origins, positive_int, rate_key
from .models import (
    ChargerGraphResponse,
    ChargersResponse,
    DemoScenario,
    DispatchRequest,
    DispatchResponse,
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
    VoiceRideDecided,
    VoiceRideHeard,
    WhyResponse,
)
from .service import ApiError, EngineService
from .speech import VOICE_MESSAGE, synthesise
from .voice_ride import decide_stage, hear_stage

# Origins her pages use: "null" (file:// double-click), VS Code Live Server, and GitHub Pages.
DEFAULT_ORIGINS = ["null", "http://localhost:5500", "http://127.0.0.1:5500", "https://ansh-goyal01.github.io"]
DEFAULT_REPORT_RATE_PER_MIN = 10  # live reports per address: each one writes to the graph that trust is computed from
REPORT_COOLDOWN_S = 600.0  # one address may report on one charger once per 10 minutes (one anonymous caller must not stack reports)
DEFAULT_VOICE_RATE_PER_MIN = 20  # tts + stt together, per address: every call is paid, a demo needs a handful
VOICE_WINDOW_S = 60.0
MAX_RECORDING_BYTES = 5_000_000  # Sarvam takes clips under 30 s; a larger upload is not a driver's one sentence
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
        _service = EngineService()
    return _service


def get_voice_client() -> SarvamClient | None:
    return SarvamClient.from_env()


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


app = FastAPI(title="Verity API", version="0.1.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=ALLOWED_ORIGINS, allow_methods=["*"], allow_headers=["*"],
                   allow_credentials=False,
                   expose_headers=["Retry-After"])  # else a page on another origin reads it as null (W0.7 429)


def _error(status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"error": code, "message": message})


@app.exception_handler(ApiError)
async def _api_error(_: Request, exc: ApiError) -> JSONResponse:
    return _error(exc.status, exc.code, exc.message)


@app.exception_handler(RateLimited)  # the most specific handler wins, so this one adds the Retry-After header
async def _rate_limited(_: Request, exc: RateLimited) -> JSONResponse:
    return JSONResponse(status_code=429, content={"error": exc.code, "message": exc.message},
                        headers={"Retry-After": str(exc.retry_after)})


@app.exception_handler(RequestValidationError)
async def _validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
    detail = "; ".join(f"{'.'.join(str(p) for p in e['loc'] if p != 'body')}: {e['msg']}" for e in exc.errors())
    return _error(422, "invalid_request", detail or "invalid request")


@app.exception_handler(StarletteHTTPException)
async def _http_error(_: Request, exc: StarletteHTTPException) -> JSONResponse:
    code = {404: "not_found", 405: "method_not_allowed"}.get(exc.status_code, "http_error")
    return _error(exc.status_code, code, str(exc.detail))


@app.exception_handler(Exception)
async def _unexpected(_: Request, exc: Exception) -> JSONResponse:
    return _error(500, "internal_error", f"{type(exc).__name__}: the engine failed on this request")


@app.get("/api/health", response_model=Health)
def health(svc: EngineService = Depends(get_service), key_present: bool = Depends(voice_key_present)) -> dict:
    return {**svc.health(), "sarvam_key_present": key_present}


@app.get("/api/demo/scenario", response_model=DemoScenario)
def demo_scenario(svc: EngineService = Depends(get_service)) -> dict:
    return svc.demo_scenario()


@app.post("/api/dispatch", response_model=DispatchResponse)
def post_dispatch(req: DispatchRequest, svc: EngineService = Depends(get_service)) -> dict:
    return svc.dispatch(req.model_dump())


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
def voice_tts(req: TtsRequest, voice: SarvamClient | None = Depends(get_voice_client),
              cache: AudioCache = Depends(get_audio_cache)):
    """Live text-to-speech; when there is no key or Sarvam cannot be reached, the pre-generated clip of exactly this
    sentence (flagged ``cached: true``), and only if there is one. Anything else is the honest 503."""
    try:
        return synthesise(req.text, req.language, voice, cache)
    except VoiceUnavailable as exc:
        return _error(503, "voice_unavailable", str(exc))


@app.post("/api/voice/stt", response_model=SttResponse, dependencies=[Depends(limit_voice)])
async def voice_stt(request: Request, voice: SarvamClient | None = Depends(get_voice_client)):
    if voice is None:
        return _error(503, "voice_unavailable", VOICE_MESSAGE)
    try:
        fields, files = parse_multipart(request.headers.get("content-type", ""), await request.body())
    except ValueError as exc:
        return _error(422, "invalid_request", str(exc))
    upload = files.get("audio") or files.get("file")
    if upload is None:
        return _error(422, "invalid_request", "multipart field 'audio' (the recording) is required")
    filename, content_type, data = upload
    try:
        text = await run_in_threadpool(voice.stt, data, filename, content_type, fields.get("language", "en-IN"))
    except VoiceUnavailable as exc:
        return _error(503, "voice_unavailable", str(exc))
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
        return _error(413, "payload_too_large", f"the request is larger than this endpoint accepts "
                                                f"({MAX_RECORDING_BYTES} bytes of audio, one sentence under 30 s)")
    if is_json:
        try:
            confirm = VoiceRideConfirm.model_validate_json(body)
        except ValidationError as exc:
            return _error(422, "invalid_request", _validation_detail(exc))
        return await run_in_threadpool(decide_stage, svc, voice, cache, confirm)

    if voice is None:
        return _error(503, "voice_unavailable", VOICE_MESSAGE)
    try:
        fields, files = await run_in_threadpool(parse_multipart, content_type, body)  # not on the event loop
    except ValueError as exc:
        return _error(422, "invalid_request", str(exc))
    upload = files.get("audio") or files.get("file")
    if upload is None:
        return _error(422, "invalid_request", "multipart field 'audio' (the recording) is required")
    filename, audio_type, audio = upload
    if not audio or len(audio) > MAX_RECORDING_BYTES:
        return _error(422, "invalid_request", f"the recording must be between 1 byte and {MAX_RECORDING_BYTES} bytes")
    language = fields.get("language", "unknown")
    if not LANGUAGE_FIELD.fullmatch(language):
        return _error(422, "invalid_request", "language must be 'unknown' or a code like hi-IN")
    speak = fields.get("speak", "1").strip().lower() not in {"0", "false", "no"}
    try:
        return await run_in_threadpool(hear_stage, voice, cache, audio, filename, audio_type, language, speak)
    except VoiceUnavailable as exc:
        return _error(503, "voice_unavailable", str(exc))
