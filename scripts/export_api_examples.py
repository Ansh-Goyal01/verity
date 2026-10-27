"""Call every API endpoint and save the REAL responses to docs/api_examples/*.json.

The frontend can swap its hand-written mocks for these files (or call the live API). Responses come from
the engine through FastAPI's test client; nothing is typed by hand. Voice endpoints are called with the
Sarvam client forced to "no key", so no real Sarvam request is ever made by this script and the saved
voice examples are the 503 the frontend must handle. ``index.json`` lists the request behind each file.
"""

from __future__ import annotations

import json
import sys
import warnings
from pathlib import Path

warnings.filterwarnings("ignore")  # starlette's httpx deprecation notice is irrelevant here

from fastapi.testclient import TestClient  # noqa: E402

from api.main import app, get_voice_client  # noqa: E402
from verity.paths import DOCS_DIR  # noqa: E402

OUT = DOCS_DIR / "api_examples"
BASE = {"origin": {"lat": 28.5355, "lon": 77.3910}, "destination": {"lat": 27.1767, "lon": 78.0081},
        "departure_time": "2026-10-28T14:00:00+05:30", "passengers": 4, "ac_on": True, "target_confidence": 0.9}
DEMO_SOC = {"V07": 82, "V12": 62, "V19": 40}  # the demo scenario's starting charge levels


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    index: list[dict] = []

    def save(name: str, method: str, path: str, resp, request_body=None) -> dict:
        data = resp.json()
        (OUT / f"{name}.json").write_text(json.dumps(data, indent=1, ensure_ascii=False), encoding="utf-8")
        index.append({"file": f"{name}.json", "method": method, "path": path, "request_body": request_body,
                      "status": resp.status_code})
        print(f"{resp.status_code} {method} {path} -> {name}.json")
        return data

    app.dependency_overrides[get_voice_client] = lambda: None  # never call Sarvam from this script
    with TestClient(app) as c:
        save("health", "GET", "/api/health", c.get("/api/health"))
        save("demo_scenario", "GET", "/api/demo/scenario", c.get("/api/demo/scenario"))
        plan_ids = {}
        for vid, soc in DEMO_SOC.items():
            req = {**BASE, "vehicle_id": vid, "start_soc_pct": soc}
            plan_ids[vid] = save(f"dispatch_{vid}", "POST", "/api/dispatch", c.post("/api/dispatch", json=req), req)["plan_id"]
        pid = plan_ids["V12"]
        upd = {"plan_id": pid, "t": "2026-10-28T14:55:00+05:30", "lat": 28.10, "lon": 77.60, "soc_pct": 45}
        save("forecast_update", "POST", "/api/forecast/update", c.post("/api/forecast/update", json=upd), upd)
        box = {"min_lat": 27.0, "min_lon": 77.0, "max_lat": 29.5, "max_lon": 78.5}
        save("chargers", "GET", "/api/chargers", c.get("/api/chargers", params=box), box)
        save("replay", "GET", f"/api/demo/replay/{pid}", c.get(f"/api/demo/replay/{pid}"))
        tts = {"text": "Divert now", "language": "hi-IN"}
        save("voice_tts_503", "POST", "/api/voice/tts", c.post("/api/voice/tts", json=tts), tts)
        save("voice_stt_503", "POST", "/api/voice/stt",
             c.post("/api/voice/stt", files={"audio": ("clip.wav", b"x", "audio/wav")}, data={"language": "hi-IN"}),
             {"multipart": {"audio": "<audio file>", "language": "hi-IN"}})
        save("error_unknown_vehicle", "POST", "/api/dispatch",
             c.post("/api/dispatch", json={**BASE, "vehicle_id": "V99", "start_soc_pct": 50}), {**BASE, "vehicle_id": "V99", "start_soc_pct": 50})
    app.dependency_overrides.clear()
    (OUT / "index.json").write_text(json.dumps({"is_simulated": True, "note": "Real engine output for the demo scenario; "
                                                "see docs/API_CONTRACT.md.", "examples": index}, indent=1), encoding="utf-8")
    print(f"wrote {len(index)} examples to {OUT}")
    return 0 if all(e["status"] in (200, 404, 503) for e in index) else 1


if __name__ == "__main__":
    sys.exit(main())
