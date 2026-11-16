"""W0.5: synthesise the demo's spoken answers ONCE, ahead of time, into data/audio_cache/ (paid Sarvam calls).

    uv run python scripts/build_audio_cache.py --dry-run    # list what is missing; calls nothing, costs nothing
    uv run python scripts/build_audio_cache.py              # synthesise ONLY what is missing or fails its hash

The sentences are not typed here: they are the ``speech`` the engine returns for each demo vehicle's dispatch
(fixed templates filled with engine numbers, on the SIMULATED fleet), so what the cache holds is exactly what the demo
speaks. One paid text-to-speech call per missing sentence; a re-run with nothing missing makes no call. Every call is
logged (path, model, ok, HTTP status, ms; never the text or the key) in results/audio_cache_build.json.
When a template, a number or the TTS model changes the sentence changes, its clip is "missing" again, and the offline
test (tests/test_offline_demo.py) fails until this script has been run.
"""

from __future__ import annotations

import base64
import json
import sys
from datetime import datetime, timezone

from api.service import FLEET_META, EngineService, demo_dispatch_request
from verity.audio_cache import AudioCache
from verity.paths import RESULTS_DIR
from verity.voice import TTS_MODEL, SarvamClient, VoiceUnavailable

OUTPUT = RESULTS_DIR / "audio_cache_build.json"


def demo_sentences(svc: EngineService) -> list[tuple[str, str, str]]:
    """(language, text, source) for every sentence the demo speaks, straight from the engine."""
    out = []
    for vehicle in FLEET_META:
        response = svc.dispatch(demo_dispatch_request(vehicle))
        for language, text in response["speech"].items():
            out.append((language, text, f"dispatch {vehicle} ({response['decision']})"))
    return out


def main(argv: list[str]) -> int:
    sys.stdout.reconfigure(encoding="utf-8")  # Hindi on a Windows console
    svc = EngineService(graph_backend="memory")
    try:
        sentences = demo_sentences(svc)
    finally:
        svc.close()
    cache = AudioCache()
    source = {(language, text): why for language, text, why in sentences}
    todo = cache.missing([(language, text) for language, text, _ in sentences])
    print(f"{len(sentences)} demo sentences; {len(sentences) - len(todo)} already cached and valid; {len(todo)} to make")
    for language, text in todo:
        print(f"  would synthesise [{language}] {source[(language, text)]}: {len(text)} characters")
    if "--dry-run" in argv or not todo:
        return 0
    client = SarvamClient.from_env()
    if client is None:
        print("SARVAM_API_KEY missing in .env: nothing called")
        return 1
    made, failed = [], []
    for language, text in todo:
        try:
            audio = client.tts(text, language)
            cache.add(text, language, base64.b64decode(audio["audio_base64"]), tts_model=TTS_MODEL,
                      source=source[(language, text)])
            made.append({"language": language, "source": source[(language, text)], "characters": len(text)})
        except (VoiceUnavailable, ValueError) as exc:  # a failed call or a body that is not a WAV: record, keep going
            failed.append({"language": language, "source": source[(language, text)], "error": str(exc)[:200]})
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps({
        "experiment": "W0.5 audio cache build", "is_simulated": True,
        "label": "Sarvam TTS of engine sentences on the SIMULATED demo fleet",
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "tts_model": TTS_MODEL,
        "sentences_total": len(sentences), "made_now": made, "failed": failed, "paid_calls": client.calls},
        indent=1, ensure_ascii=False), encoding="utf-8")
    print(f"made {len(made)}, failed {len(failed)}, paid calls {len(client.calls)} -> {OUTPUT.name}")
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
