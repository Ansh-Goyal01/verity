"""W0.5: the demo's spoken answers, pre-generated and kept on disk, so the voice still answers with the wifi off.

The cache is keyed by (language, exact text) and every clip is verified against the hash recorded when it was made:
a corrupted or swapped file is a miss, never audio that says something else. Synthetic bytes only; no network.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from verity.audio_cache import AudioCache, audio_key

HINDI = "जाइए। आगरा तक पहुँचने की संभावना 95 प्रतिशत या उससे अधिक है।"
ENGLISH = "Go. The chance of reaching Agra is 95 percent or more."
WAV = b"RIFF\x24\x00\x00\x00WAVEfmt synthetic-clip-bytes"


@pytest.fixture
def cache(tmp_path: Path) -> AudioCache:
    return AudioCache(tmp_path / "audio_cache")


def test_the_key_depends_on_the_language_and_the_exact_text() -> None:
    assert audio_key("en-IN", ENGLISH) == audio_key("en-IN", ENGLISH)
    assert audio_key("en-IN", ENGLISH) != audio_key("hi-IN", ENGLISH)
    assert audio_key("en-IN", ENGLISH) != audio_key("en-IN", ENGLISH + " ")  # one character is a different clip


def test_a_clip_added_is_found_again_byte_for_byte(cache: AudioCache) -> None:
    key = cache.add(ENGLISH, "en-IN", WAV, tts_model="bulbul:v3", source="test")
    assert key == audio_key("en-IN", ENGLISH)
    assert cache.lookup(ENGLISH, "en-IN") == WAV and cache.has(ENGLISH, "en-IN")


def test_the_manifest_says_what_each_clip_is_and_that_it_is_simulated(cache: AudioCache) -> None:
    cache.add(HINDI, "hi-IN", WAV, tts_model="bulbul:v3", source="dispatch V12")
    manifest = json.loads((cache.directory / "manifest.json").read_text(encoding="utf-8"))
    entry = manifest["entries"][audio_key("hi-IN", HINDI)]
    assert manifest["is_simulated"] is True
    assert entry["text"] == HINDI and entry["language"] == "hi-IN" and entry["tts_model"] == "bulbul:v3"
    assert entry["bytes"] == len(WAV) and len(entry["sha256"]) == 64 and entry["source"] == "dispatch V12"
    assert (cache.directory / entry["file"]).read_bytes() == WAV
    assert HINDI in (cache.directory / "manifest.json").read_text(encoding="utf-8")  # readable, not \u-escaped


def test_an_unknown_sentence_or_language_is_a_miss(cache: AudioCache) -> None:
    cache.add(ENGLISH, "en-IN", WAV, tts_model="bulbul:v3", source="test")
    assert cache.lookup("Something never generated", "en-IN") is None
    assert cache.lookup(ENGLISH, "hi-IN") is None


def test_an_empty_or_missing_cache_is_all_misses_not_an_error(tmp_path: Path) -> None:
    assert AudioCache(tmp_path / "does-not-exist").lookup(ENGLISH, "en-IN") is None
    assert AudioCache(tmp_path / "does-not-exist").entries() == []


def test_a_corrupted_clip_is_a_miss_never_wrong_audio(cache: AudioCache) -> None:
    cache.add(ENGLISH, "en-IN", WAV, tts_model="bulbul:v3", source="test")
    clip = next(cache.directory.glob("*.wav"))
    clip.write_bytes(WAV[:-3] + b"xyz")  # same length, different bytes
    assert cache.lookup(ENGLISH, "en-IN") is None


def test_a_clip_whose_file_is_gone_is_a_miss(cache: AudioCache) -> None:
    cache.add(ENGLISH, "en-IN", WAV, tts_model="bulbul:v3", source="test")
    next(cache.directory.glob("*.wav")).unlink()
    assert cache.lookup(ENGLISH, "en-IN") is None


def test_adding_the_same_sentence_again_replaces_it_without_duplicates(cache: AudioCache) -> None:
    cache.add(ENGLISH, "en-IN", WAV, tts_model="bulbul:v3", source="first")
    cache.add(ENGLISH, "en-IN", WAV + b"2", tts_model="bulbul:v3", source="second")
    assert len(cache.entries()) == 1 and len(list(cache.directory.glob("*.wav"))) == 1
    assert cache.lookup(ENGLISH, "en-IN") == WAV + b"2"


def test_missing_lists_only_the_sentences_not_yet_generated_so_a_rebuild_costs_nothing(cache: AudioCache) -> None:
    cache.add(ENGLISH, "en-IN", WAV, tts_model="bulbul:v3", source="test")
    wanted = [("en-IN", ENGLISH), ("hi-IN", HINDI)]
    assert cache.missing(wanted) == [("hi-IN", HINDI)]
    assert cache.missing([("en-IN", ENGLISH)]) == []


def test_a_clip_that_fails_its_hash_counts_as_missing_too(cache: AudioCache) -> None:
    cache.add(ENGLISH, "en-IN", WAV, tts_model="bulbul:v3", source="test")
    next(cache.directory.glob("*.wav")).write_bytes(b"corrupt")
    assert cache.missing([("en-IN", ENGLISH)]) == [("en-IN", ENGLISH)]


def test_the_committed_cache_is_valid_when_present() -> None:
    committed = AudioCache()
    for entry in committed.entries():
        assert committed.lookup(entry["text"], entry["language"]) is not None, f"{entry['file']} fails its recorded hash"
