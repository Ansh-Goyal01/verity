"""Request budgets and production HTTP security policy."""

import logging
import math
from http import HTTPStatus
from uuid import uuid4
from urllib.parse import urlsplit

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import MutableHeaders
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.cors import CORSMiddleware
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from .auth import verify_access_token
from .limits import RateLimiter, client_ip, rate_key

log = logging.getLogger(__name__)
JSON_LIMIT = 64 * 1024
AUDIO_LIMIT = 1024 * 1024
PLANNING = {"/api/plan", "/api/dispatch", "/api/ride-check", "/api/forecast/update", "/api/trips",
            "/api/fleet/booking", "/api/fleet/booking/check"}
REPORTING = {"/api/reports", "/api/reports/quick", "/api/voice/report/confirm"}
DOCS = {"/docs", "/docs/oauth2-redirect", "/redoc", "/openapi.json"}


def error_response(request: Request, status: int, code: str, message: str,
                   headers: dict | None = None, *, extra: dict | None = None) -> JSONResponse:
    request_id = getattr(request.state, "request_id", None) or uuid4().hex
    if getattr(request.state, "production", False):
        # Classify upstream failures and validation errors without echoing their input or exception text.
        if (status >= 500 and code != "offline") or status == 422:
            message = HTTPStatus(status).phrase
        content = {"error": message, "id": request_id}
    else:
        content = {"error": code, "message": message}
    if extra:
        content.update(extra)
    return JSONResponse(content, status_code=status, headers=headers)


async def http_error(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    code = {404: "not_found", 405: "method_not_allowed"}.get(exc.status_code, "http_error")
    return error_response(request, exc.status_code, code, str(exc.detail), exc.headers)


async def validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
    detail = "; ".join(f"{'.'.join(str(p) for p in e['loc'] if p != 'body')}: {e['msg']}" for e in exc.errors())
    return error_response(request, 422, "invalid_request", detail or "Invalid request.")


class ShareLogFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        # uvicorn.access supplies the request target as the third argument.
        if isinstance(record.args, tuple) and len(record.args) == 5:
            client, method, target, version, status = record.args
            if isinstance(target, str) and target.startswith("/api/share/"):
                record.args = (client, method, "/api/share/[redacted]", version, status)
        return True


class SecurityHeaders:
    def __init__(self, app: ASGIApp, production: bool) -> None:
        self.app = app
        self.production = production

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request = Request(scope)
        request.state.request_id = uuid4().hex
        request.state.production = self.production

        async def hardened_send(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                csp = "default-src 'none'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
                if not self.production and scope["path"] in DOCS:
                    # FastAPI's local interactive documentation embeds its initialization script.
                    csp += ("; script-src 'unsafe-inline' https://cdn.jsdelivr.net"
                            "; style-src 'unsafe-inline' https://cdn.jsdelivr.net"
                            "; img-src 'self' data: https://fastapi.tiangolo.com"
                            "; connect-src 'self'; worker-src blob:")
                headers["Content-Security-Policy"] = csp
                headers["Strict-Transport-Security"] = "max-age=31536000"
                headers["X-Content-Type-Options"] = "nosniff"
                headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
                headers["Permissions-Policy"] = "microphone=(self), camera=(), geolocation=(self)"
                headers["X-Request-ID"] = request.state.request_id
                if "authorization" in request.headers:
                    headers["Cache-Control"] = "no-store"
                if scope["path"].startswith("/api/share/"):
                    headers["Cache-Control"] = "no-store"
                    headers["Referrer-Policy"] = "no-referrer"
            await send(message)

        await self.app(scope, receive, hardened_send)


class RequestGuard:
    def __init__(self, app: ASGIApp, trust_proxy: bool, voice_limit: int, planning_limit: int) -> None:
        self.app = app
        self.trust_proxy = trust_proxy
        self.ip = {kind: RateLimiter(cap, 60) for kind, cap in {"planning": planning_limit, "reports": 10}.items()}
        self.accounts = {kind: RateLimiter(cap, 60)
                         for kind, cap in {"planning": 20, "reports": 10, "voice": voice_limit}.items()}

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request = Request(scope)
        started = False

        async def tracked_send(message: Message) -> None:
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
            await send(message)

        try:
            if request.method == "POST":
                await self.limit_request(request)
            if request.method in {"POST", "PUT", "PATCH"}:
                receive = await self.bounded_body(request, receive)
            await self.app(scope, receive, tracked_send)
        except HTTPException as exc:
            if started:
                raise
            code = {400: "invalid_request", 413: "payload_too_large", 429: "rate_limited"}.get(exc.status_code,
                                                                                              "http_error")
            await error_response(request, exc.status_code, code, exc.detail, exc.headers)(scope, receive, send)
        except Exception as exc:
            if started:
                raise
            log.error("Request %s failed (%s)", request.state.request_id, type(exc).__name__)
            await error_response(request, 500, "internal_error", "The request failed.")(scope, receive, send)

    async def limit_request(self, request: Request) -> None:
        path = request.url.path
        kinds = []
        if path in PLANNING:
            kinds.append("planning")
        if path in REPORTING:
            kinds.append("reports")
        if path.startswith("/api/voice/") or path == "/api/lab/voice-sample":
            kinds.append("voice")
        if not kinds:
            return
        for kind in kinds:
            # The existing voice and legacy report routes retain their per-IP dependency limits.
            if kind == "planning" or path == "/api/reports/quick":
                self.hit(self.ip[kind], rate_key(client_ip(request, self.trust_proxy)))
        scheme, _, token = request.headers.get("authorization", "").partition(" ")
        if scheme.lower() == "bearer" and token:
            user = await run_in_threadpool(verify_access_token, token)
            request.state.verified_user = user
            for kind in kinds:
                self.hit(self.accounts[kind], user.id)

    @staticmethod
    def hit(limiter: RateLimiter, key: str) -> None:
        retry = limiter.hit(key)
        if retry is not None:
            raise HTTPException(429, "Too many requests. Try again shortly.",
                                headers={"Retry-After": str(max(1, math.ceil(retry)))})

    @staticmethod
    async def bounded_body(request: Request, receive: Receive) -> Receive:
        content_type = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
        audio_path = request.url.path.startswith("/api/voice/") or request.url.path == "/api/lab/voice-sample"
        limit = AUDIO_LIMIT if audio_path and content_type == "multipart/form-data" else JSON_LIMIT
        length = request.headers.get("content-length")
        if length is not None:
            if not length.isascii() or not length.isdecimal() or len(length) > 20:
                raise HTTPException(400, "Invalid Content-Length.")
            if int(length) > limit:
                raise HTTPException(413, "Request body is too large.")

        body = bytearray()
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                async def disconnected() -> Message:
                    return {"type": "http.disconnect"}
                return disconnected
            chunk = message.get("body", b"")
            if len(body) + len(chunk) > limit:
                raise HTTPException(413, "Request body is too large.")
            body.extend(chunk)
            if not message.get("more_body", False):
                break

        delivered = False

        async def replay() -> Message:
            nonlocal delivered
            if not delivered:
                delivered = True
                return {"type": "http.request", "body": bytes(body), "more_body": False}
            return await receive()

        return replay


def install(app: FastAPI, origins: list[str], production: bool, trust_proxy: bool, voice_limit: int,
            *, planning_limit: int = 30) -> None:
    exact = []
    schemes = {"https"} if production else {"http", "https"}
    for origin in origins:
        parsed = urlsplit(origin)
        if origin == "null" and not production:
            exact.append(origin)
        elif (parsed.scheme in schemes and parsed.hostname and "*" not in origin
              and not (parsed.username or parsed.path or parsed.query or parsed.fragment)):
            exact.append(origin)
    app.add_middleware(RequestGuard, trust_proxy=trust_proxy, voice_limit=voice_limit, planning_limit=planning_limit)
    app.add_middleware(CORSMiddleware, allow_origins=exact, allow_methods=["GET", "POST", "PUT", "DELETE"],
                       allow_headers=["Authorization", "Content-Type", "X-Lab-Token"],
                       expose_headers=["Retry-After", "X-Request-ID"])
    app.add_middleware(SecurityHeaders, production=production)
    app.add_exception_handler(StarletteHTTPException, http_error)
    app.add_exception_handler(RequestValidationError, validation_error)
    logging.getLogger("uvicorn.access").addFilter(ShareLogFilter())
