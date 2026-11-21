"""Likely shared grid cause (WP8c): each charger's NEAREST OpenStreetMap substation within ``SUBSTATION_RADIUS_KM``.

This is proximity only. India's distribution feeder maps are not public, so a charger's nearest substation is NOT known
to be the one that feeds it, and nothing here or downstream may call it that. Two chargers with the same nearest
substation get the weakest common-cause tier in the Monte Carlo (``sites.BETA_SUBSTATION``): a local grid fault is one
plausible reason both could be down at once. Pure functions; no network (``scripts/import_substations.py`` fetches).
"""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path

import numpy as np

from .paths import DATA_DIR
from .trips import haversine_m

SUBSTATIONS_FILE = DATA_DIR / "processed" / "substations.json"
SUBSTATION_RADIUS_KM = 5.0  # Ansh's choice, 2026-11-21 (docs/DECISIONS.md WP8c)
LINK = "NEAREST_SUBSTATION"  # the relationship name says what it is: nearest, never "feeds"


def load_substations(path: Path = SUBSTATIONS_FILE) -> list[dict]:
    """[{id, lat, lon, name, voltage, operator}]; [] when the import has not been run."""
    if not path.is_file():
        return []
    return json.loads(path.read_text(encoding="utf-8"))["substations"]


def nearest_substations(chargers: Iterable, substations: Sequence[dict],
                        max_km: float = SUBSTATION_RADIUS_KM) -> dict[str, tuple[str, float]]:
    """Charger id -> (nearest substation id, distance km), for chargers with one within ``max_km``."""
    if not substations:
        return {}
    lat = np.array([s["lat"] for s in substations], dtype=float)
    lon = np.array([s["lon"] for s in substations], dtype=float)
    pad = max_km / 111.0 + 0.01  # a cheap box first: one degree of latitude is ~111 km
    out = {}
    for c in chargers:
        lon_pad = pad / max(float(np.cos(np.radians(c.lat))), 0.2)
        box = np.nonzero((np.abs(lat - c.lat) <= pad) & (np.abs(lon - c.lon) <= lon_pad))[0]
        if not len(box):
            continue
        d = haversine_m(np.full(len(box), c.lat), np.full(len(box), c.lon), lat[box], lon[box]) / 1000.0
        i = int(np.argmin(d))
        if d[i] <= max_km:
            out[c.id] = (substations[box[i]]["id"], round(float(d[i]), 3))
    return out


def shared_only(substation_of: Mapping[str, str], ids: Iterable[str]) -> dict[str, str]:
    """The links that matter for one set of chargers: a substation only adds a common cause when at least two of them
    share it (alone, a charger's substation changes nothing, as for operators)."""
    ids = [i for i in ids if i in substation_of]
    counts = Counter(substation_of[i] for i in ids)
    return {i: substation_of[i] for i in ids if counts[substation_of[i]] >= 2}
