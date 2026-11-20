"""Place search for India through Photon (komoot), behind ``CachedClient`` (WP1, docs/FINALE_BUILD.md).

Photon's public API terms (read 2026-11-20, https://photon.komoot.io/): "You can use the API for your project, but
please be fair - extensive usage will be throttled." No numeric limit is published. So: every response is cached on
disk, and the geocoder has its OWN cache directory and lifetime request budget (``VERITY_GEOCODE_MAX_REQUESTS``),
so autocomplete typing can never use up the routing budget. Results come from OpenStreetMap data.

India only: Photon is asked for ``countrycode=IN`` and any feature that is not ``IN`` is dropped as well, so no
remembered bounding box is needed. Coordinates are the geocoder's, never typed in by hand.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .paths import CACHE_DIR
from .routing import CachedClient, max_requests_setting

PHOTON_URL = "https://photon.komoot.io/api/"
GEOCODE_CACHE_DIR = CACHE_DIR / "geocode"
GEOCODE_BUDGET_ENV = "VERITY_GEOCODE_MAX_REQUESTS"
DEFAULT_GEOCODE_BUDGET = 500  # lifetime calls; a typed prefix is cached, so repeats cost nothing
COUNTRY_CODE = "IN"
MIN_QUERY_CHARS, MAX_QUERY_CHARS = 2, 100
MAX_LIMIT = 10


@dataclass(frozen=True)
class Place:
    name: str
    lat: float
    lon: float
    kind: str

    def as_dict(self) -> dict:
        return {"name": self.name, "lat": self.lat, "lon": self.lon, "kind": self.kind}


def geocode_client(cache_dir: Path = GEOCODE_CACHE_DIR, **kwargs: object) -> CachedClient:
    """The geocoder's own client: separate cache and separate persisted request budget."""
    budget = max_requests_setting(GEOCODE_BUDGET_ENV, DEFAULT_GEOCODE_BUDGET)
    return CachedClient(cache_dir=cache_dir, max_requests=budget, **kwargs)  # type: ignore[arg-type]


def normalise_query(q: str) -> str:
    """Trimmed, single-spaced, lower-case (so 'Jaipur' and 'jaipur ' share one cached response)."""
    text = " ".join(q.split()).lower()
    if not MIN_QUERY_CHARS <= len(text) <= MAX_QUERY_CHARS:
        raise ValueError(f"query must be {MIN_QUERY_CHARS}-{MAX_QUERY_CHARS} characters")
    return text


def _label(props: dict) -> str:
    name = props.get("name") or props.get("street") or ""
    parts = [name, props.get("city") if props.get("city") != name else None, props.get("state")]
    return ", ".join(p for p in parts if p)


def places_from_photon(payload: dict, limit: int) -> list[Place]:
    """Parse a Photon FeatureCollection; keep only Indian, named, point features (pure, no network)."""
    out: list[Place] = []
    for feat in payload.get("features") or []:
        props = feat.get("properties") or {}
        geom = feat.get("geometry") or {}
        coords = geom.get("coordinates") or []
        if str(props.get("countrycode", "")).upper() != COUNTRY_CODE or geom.get("type") != "Point" or len(coords) < 2:
            continue
        label = _label(props)
        if not label:
            continue
        out.append(Place(label, round(float(coords[1]), 6), round(float(coords[0]), 6), str(props.get("osm_value") or "")))
        if len(out) >= limit:
            break
    return out


def geocode(client: CachedClient, q: str, limit: int = 5, near: tuple[float, float] | None = None) -> list[Place]:
    """Up to ``limit`` Indian places for ``q``, optionally biased towards ``near`` (lat, lon)."""
    limit = max(1, min(int(limit), MAX_LIMIT))
    params: dict = {"q": normalise_query(q), "limit": limit, "countrycode": COUNTRY_CODE, "lang": "en"}
    if near is not None:
        params["lat"], params["lon"] = round(near[0], 3), round(near[1], 3)  # rounded: better cache reuse
    payload, _ = client.get_json(PHOTON_URL, params)
    return places_from_photon(payload, limit)
