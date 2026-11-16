"""FastAPI app serving the Verity engine exactly as specified in docs/API_CONTRACT.md.

Run:  uv run uvicorn api.main:app --reload     (then open http://localhost:8000/api/health)

Every response carries ``is_simulated`` and is validated against ``api/models.py`` (which mirrors the
contract). Errors always have the shape ``{"error": code, "message": text}``.
"""

from __future__ import annotations

import base64
import math
import os
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool
from starlette.exceptions import HTTPException as StarletteHTTPException

from verity.audio_cache import AudioCache
from verity.trust.graph import load_env
from verity.voice import SarvamClient, VoiceUnavailable, parse_multipart, parse_soc_percent, sarvam_key_present

from .limits import RateLimiter, client_ip, parse_origins, positive_int, rate_key
from .models import (
    ChargersResponse,
    DemoScenario,
    DispatchRequest,
    DispatchResponse,
    DriverDemo,
    ForecastUpdateRequest,
    ForecastUpdateResponse,
    Health,
    ReplayResponse,
    RideCheckRequest,
    RideCheckResponse,
    SttResponse,
    TtsRequest,
    TtsResponse,
)
from .service import ApiError, EngineService

# Origins her pages use: "null" (file:// double-click), VS Code Live Server, and GitHub Pages.
DEFAULT_ORIGINS = ["null", "http://localhost:5500", "http://127.0.0.1:5500", "https://ansh-goyal01.github.io"]
DEFAULT_VOICE_RATE_PER_MIN = 20  # tts + stt together, per address: every call is paid, a demo needs a handful
VOICE_WINDOW_S = 60.0
VOICE_MESSAGE = "Voice is not configured on this server (no SARVAM_API_KEY); use browser speech and the keypad."


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
    def __init__(self, retry_after_s: float) -> None:
        self.retry_after = max(1, math.ceil(retry_after_s))  # whole seconds, never "0"
        super().__init__(429, "rate_limited", f"Too many voice requests from this address; try again in "
                                              f"{self.retry_after} s.")


def limit_voice(request: Request, limiter: RateLimiter = Depends(get_voice_limiter)) -> None:
    """Both voice endpoints draw on one budget per address: it is the paid Sarvam calls that are being rationed."""
    retry_after = limiter.hit(rate_key(client_ip(request, TRUST_PROXY)))
    if retry_after is not None:
        raise RateLimited(retry_after)


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


@app.get("/api/demo/replay/{plan_id}", response_model=ReplayResponse)
def get_replay(plan_id: str, svc: EngineService = Depends(get_service)) -> dict:
    return svc.replay(plan_id)


@app.post("/api/voice/tts", response_model=TtsResponse, dependencies=[Depends(limit_voice)])
def voice_tts(req: TtsRequest, voice: SarvamClient | None = Depends(get_voice_client),
              cache: AudioCache = Depends(get_audio_cache)):
    """Live text-to-speech; when there is no key or Sarvam cannot be reached, the pre-generated clip of exactly this
    sentence (flagged ``cached: true``), and only if there is one. Anything else is the honest 503."""
    if voice is None:
        failure = VOICE_MESSAGE
    else:
        try:
            return {**voice.tts(req.text, req.language), "cached": False}
        except VoiceUnavailable as exc:
            failure = str(exc)
    clip = cache.lookup(req.text, req.language)
    if clip is None:
        return _error(503, "voice_unavailable", failure)
    return {"audio_base64": base64.b64encode(clip).decode("ascii"), "format": "wav", "cached": True}


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
