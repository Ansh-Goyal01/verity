"""F10 driver demo: run the pinned airport-ride scenario through the ride check and save the answer.

Real route, weather and chargers; SIMULATED vehicle and charger reports (api/service.py DRIVER_PINS).
Writes results/driver_demo_simulated.json.

Run: uv run python scripts/driver_demo.py
"""

from __future__ import annotations

import json

from api.service import DRIVER_DEMO, EngineService
from verity.paths import RESULTS_DIR


def main() -> None:
    svc = EngineService(graph_backend="memory")
    try:
        scenario = svc.driver_demo()
        answer = svc.ride_check(dict(DRIVER_DEMO))
    finally:
        svc.close()
    answer.pop("route", None)  # the polyline is in the committed bundle; keep the result readable
    out = {"label": "SIMULATED vehicle and charger reports on a REAL route, weather and charger directory",
           "scenario": scenario, "answer": answer}
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    path = RESULTS_DIR / "driver_demo_simulated.json"
    path.write_text(json.dumps(out, indent=1), encoding="utf-8")
    ad = answer["after_drop"]
    avoided = "; ".join(f"{c['name']} - {c['note']}" for c in answer["considered"] if c["note"].startswith("Avoided"))
    print(f"{answer['decision']} ({answer['confidence']:.0%}): {answer['reason']}")
    print(f"after the drop: {ad['charger']['name']} ({ad['charger']['road_km']} km), expected {ad['expected_lost_min']} "
          f"lost minutes = Rs {ad['expected_lost_rs']} at the example rate; avoided: {avoided}")
    print(f"-> {path}")


if __name__ == "__main__":
    main()
