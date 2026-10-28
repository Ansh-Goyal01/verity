"""FastAPI app serving the Verity engine exactly as specified in docs/API_CONTRACT.md.

Run:  uv run uvicorn api.main:app --reload     (then open http://localhost:8000/api/health)

Every response carries ``is_simulated`` and is validated against ``api/models.py`` (which mirrors the
contract). Errors always have the shape ``{"error": code, "message": text}``.
"""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool
from starlette.exceptions import HTTPException as StarletteHTTPException

from verity.voice import SarvamClient, VoiceUnavailable, parse_multipart, parse_soc_percent

from .models import (
    ChargersResponse,
    DemoScenario,
    DispatchRequest,
    DispatchResponse,
    ForecastUpdateRequest,
    ForecastUpdateResponse,
    Health,
    ReplayResponse,
    SttResponse,
    TtsRequest,
    TtsResponse,
)
from .service import ApiError, EngineService

# Origins her pages use: "null" (file:// double-click), VS Code Live Server, and GitHub Pages.
ALLOWED_ORIGINS = ["null", "http://localhost:5500", "http://127.0.0.1:5500", "https://ansh-goyal01.github.io"]
VOICE_MESSAGE = "Voice is not configured on this server (no SARVAM_API_KEY); use browser speech and the keypad."

_service: EngineService | None = None


def get_service() -> EngineService:
    global _service
    if _service is None:
        _service = EngineService()
    return _service


def get_voice_client() -> SarvamClient | None:
    return SarvamClient.from_env()


@asynccontextmanager
async def lifespan(_: FastAPI):
    svc = get_service()  # build the demo fleet and trust estimates once, before the first request
    yield
    svc.close()  # release the Neo4j driver


app = FastAPI(title="Verity API", version="0.1.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=ALLOWED_ORIGINS, allow_methods=["*"], allow_headers=["*"],
                   allow_credentials=False)


def _error(status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"error": code, "message": message})


@app.exception_handler(ApiError)
async def _api_error(_: Request, exc: ApiError) -> JSONResponse:
    return _error(exc.status, exc.code, exc.message)


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
def health(svc: EngineService = Depends(get_service)) -> dict:
    return svc.health()


@app.get("/api/demo/scenario", response_model=DemoScenario)
def demo_scenario(svc: EngineService = Depends(get_service)) -> dict:
    return svc.demo_scenario()


@app.post("/api/dispatch", response_model=DispatchResponse)
def post_dispatch(req: DispatchRequest, svc: EngineService = Depends(get_service)) -> dict:
    return svc.dispatch(req.model_dump())


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


@app.post("/api/voice/tts", response_model=TtsResponse)
def voice_tts(req: TtsRequest, voice: SarvamClient | None = Depends(get_voice_client)):
    if voice is None:
        return _error(503, "voice_unavailable", VOICE_MESSAGE)
    try:
        return voice.tts(req.text, req.language)
    except VoiceUnavailable as exc:
        return _error(503, "voice_unavailable", str(exc))


@app.post("/api/voice/stt", response_model=SttResponse)
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
