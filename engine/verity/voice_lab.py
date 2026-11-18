"""Measuring the voice path on real people (guide W3.5): the sample record, the store, and the summary.

One sample is one consented utterance, scored against what the speaker was asked to say. Nothing identifying is kept: no
audio, no name, no address; ``speaker_id`` is an anonymous label the operator hands out ("s07"), used only to count speakers.
``summarise`` turns the samples into the numbers of ``results/voice_real.json``: accuracy with Wilson intervals by language,
speech-to-text latency p50/p95, and the same audio replayed through each model and mode. Pure functions plus one small
file-backed store; the network calls are made by the caller and passed in.
"""

from __future__ import annotations

import json
import threading
import time
from collections import defaultdict
from dataclasses import replace
from pathlib import Path
from typing import Any, Callable, Sequence

import numpy as np

from .evidence import wilson
from .voice import VoiceUnavailable

SAMPLE_SCHEMA = "verity.voice_sample.v1"
SUMMARY_SCHEMA = "verity.voice_real.v1"
SPOKEN_LANGUAGES = ("hindi", "hinglish", "english")
# The same four cells the S5 latency bench measured on synthetic audio (scripts/bench_voice_ride_check.py).
COMPARE_CONFIGS = (("saaras:v3", "transcribe"), ("saaras:v3", "codemix"), ("saaras:v4", "transcribe"), ("saaras:v4", "codemix"))
MIN_SAMPLES = 30  # guide W3.5 target: at least 30 utterances ...
MIN_SPEAKERS = 10  # ... from at least 10 people


def with_models(voice: Any, model: str, mode: str) -> Any:
    """A copy of the (dataclass) client that uses ``model``; it shares the HTTP client and the paid-call log."""
    return replace(voice, models=replace(voice.models, stt_model=model, stt_mode=mode))


def compare_configs(voice: Any, audio: bytes, filename: str, content_type: str, language: str,
                    score: Callable[[str], bool | None], configs: Sequence[tuple[str, str]] = COMPARE_CONFIGS) -> list[dict]:
    """The same audio through every (model, mode). A failed call is kept as a row with its error: dropping it would flatter the config."""
    rows = []
    for model, mode in configs:
        started = time.perf_counter()
        try:
            heard = with_models(voice, model, mode).stt_full(audio, filename, content_type, language, mode)
        except VoiceUnavailable as exc:
            rows.append({"model": model, "mode": mode, "transcript": None, "correct": None, "ms": _ms(started), "error": str(exc)})
            continue
        transcript = heard["transcript"]
        rows.append({"model": model, "mode": mode, "transcript": transcript, "correct": score(transcript),
                     "ms": _ms(started), "error": None})
    return rows


def _ms(since: float) -> int:
    return int(round(1000 * (time.perf_counter() - since)))


class SampleStore:
    """Append-only JSON lines, one sample per line. A damaged line is counted on read, never silently skipped."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.Lock()

    def append(self, sample: dict) -> None:
        line = json.dumps(sample, ensure_ascii=False, separators=(",", ":"))
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8", newline="\n") as handle:
                handle.write(line + "\n")

    def read(self) -> tuple[list[dict], int]:
        """(samples, number of lines that could not be read)."""
        if not self.path.is_file():
            return [], 0
        samples, skipped = [], 0
        for line in self.path.read_text(encoding="utf-8").split("\n"):  # not splitlines(): U+2028 etc. may sit inside a transcript
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except ValueError:
                skipped += 1
                continue
            if _is_real_sample(row):
                samples.append(row)
            else:
                skipped += 1
        return samples, skipped


def _is_real_sample(row: object) -> bool:
    """Only a consented, non-simulated row of this schema counts: a hand-edited or simulated line must not become 'real' evidence."""
    return (isinstance(row, dict) and row.get("schema") == SAMPLE_SCHEMA and row.get("consent") is True
            and row.get("is_simulated") is False)


def _rate(outcomes: Sequence[bool | None]) -> dict:
    scored = [o for o in outcomes if o is not None]
    correct = sum(1 for o in scored if o)
    return {"scored": len(scored), "correct": correct,
            "accuracy": round(correct / len(scored), 4) if scored else None, "wilson95": wilson(correct, len(scored))}


def _latency(values: Sequence[float]) -> dict:
    if not values:
        return {"n": 0, "p50": None, "p95": None}
    return {"n": len(values), "p50": round(float(np.percentile(values, 50)), 1), "p95": round(float(np.percentile(values, 95)), 1)}


def _comparison_table(samples: Sequence[dict]) -> list[dict]:
    cells: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for sample in samples:
        for c in sample.get("comparisons") or []:
            cells[(c["model"], c["mode"])].append(c)
    rows = []
    for (model, mode), calls in cells.items():
        over_all = _rate([bool(c.get("correct")) for c in calls])  # a failed call counts AGAINST the config
        row = {"model": model, "mode": mode, "calls": len(calls), "errors": sum(1 for c in calls if c.get("error")),
               **_rate([c.get("correct") for c in calls]),  # accuracy of the calls that returned a transcript
               "success_over_all_calls": over_all["accuracy"], "success_over_all_calls_wilson95": over_all["wilson95"],
               "ms": _latency([c["ms"] for c in calls if c.get("ms") is not None and not c.get("error")])}
        rows.append(row)
    # Ranked by the lower end of the all-calls interval: a config that errors often, or has few calls, cannot top the table on luck.
    rows.sort(key=lambda r: (-(r["success_over_all_calls_wilson95"] or [0.0])[0], r["ms"]["p50"] or float("inf")))
    return rows


def summarise(samples: Sequence[dict], *, skipped_lines: int = 0) -> dict:
    """The contents of ``results/voice_real.json``. ``preliminary`` stays true until the guide's 30 samples from 10 speakers exist."""
    speakers = {s["speaker_id"] for s in samples if s.get("speaker_id")}
    reasons = []
    if len(samples) < MIN_SAMPLES:
        reasons.append(f"{len(samples)} samples; the target is at least {MIN_SAMPLES}")
    if len(speakers) < MIN_SPEAKERS:
        reasons.append(f"{len(speakers)} speakers; the target is at least {MIN_SPEAKERS}")
    by_language: dict[str, list[dict]] = defaultdict(list)
    for s in samples:
        by_language[s.get("spoken_language") or "unknown"].append(s)
    return {
        "schema": SUMMARY_SCHEMA,
        "is_simulated": False,
        "n_samples": len(samples),
        "n_speakers": len(speakers),
        "skipped_lines": skipped_lines,
        "preliminary": bool(reasons),
        "preliminary_reason": "; ".join(reasons) or None,
        "battery": _rate([s.get("battery_correct") for s in samples]),
        "charger_match": _rate([s.get("charger_correct") for s in samples]),
        "stt_latency_ms": _latency([s["stt_ms"] for s in samples if s.get("stt_ms") is not None]),
        "by_language": {
            lang: {"n_samples": len(rows), "n_speakers": len({r["speaker_id"] for r in rows if r.get("speaker_id")}),
                   "battery": _rate([r.get("battery_correct") for r in rows]),
                   "charger_match": _rate([r.get("charger_correct") for r in rows]),
                   "stt_latency_ms": _latency([r["stt_ms"] for r in rows if r.get("stt_ms") is not None])}
            for lang, rows in sorted(by_language.items())},
        "model_comparison": _comparison_table(samples),
        "note": ("Accuracy is the share of utterances where the parsed value equals what the speaker was asked to say. "
                 "Latency is the speech-to-text call only. model_comparison replays the same audio through every model and mode; "
                 "failed calls are counted in errors, never dropped, and model_comparison is ranked by success over ALL calls. "
                 "Speakers are anonymous labels that nothing verifies. Wilson intervals treat utterances as independent, but "
                 "utterances from one speaker are correlated, so they are too narrow. Rows that are not consented, non-simulated "
                 "samples of the current schema are skipped and counted in skipped_lines. Per-language rows can rest on few samples: "
                 "read n_samples before the percentage."),
    }
