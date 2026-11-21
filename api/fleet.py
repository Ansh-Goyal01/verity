"""Fleet booking acceptance (WP6, docs/FINALE_BUILD.md): which car can take a booking, ranked, with a release to send.

Each car is planned with the WP4 *safest* option from where it is (``start``) through the pickup to the destination,
as ONE routed trip, so a charging stop on the empty leg counts and the Monte Carlo's trip-level factors span both legs.
Cars are ranked with a reduced Monte Carlo (``VERITY_FLEET_RANK_RUNS``); the car to recommend is then re-run at the full
count, so its release is the same as ``POST /api/plan`` would give for that car (``confirm_recommended``).

Honesty: the demo fleet's car MODELS are real catalog rows, but their battery % and depots are scenario inputs chosen by
us, so every release planned from the demo fleet says ``data_sources.vehicle_state: simulated`` and ``is_simulated:
true``. ``trips_calibrated`` always comes from the catalog (real public trips), never from the fleet file.
"""

from __future__ import annotations

import json
import os
import time
from collections.abc import Callable, Sequence
from pathlib import Path

from verity.paths import DATA_DIR

from .limits import positive_int
from .speech import try_synthesise

FLEET_FILE = DATA_DIR / "fleet" / "demo_fleet.json"
DEFAULT_RANK_RUNS = 400  # FINALE_BUILD WP6 step 4's example; the recommended car is re-run at the full count
MAX_EXTRA_RERUNS = 2  # full-count re-runs allowed after the first when the full count reorders the top of the ranking
SHARE_LANGUAGES = ("en-IN", "hi-IN")
DECISION_WORDS = {"GO": "GO", "NO_GO": "NO-GO", "INSUFFICIENT_EVIDENCE": "NOT ENOUGH EVIDENCE"}

Row = dict
Rerun = Callable[[Row], Row]


def rank_runs_setting() -> int:
    return positive_int(os.environ.get("VERITY_FLEET_RANK_RUNS"), DEFAULT_RANK_RUNS, "VERITY_FLEET_RANK_RUNS")


def load_demo_fleet(catalog: dict, path: Path = FLEET_FILE) -> dict | None:
    """The demo fleet file, checked: every car is a real catalog model, every depot names where its coordinates came
    from, and the file says it is simulated. ``None`` when the file does not exist (then a booking must list cars)."""
    if not path.is_file():
        return None
    fleet = json.loads(path.read_text(encoding="utf-8"))
    if fleet.get("is_simulated") is not True:
        raise ValueError(f"{path}: the demo fleet's battery % and depots are scenario inputs; it must say is_simulated")
    depots = fleet["depots"]
    for key, depot in depots.items():
        if not depot.get("source") or not depot.get("query"):
            raise ValueError(f"{path}: depot {key!r} does not say where its coordinates came from")
    for car in fleet["vehicles"]:
        if car["vehicle_id"] not in catalog:
            raise ValueError(f"{path}: {car['fleet_id']} is {car['vehicle_id']!r}, which is not in vehicles_india.csv")
        if car.get("depot") is not None and car["depot"] not in depots:
            raise ValueError(f"{path}: {car['fleet_id']} names an unknown depot {car['depot']!r}")
    return fleet


def demo_cars(fleet: dict) -> list[dict]:
    """The demo fleet as booking cars: {fleet_id, vehicle_id, label, soc_pct, location, start_name}."""
    out = []
    for car in fleet["vehicles"]:
        depot = fleet["depots"].get(car.get("depot") or "")
        out.append({"fleet_id": car["fleet_id"], "vehicle_id": car["vehicle_id"], "label": car["label"],
                    "soc_pct": float(car["soc_pct"]), "start_name": depot["name"] if depot else None,
                    "location": None if depot is None else {"lat": depot["lat"], "lon": depot["lon"]}})
    return out


def rank_key(row: Row) -> tuple:
    """GO first, then the higher confidence, then fewer minutes door to door (charging included)."""
    return (row["decision"] != "GO", -row["confidence"], row["total_minutes"])


def ranked(rows: Sequence[Row]) -> list[Row]:
    return [{**r, "rank": i + 1} for i, r in enumerate(sorted(rows, key=rank_key))]


def confirm_recommended(rows: Sequence[Row], rerun: Rerun, max_extra: int = MAX_EXTRA_RERUNS) -> tuple[list[Row], int]:
    """(ranked rows with at most one ``recommended``, full-count re-runs made). The top car is re-run at the full count;
    when that reorders the top (its GO at 400 runs is a NO_GO at the full count, say), the new top is re-run too, at most
    ``max_extra`` more times. Only a GO confirmed at the full count is recommended: a 400-run answer never is."""
    rows, confirmed = ranked(rows), set()
    for _ in range(1 + max_extra):
        top = rows[0]
        if top["decision"] != "GO" or top["fleet_id"] in confirmed:
            break
        full = rerun(top)
        confirmed.add(full["fleet_id"])
        rows = ranked([full if r["fleet_id"] == top["fleet_id"] else r for r in rows])
    best = next((r for r in rows if r["fleet_id"] in confirmed and r["decision"] == "GO"), None)
    return [{**r, "recommended": best is not None and r["fleet_id"] == best["fleet_id"]} for r in rows], len(confirmed)


def booking_row(car: dict, release: dict, deadhead_km: float | None) -> Row:
    stops = release["stops"]
    return {"rank": 1, "fleet_id": car["fleet_id"], "label": car["label"], "vehicle": release["vehicle"],
            "start": None if car["location"] is None else {"name": car.get("start_name"), **car["location"]},
            "soc_pct": car["soc_pct"], "deadhead_km": deadhead_km, "decision": release["decision"],
            "confidence": release["confidence"], "confidence_display": release["confidence_display"],
            "arrival_soc": release["arrival_soc"],
            "charging_minutes": int(sum(s["charge_minutes"] for s in stops if not s.get("if_needed"))),
            "total_minutes": int(release["route"]["duration_min"]), "plan_id": release["plan_id"],
            "n_runs": release["n_runs"], "why_not": None if release["decision"] == "GO" else (
                release["refusal_reason"] or release["reason"]), "recommended": False}


def share_summary(release: dict) -> str:
    """The message for the driver, built only from the release's own fields (no number is written here)."""
    word = DECISION_WORDS[release["decision"]]
    parts = [f"{release['vehicle']['name']}: {word}, {release['confidence_display']} confidence "
             f"({release['n_runs']} Monte Carlo runs)."]
    stops = [s for s in release["stops"] if not s.get("if_needed")]
    if stops:
        parts.append("Charge at " + "; ".join(f"{s['name']} (km {s['km_along_route']:.0f}, about {s['charge_minutes']} min)"
                                              for s in stops) + ".")
    elif release["decision"] == "GO":
        parts.append("No charging stop planned.")
    a = release["arrival_soc"]
    parts.append(f"Arrival battery about {a['p50']}% (likely {a['p10']}-{a['p90']}%).")
    if release["bingo_line"]:
        parts.append(f"Bingo Line at the start: divert if the battery is below {release['bingo_line'][0]['bingo_soc_pct']}%.")
    if release["decision"] != "GO":
        parts.append(release["refusal_reason"] or release["reason"])
    if release["is_simulated"]:
        parts.append("(Some inputs are simulated.)")
    return " ".join(parts)


def release_link(plan_id: str) -> str:
    base = os.environ.get("VERITY_PUBLIC_URL", "").strip().rstrip("/")
    return f"{base}/api/release/{plan_id}"


class FleetEndpoints:
    """Mixed into EngineService: uses its ``plan``, ``_plan``, ``_vehicle_for``, ``_resolve_route``, ``demo_fleet``, ``cfg``."""

    def fleet_booking(self, req: dict) -> dict:
        from .models import FleetBookingRequest, PlanRequest  # local: service imports this module
        from .service import ApiError, canonical_request

        req = canonical_request(FleetBookingRequest, req)
        t0 = time.perf_counter()
        if req["vehicles"] is not None:
            cars = [{"fleet_id": f"car-{i + 1}", "vehicle_id": c["vehicle_id"], "label": c["label"] or c["vehicle_id"],
                     "soc_pct": c["soc_pct"], "location": c["location"], "start_name": None}
                    for i, c in enumerate(req["vehicles"])]
            state_source, fleet_source = "entered", "request"
        elif self.demo_fleet is not None:
            cars, state_source, fleet_source = demo_cars(self.demo_fleet), "demo_fleet", "data/fleet/demo_fleet.json"
        else:
            raise ApiError(503, "fleet_unavailable", "No demo fleet on this server; send the cars in 'vehicles'.")
        for car in cars:  # one unknown id fails the whole booking before any Monte Carlo runs
            self._vehicle_for({"vehicle_id": car["vehicle_id"], "passengers": req["passengers"], "ac_on": req["ac_on"]})
        rank_runs = min(rank_runs_setting(), self.cfg.n_runs)  # never more runs to rank than to release

        def row_for(car: dict, n_runs: int | None) -> Row:
            body = {"vehicle_id": car["vehicle_id"], "origin": req["origin"], "destination": req["destination"],
                    "departure_time": req["departure_time"], "start_soc_pct": car["soc_pct"],
                    "passengers": req["passengers"], "ac_on": req["ac_on"],
                    "target_confidence": req["target_confidence"], "preferences": {"objectives": ["safest"]},
                    "start": car["location"], "state_source": state_source}
            release = self.plan(body, n_runs=n_runs)["options"][0]
            resolved = self._resolve_route(canonical_request(PlanRequest, body))  # cached: the plan just resolved it
            return booking_row(car, release, resolved.pickup_km)

        by_id = {c["fleet_id"]: c for c in cars}
        rows = [row_for(car, rank_runs) for car in cars]
        rows, reruns = confirm_recommended(rows, lambda r: row_for(by_id[r["fleet_id"]], None))
        best = next((r for r in rows if r["recommended"]), None)
        return {"booking": {k: v for k, v in req.items() if k != "vehicles"}, "rows": rows,
                "recommended_plan_id": best["plan_id"] if best else None,
                "n_runs_rank": rank_runs, "n_runs_full": self.cfg.n_runs, "full_reruns": reruns,
                "fleet_source": fleet_source, "compute_ms": int(round(1000 * (time.perf_counter() - t0))),
                "is_simulated": state_source == "demo_fleet" or any(
                    self._plan(r["plan_id"]).response["is_simulated"] for r in rows)}

    def release(self, plan_id: str) -> dict:
        return self._plan(plan_id).response

    def share(self, plan_id: str, voice, cache, *, audio: bool = True) -> dict:
        """The release as a message for the driver: text, a link, and the release's own spoken sentences as audio
        (live Sarvam, else the pre-made clip marked ``cached: true``, else none and the reason)."""
        release = self._plan(plan_id).response
        speech = release.get("speech") or {}
        text = {"en-IN": share_summary(release)}
        if speech.get("hi-IN"):
            text["hi-IN"] = speech["hi-IN"]  # the checked Hindi template; no new Hindi wording here
        clips: dict = {lang: None for lang in SHARE_LANGUAGES}
        note = None
        if not audio:
            note = "Audio not requested (audio=0)."
        elif not speech:
            note = "This release has no spoken answer (no destination name), so there is no audio."
        else:
            clips = {lang: try_synthesise(speech[lang], lang, voice, cache) if speech.get(lang) else None
                     for lang in SHARE_LANGUAGES}
            if any(c is None for c in clips.values()):
                note = "Voice is unavailable and there is no pre-made clip for every language; send the text."
        return {"plan_id": plan_id, "text": text, "link": release_link(plan_id), "audio": clips, "audio_note": note,
                "is_simulated": release["is_simulated"]}
