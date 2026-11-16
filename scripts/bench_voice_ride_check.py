"""W3.1: measure the voice stages of the spoken ride-check and pick the defaults by measurement, not by the vendor's label.

    uv run python scripts/bench_voice_ride_check.py          # dry run: lists the paid calls, makes none
    uv run python scripts/bench_voice_ride_check.py --run    # at most MAX_PAID_CALLS paid Sarvam calls

What is measured (every call goes through ``SarvamClient`` and so its paid-call log):
1. Two driver sentences (Hinglish, Hindi) are synthesised once by Sarvam TTS: SYNTHETIC audio, not a real driver. Real speech
   (accent, noise, a phone microphone) will differ; Ansh's own Hindi/Hinglish test is the real check.
2. STT: saaras:v3 transcribe / v3 codemix / v4 transcribe, each on both clips, twice.
3. Chat fallback: sarvam-105b with reasoning low / none / omit, and sarvam-105b-conversations, on a sentence the rules cannot
   finish (a Hindi number WORD), twice, with a generous 30 s limit so the true latency shows against the 4 s the app allows.
A configuration is "correct" only if the slots it yields are exactly 45 %, Sector 18, IGI Airport T3. Nothing is applied
automatically: the numbers go to results/voice_ride_check_latency.json and the defaults are chosen from them in docs/DECISIONS.md.
"""

from __future__ import annotations

import base64
import json
import statistics
import sys
from dataclasses import replace
from datetime import datetime, timezone

from verity.paths import RESULTS_DIR
from verity.places import Gazetteer
from verity.ride_voice import LLM_TIMEOUT_S, complete_with_llm, parse_ride_request
from verity.voice import SarvamClient, VoiceUnavailable

OUTPUT = RESULTS_DIR / "voice_ride_check_latency.json"
MAX_PAID_CALLS = 24
REPS = 2
CLIPS = {"hinglish": ("en-IN", "Battery 45 percent hai, Sector 18 se airport ka ride mila hai"),
         "hindi": ("hi-IN", "बैटरी 45 प्रतिशत है, सेक्टर 18 से एयरपोर्ट का राइड मिला है")}
STT_CONFIGS = [("saaras:v3", "transcribe"), ("saaras:v3", "codemix"), ("saaras:v4", "transcribe")]
CHAT_CONFIGS = [("sarvam-105b", "low"), ("sarvam-105b", "none"), ("sarvam-105b", "omit"), ("sarvam-105b-conversations", "omit")]
CHAT_CASE = "बैटरी पैंतालीस प्रतिशत है, सेक्टर 18 से एयरपोर्ट का राइड"  # a number WORD: the rules leave it to the model
EXPECTED = (45, "noida-sector-18", "igi-airport-t3")
CHAT_GENEROUS_TIMEOUT_S = 30.0


def planned_calls() -> int:
    return len(CLIPS) + len(STT_CONFIGS) * len(CLIPS) * REPS + len(CHAT_CONFIGS) * REPS


def yielded(slots) -> tuple:
    return (slots.soc_pct, slots.pickup.id if slots.pickup else None, slots.drop.id if slots.drop else None)


def summarise(stage: str, model: str, option: str, samples: list[dict]) -> dict:
    ms = [s["ms"] for s in samples]
    return {"stage": stage, "model": model, "option": option, "samples": samples, "n": len(samples),
            "ok": sum(s["ok"] for s in samples), "correct": sum(s["correct"] for s in samples),
            "median_ms": int(statistics.median(ms)), "max_ms": max(ms)}


def guard(client: SarvamClient) -> None:
    if len(client.calls) >= MAX_PAID_CALLS:
        raise SystemExit(f"stopped: {len(client.calls)} paid calls already made (cap {MAX_PAID_CALLS})")


def timed(client: SarvamClient, call) -> tuple[object, dict]:
    """Run one paid call; return (result or None, its entry in the paid-call log)."""
    guard(client)
    result = None
    try:
        result = call()
    except VoiceUnavailable:
        pass  # recorded by the client's paid-call log (class name and HTTP status, never text or key)
    return result, client.calls[-1]


def bench_stt(base: SarvamClient, clips: dict[str, bytes], gaz: Gazetteer) -> list[dict]:
    out = []
    for model, mode in STT_CONFIGS:
        client = replace(base, models=replace(base.models, stt_model=model, stt_mode=mode), calls=base.calls)
        samples = []
        for name, audio in clips.items():
            for rep in range(REPS):
                heard, log = timed(client, lambda a=audio: client.stt_full(a, "ride.wav", "audio/wav", "unknown"))
                text = heard["transcript"] if heard else ""
                code = heard["language_code"] if heard else None
                slots = parse_ride_request(text, gaz, code)
                samples.append({"clip": name, "rep": rep, "ms": log["ms"], "ok": log["ok"], "http_status": log["http_status"],
                                "transcript": text, "language_code": code, "correct": yielded(slots) == EXPECTED})
        out.append(summarise("stt", model, mode, samples))
    return out


def bench_chat(base: SarvamClient, gaz: Gazetteer) -> list[dict]:
    out = []
    for model, reasoning in CHAT_CONFIGS:
        client = replace(base, models=replace(base.models, chat_model=model, chat_reasoning=reasoning), calls=base.calls)
        samples = []
        for rep in range(REPS):
            guard(client)
            slots = complete_with_llm(parse_ride_request(CHAT_CASE, gaz, "hi-IN"), CHAT_CASE, client, gaz,
                                      timeout=CHAT_GENEROUS_TIMEOUT_S)
            log = client.calls[-1]
            samples.append({"rep": rep, "ms": log["ms"], "ok": log["ok"], "http_status": log["http_status"],
                            "within_app_limit": log["ok"] and log["ms"] <= 1000 * LLM_TIMEOUT_S,
                            "correct": yielded(slots) == EXPECTED})
        out.append(summarise("chat", model, f"reasoning={reasoning}", samples))
    return out


def main(argv: list[str]) -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    print(f"plan: {len(CLIPS)} TTS + {len(STT_CONFIGS) * len(CLIPS) * REPS} STT + {len(CHAT_CONFIGS) * REPS} chat "
          f"= {planned_calls()} paid calls (cap {MAX_PAID_CALLS})")
    if "--run" not in argv:
        print("dry run: nothing called. Add --run to make the calls.")
        return 0
    client = SarvamClient.from_env()
    if client is None:
        print("SARVAM_API_KEY missing: nothing called")
        return 1
    gaz = Gazetteer.from_file()
    try:
        clips = {name: base64.b64decode(client.tts(text, lang)["audio_base64"]) for name, (lang, text) in CLIPS.items()}
    except VoiceUnavailable as exc:
        print(f"could not make the test audio: {exc}")
        return 1
    configs = bench_stt(client, clips, gaz) + bench_chat(client, gaz)
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps({
        "experiment": "W3.1 voice ride-check latency", "is_simulated": True,
        "label": "SYNTHETIC audio (Sarvam TTS reading our own sentences), not a real driver; latency measured from this laptop",
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "app_llm_limit_s": LLM_TIMEOUT_S,
        "expected_slots": {"soc_pct": EXPECTED[0], "pickup": EXPECTED[1], "drop": EXPECTED[2]}, "chat_case": CHAT_CASE,
        "clips": {name: {"language": lang, "text": text} for name, (lang, text) in CLIPS.items()},
        "configs": configs, "paid_calls": client.calls}, indent=1, ensure_ascii=False), encoding="utf-8")
    for c in configs:
        print(f"{c['stage']:4} {c['model']:27} {c['option']:18} ok {c['ok']}/{c['n']} correct {c['correct']}/{c['n']} "
              f"median {c['median_ms']} ms max {c['max_ms']} ms")
    print(f"{len(client.calls)} paid calls -> {OUTPUT.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
