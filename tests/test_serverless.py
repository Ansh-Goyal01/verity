"""Vercel runtime paths and production website routing."""

from __future__ import annotations

import base64
import hashlib
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from verity import paths
from verity.audio_cache import AUDIO_CACHE_DIR, AudioCache
from verity.routing import CachedClient


def test_writes_stay_in_place_locally(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("VERCEL", raising=False)
    assert paths.writable_dir(paths.CACHE_DIR) == paths.CACHE_DIR


def test_on_vercel_repo_paths_move_under_tmp_and_others_stay(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("VERCEL", "1")
    assert paths.writable_dir(paths.CACHE_DIR) == paths.SERVERLESS_TMP / "data" / "cache"
    assert paths.writable_dir(tmp_path) == tmp_path  # a path outside the repo is left alone
    assert CachedClient().cache_dir == paths.SERVERLESS_TMP / "data" / "cache"


def test_on_vercel_audio_is_read_from_the_repo_and_written_to_tmp(monkeypatch: pytest.MonkeyPatch,
                                                                    tmp_path: Path) -> None:
    monkeypatch.setenv("VERCEL", "1")
    cache = AudioCache()
    assert cache.directory == AUDIO_CACHE_DIR and cache.write_directory == paths.SERVERLESS_TMP / "data" / "audio_cache"
    committed = AudioCache().entries()
    assert committed, "the committed clips must still be found on Vercel"
    entry = committed[0]
    assert cache.lookup(entry["text"], entry["language"]) is not None

    monkeypatch.setattr(paths, "SERVERLESS_TMP", tmp_path / "tmp")
    fresh = AudioCache()
    fresh.add("a new sentence", "en-IN", b"RIFF-test", tts_model="test", source="test")
    assert (tmp_path / "tmp" / "data" / "audio_cache" / "manifest.json").is_file()
    assert fresh.lookup("a new sentence", "en-IN") == b"RIFF-test"
    assert fresh.lookup(entry["text"], entry["language"]) is not None  # committed clips still served


def test_vercel_serves_homepage_assets_and_api() -> None:
    from app import app

    client = TestClient(app)
    landing = client.get("/?source=demo")
    assert landing.url.path == "/web/home.html" and landing.url.query == b"source=demo"
    assert landing.status_code == 200 and "<title>Verity" in landing.text
    assert "default-src 'none'" not in landing.headers.get("content-security-policy", "")

    for path, content_type in [
        ("/web/drive/styles.css", "text/css"),
        ("/design/tokens.css", "text/css"),
        ("/logger/index.html", "text/html"),
        ("/web/drive/assets/arrival.webp", "image/webp"),
        ("/data/audio_cache/" + AudioCache().entries()[0]["file"], "audio/"),
    ]:
        response = client.get(path)
        assert response.status_code == 200 and response.headers["content-type"].startswith(content_type)

    assert "window.location.origin" in client.get("/web/js/config.js").text
    health = client.get("/api/health")
    assert health.status_code == 200 and health.json()["status"] == "ok"
    assert "default-src 'none'" in health.headers["content-security-policy"]
    assert client.get("/api/missing").status_code == 404


def test_html_csp_allows_only_configured_services_and_existing_inline_scripts(monkeypatch) -> None:
    from app import app

    monkeypatch.setenv("SUPABASE_URL", "https://project.example.org/")
    client = TestClient(app)
    for path in ("/web/index.html", "/web/planner.html", "/logger/index.html", "/web/js/check-mocks.html"):
        page = client.get(path)
        policy = page.headers["content-security-policy"].split("; ")
        assert "connect-src 'self' https://project.example.org" in policy
        scripts = next(part for part in policy if part.startswith("script-src"))
        assert "'unsafe-inline'" not in scripts
        for body in re.findall(r"<script>(.*?)</script>", page.text, re.S):
            digest = hashlib.sha256(body.replace("\r\n", "\n").encode()).digest()
            assert f"'sha256-{base64.b64encode(digest).decode()}'" in scripts

    monkeypatch.setenv("SUPABASE_URL", "https://untrusted.example; img-src *")
    assert client.get("/web/home.html").headers["content-security-policy"].endswith("connect-src 'self'")


@pytest.mark.parametrize("path", ["/", "/web/home.html", "/web/drive/styles.css", "/missing", "/api/health"])
def test_site_headers(path: str) -> None:
    from app import app

    response = TestClient(app, follow_redirects=False).get(path)
    assert response.headers["X-Frame-Options"] == "DENY"
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert "frame-ancestors 'none'" in response.headers["Content-Security-Policy"]
