"""F7: verify the Sarvam voice loop with at most FIVE logged paid calls -> results/voice_check.json.

    uv run python scripts/voice_check.py

1. TTS (hi-IN, bulbul:v3) of a driver note written by us: "battery 42 percent, Sector 62 wala charger band hai".
2. STT (saaras:v3, language auto-detect, mode translit = Latin script) of that audio, plus ONE Sarvam Translate
   call (Hindi number words -> English) -> battery % and a structured charger report. The speech is OUR synthetic audio, so the report is is_simulated=true and never enters the graph.
4./5. TTS of the decision templates in Hindi and English, filled from a saved engine output
   (docs/api_examples/dispatch_V12.json: demo scenario on the SIMULATED fleet), never from invented numbers.
Audio goes to results/dev/voice/ (gitignored). The key is never printed; failures record class names and HTTP status.
"""

from __future__ import annotations

import base64
import json
import sys
from datetime import datetime, timezone

from verity.chargers import load_chargers
from verity.paths import DOCS_DIR, RESULTS_DIR
from verity.voice import STT_MODEL, TTS_MODEL, SarvamClient, VoiceUnavailable
from verity.voice_loop import render_decision, soc_from_transcript, voice_report

DRIVER_NOTE_HI = "बैटरी 42 प्रतिशत है। सेक्टर 62 वाला चार्जर बंद है।"  # written by us; read aloud by TTS
DECISION_SOURCE = DOCS_DIR / "api_examples" / "dispatch_V12.json"
AUDIO_DIR = RESULTS_DIR / "dev" / "voice"
OUTPUT = RESULTS_DIR / "voice_check.json"


def _save(name: str, audio_b64: str) -> str:
    AUDIO_DIR.mkdir(parents=True, exist_ok=True)
    path = AUDIO_DIR / name
    path.write_bytes(base64.b64decode(audio_b64))
    return str(path.relative_to(RESULTS_DIR.parent)).replace("\\", "/")


def decision_fields() -> dict:
    data = json.loads(DECISION_SOURCE.read_text(encoding="utf-8"))
    resp = data.get("response", data)
    return {"decision": resp["decision"], "confidence": resp["confidence"],
            "bingo": resp["bingo_line"][0]["bingo_soc_pct"], "destination": "Agra"}


def check_driver_note(client: SarvamClient) -> dict:
    chargers, _ = load_chargers()
    try:
        audio = client.tts(DRIVER_NOTE_HI, "hi-IN")
        saved = _save("driver_note_hi.wav", audio["audio_base64"])
        heard = client.stt_full(base64.b64decode(audio["audio_base64"]), "note.wav", "audio/wav", "unknown", "translit")
    except VoiceUnavailable as exc:
        return {"ok": False, "error": str(exc)}
    english: list[str] = []

    def translate(text: str) -> str:  # one Sarvam Translate call, reused for the charger name
        english.append(client.translate(text, "en-IN"))
        return english[-1]

    try:
        soc, how = soc_from_transcript(heard["transcript"], translate, heard["language_code"])
    except VoiceUnavailable as exc:
        soc, how = None, f"translate failed: {exc}"
    record, report = voice_report(heard["transcript"], chargers, datetime.now(timezone.utc), "voice-check",
                                  from_real_person=False, translation=english[0] if english else "")
    return {"ok": True, "audio": saved, "transcript": heard["transcript"], "language_code": heard["language_code"],
            "translation_en": english[0] if english else None,
            "battery_pct": soc, "battery_read_from": how, "charger_report": record,
            "would_create_report": report is not None}


def check_decisions(client: SarvamClient) -> dict:
    fields = decision_fields()
    out = {}
    for lang in ("hi-IN", "en-IN"):
        text = render_decision(fields["decision"], lang, destination=fields["destination"],
                               confidence=fields["confidence"], bingo=fields["bingo"])
        try:
            audio = client.tts(text, lang)
            out[f"tts_decision_{lang}"] = {"ok": True, "text": text,
                                           "audio": _save(f"decision_{lang}.wav", audio["audio_base64"])}
        except VoiceUnavailable as exc:
            out[f"tts_decision_{lang}"] = {"ok": False, "text": text, "error": str(exc)}
    return out


def main() -> int:
    client = SarvamClient.from_env()
    if client is None:
        print("SARVAM_API_KEY missing in .env: nothing called")
        return 1
    steps = {"driver_note_tts_then_stt": check_driver_note(client), **check_decisions(client)}
    result = {"experiment": "F7 Sarvam voice loop check", "is_simulated": True,
              "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
              "label": "SIMULATED speech: our own TTS audio read back by STT (no real driver)",
              "stt_model": STT_MODEL, "tts_model": TTS_MODEL,
              "decision_source": "docs/api_examples/dispatch_V12.json (saved engine output, demo on the SIMULATED fleet)",
              "steps": steps, "paid_calls": client.calls}
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(result, indent=1, ensure_ascii=False), encoding="utf-8")
    print(json.dumps({k: v.get("ok") for k, v in steps.items()}), f"paid calls {len(client.calls)}")
    return 0 if all(v.get("ok") for v in steps.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
