"""Vercel runtime paths and production website routing."""

from __future__ import annotations

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
