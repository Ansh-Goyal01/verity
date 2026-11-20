"""Where each input of a release came from, and ``is_simulated`` derived from it (WP1, FINALE_BUILD rule 3).

``is_simulated`` is true exactly when ANY input is simulated: a plan on a real route, with a real car and only real
reports, says false, and the first such release is a milestone, so it must never be claimed falsely either way.
Pure functions, no I/O.
"""

from __future__ import annotations

from datetime import datetime

from verity.routing import ist_hour

REAL, SIMULATED = "real", "simulated"
ROUTER_LABEL = {"osrm": "OSRM public router (router.project-osrm.org), OpenStreetMap roads"}
DEMO_FLEET_LABEL = "Demo fleet: simulated trips, calibrated through the real pipeline"


def _entry(simulated: bool, source: str, *, n: int | None = None, note: str | None = None) -> dict:
    return {"status": SIMULATED if simulated else REAL, "source": source, "n": n, "note": note}


def weather_hour_matched(snap: dict, departure: datetime) -> bool:
    """True when every forecast point is for the departure hour (the fetch otherwise silently takes another hour)."""
    wanted = ist_hour(departure)  # the forecast is labelled in IST; an ...Z departure is converted first
    points = snap.get("weather_points") or []
    return bool(points) and all(p.get("hour") == wanted for p in points)


def build_data_sources(snap: dict, departure: datetime, *, vehicle_simulated: bool, vehicle_source: str,
                       n_real_reports: int, n_simulated_reports: int) -> dict:
    """One entry per input the plan used. ``n_*_reports`` count reports on the chargers near this route."""
    geo_sim = bool(snap.get("is_simulated"))
    matched = weather_hour_matched(snap, departure)
    weather_note = None if matched else "The forecast did not hold the departure hour; the nearest available hour was used."
    n_reports = n_real_reports + n_simulated_reports
    return {
        "route": _entry(geo_sim, ROUTER_LABEL.get(snap.get("source", ""), str(snap.get("source", "unknown")))),
        "elevation": _entry(geo_sim, str(snap.get("elevation_source", "unknown"))),
        "weather": _entry(geo_sim, "Open-Meteo forecast", note=weather_note),
        "vehicle": _entry(vehicle_simulated, vehicle_source),
        "charger_reports": _entry(n_simulated_reports > 0, "Driver and source reports in the trust graph",
                                  n=n_reports,
                                  note=f"{n_real_reports} real, {n_simulated_reports} simulated" if n_reports else
                                  "No reports on this route's chargers yet: trust is the prior only"),
    }


def any_simulated(sources: dict) -> bool:
    return any(entry["status"] == SIMULATED for entry in sources.values())
