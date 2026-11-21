"""Pre-generated spoken answers for the demo, kept on disk for when the voice service is out of reach (W0.5, F9).

A venue's wifi is the one thing the demo cannot control. The sentences the demo speaks are fixed templates filled
with engine numbers (docs/PLAN rule: a language model never writes a number), so they can be synthesised once, ahead
of time, and replayed. ``data/audio_cache/`` holds one WAV per (language, exact text) and a manifest that says what
each clip is. Nothing here calls the network: ``scripts/build_audio_cache.py`` makes the clips, the API serves them
(flagged ``cached: true``) only when live text-to-speech fails.

Integrity: every clip is checked against the sha256 recorded when it was made. A missing, truncated or swapped file
is a miss, never audio that says something else.
"""

from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Iterable
from pathlib import Path

from .paths import DATA_DIR, writable_dir

log = logging.getLogger(__name__)

AUDIO_CACHE_DIR = DATA_DIR / "audio_cache"
MANIFEST = "manifest.json"
NOTE = ("Sarvam text-to-speech of the demo's spoken answers, made ahead of time by scripts/build_audio_cache.py from "
        "sentences the engine wrote on the SIMULATED demo fleet. Served only when live text-to-speech fails.")


def audio_key(language: str, text: str) -> str:
    """The same sentence in the same language always has the same key; one changed character is a different clip."""
    return hashlib.sha256(f"{language}\n{text}".encode("utf-8")).hexdigest()[:24]


class AudioCache:
    def __init__(self, directory: Path | None = None) -> None:
        self.directory = AUDIO_CACHE_DIR if directory is None else directory  # the committed clips are read from here
        self.write_directory = writable_dir(self.directory)  # new clips go here (/tmp/verity/... on Vercel)

    def _dirs(self) -> list[Path]:
        return [self.directory] if self.write_directory == self.directory else [self.directory, self.write_directory]

    def _manifest(self, directory: Path | None = None) -> dict:
        path = (self.directory if directory is None else directory) / MANIFEST
        if not path.is_file():
            return {"is_simulated": True, "note": NOTE, "entries": {}}
        return json.loads(path.read_text(encoding="utf-8"))

    def entries(self) -> list[dict]:
        merged: dict = {}
        for directory in self._dirs():
            merged.update(self._manifest(directory)["entries"])
        return list(merged.values())

    def lookup(self, text: str, language: str) -> bytes | None:
        for directory in self._dirs():
            wav = self._lookup_in(directory, text, language)
            if wav is not None:
                return wav
        return None

    def _lookup_in(self, directory: Path, text: str, language: str) -> bytes | None:
        entry = self._manifest(directory)["entries"].get(audio_key(language, text))
        if entry is None:
            return None
        try:
            wav = (directory / entry["file"]).read_bytes()
        except OSError:
            log.warning("audio cache: %s is listed but cannot be read", entry["file"])
            return None
        if len(wav) != entry["bytes"] or hashlib.sha256(wav).hexdigest() != entry["sha256"]:
            log.warning("audio cache: %s does not match its recorded hash; not served", entry["file"])
            return None
        return wav

    def has(self, text: str, language: str) -> bool:
        return self.lookup(text, language) is not None

    def missing(self, sentences: Iterable[tuple[str, str]]) -> list[tuple[str, str]]:
        """The (language, text) pairs that have no valid clip yet: what a rebuild still has to pay for."""
        return [(language, text) for language, text in sentences if not self.has(text, language)]

    def add(self, text: str, language: str, wav: bytes, *, tts_model: str, source: str) -> str:
        if not wav.startswith(b"RIFF"):
            raise ValueError("not a WAV file (an error body must never be stored as audio)")
        key = audio_key(language, text)
        out = self.write_directory
        out.mkdir(parents=True, exist_ok=True)
        (out / f"{key}.wav").write_bytes(wav)
        manifest = self._manifest(out)
        manifest["entries"][key] = {"language": language, "text": text, "file": f"{key}.wav", "bytes": len(wav),
                                    "sha256": hashlib.sha256(wav).hexdigest(), "tts_model": tts_model, "source": source}
        body = json.dumps(manifest, indent=1, ensure_ascii=False, sort_keys=True) + "\n"
        with (out / MANIFEST).open("w", encoding="utf-8", newline="\n") as handle:  # LF on every machine
            handle.write(body)
        return key
