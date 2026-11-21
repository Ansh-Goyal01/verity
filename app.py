"""Vercel entrypoint for the public website and the existing API.

The repo's own ``engine/`` goes first on the import path so ``verity`` resolves to the checked-out source, whose
``paths.REPO_ROOT`` finds ``data/``, even if the platform also installed the package into site-packages.
"""

import mimetypes
import os
import re
import sys
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.datastructures import MutableHeaders
from starlette.types import Message, Receive, Scope, Send

ROOT = Path(__file__).resolve().parent
for entry in (ROOT / "engine", ROOT):
    if str(entry) not in sys.path:
        sys.path.insert(0, str(entry))

from api.main import app as api, lifespan  # noqa: E402

class Site(FastAPI):
    """Apply site policy to the first response frame, including redirects and error pages."""

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                headers.setdefault("X-Frame-Options", "DENY")
                headers.setdefault("Content-Security-Policy", "default-src 'none'; frame-ancestors 'none'; base-uri 'none'")
            await send(message)

        await super().__call__(scope, receive, send_with_headers)


# Keep the API's startup and shutdown hooks when serving it as a mounted app.
app = Site(openapi_url=None, lifespan=lifespan)
mimetypes.add_type("image/webp", ".webp")
mimetypes.add_type("font/woff2", ".woff2")


# Vercel serves the browser API on this same origin. Permit just the configured
# Supabase project for sign-in. Inline script hashes cover the three legacy HTML pages.
def frontend_csp() -> str:
    project = os.environ.get("SUPABASE_URL", "").rstrip("/")
    connections = "'self'"
    if re.fullmatch(r"https://[A-Za-z0-9.-]+(?::[0-9]{1,5})?", project):
        connections += " " + project
    return (
        "default-src 'self'; object-src 'none'; base-uri 'self'; frame-ancestors 'none'; form-action 'self'; "
        "script-src 'self' 'sha256-Jwd4k1QAsafYuY52nZJOWTEDTc1DTKLLQBM1TIiE9iQ=' "
        "'sha256-A7t06XADOxD5KB/lDBkghPjAZ+lhNoPsspbym8TWcUk=' "
        "'sha256-LUB9ZwX709iWs4OwnDwUDixMoc4R5rYM0NcPb8UzH7I='; "
        "style-src 'self' 'unsafe-inline'; img-src 'self' data: https://tile.openstreetmap.org; "
        "media-src 'self' data: blob:; connect-src " + connections
    )


class FrontendFiles(StaticFiles):
    async def get_response(self, path: str, scope: Scope) -> Response:
        response = await super().get_response(path, scope)
        if response.headers.get("content-type", "").startswith("text/html"):
            response.headers["Content-Security-Policy"] = frontend_csp()
        return response


@app.api_route("/", methods=["GET", "HEAD"])
@app.api_route("/index.html", methods=["GET", "HEAD"])
async def homepage(request: Request) -> RedirectResponse:
    destination = "/web/home.html"
    if request.url.query:
        destination += "?" + request.url.query
    return RedirectResponse(destination)


@app.api_route("/web/js/config.js", methods=["GET", "HEAD"])
async def browser_config() -> Response:
    # Standalone copies of web/ keep their offline default.
    return Response("window.VERITY_CONFIG = { apiBaseUrl: window.location.origin };\n",
                    media_type="application/javascript")


@app.api_route("/design/tokens.css", methods=["GET", "HEAD"])
async def design_tokens() -> FileResponse:
    return FileResponse(ROOT / "design" / "tokens.css", media_type="text/css")


app.mount("/web", FrontendFiles(directory=ROOT / "web", html=True), name="web")
app.mount("/logger", FrontendFiles(directory=ROOT / "logger", html=True), name="logger")
app.mount("/data/audio_cache", StaticFiles(directory=ROOT / "data" / "audio_cache"), name="demo-audio")
app.mount("/", api)
