"""Paths and public metadata for the packaged demo data."""

import json
import os
from pathlib import Path

from verity.paths import PROCESSED_DIR

ROUTES_DIR = PROCESSED_DIR / "routes"
GEOCODES_FILE = PROCESSED_DIR / "demo_geocodes.json"
CORRIDORS = (
    ("delhi-jaipur", "Connaught Place", "Jaipur"),
    ("delhi-chandigarh", "Connaught Place", "Chandigarh"),
    ("delhi-dehradun", "Connaught Place", "Dehradun"),
    ("pune-mumbai", "Pune", "Mumbai"),
    ("bengaluru-mysuru", "Bengaluru", "Mysuru"),
)
WARM_QUERIES = ("Noida", "Agra", "Delhi", "jai", "chan", "jaipur", "dehradun", "mys")


def offline_enabled() -> bool:
    return os.environ.get("VERITY_OFFLINE", "").strip() == "1"


def trip_info(slug: str, snap: dict) -> dict:
    return {key: snap[key] for key in ("origin", "destination", "departure_time")} | {"slug": slug}


def load_geocodes(path: Path = GEOCODES_FILE) -> dict[str, list[dict]]:
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))
