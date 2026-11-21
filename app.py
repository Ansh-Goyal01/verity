"""Vercel entrypoint for the public website and the existing API.

The repo's own ``engine/`` goes first on the import path so ``verity`` resolves to the checked-out source, whose
``paths.REPO_ROOT`` finds ``data/``, even if the platform also installed the package into site-packages.
"""

import mimetypes
import sys
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles

ROOT = Path(__file__).resolve().parent
for entry in (ROOT / "engine", ROOT):
    if str(entry) not in sys.path:
        sys.path.insert(0, str(entry))

from api.main import app as api, lifespan  # noqa: E402

# Keep the API's startup and shutdown hooks when serving it as a mounted app.
app = FastAPI(openapi_url=None, lifespan=lifespan)
mimetypes.add_type("image/webp", ".webp")
mimetypes.add_type("font/woff2", ".woff2")


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


app.mount("/web", StaticFiles(directory=ROOT / "web", html=True), name="web")
app.mount("/logger", StaticFiles(directory=ROOT / "logger", html=True), name="logger")
app.mount("/data/audio_cache", StaticFiles(directory=ROOT / "data" / "audio_cache"), name="demo-audio")
app.mount("/", api)
