"""BMW i3 real-driving dataset (IEEE DataPort, TU Munich): loader entry point.

STATUS: BLOCKED. The data has not been downloaded, so this module only reports availability. The file
format, column names and units must be read off the real files (Prompt 4 of docs/PROMPTS.md: inspect
first, then write the loader). Guessing a column layout here would be inventing facts.

To unblock: download "Battery and Heating Data in Real Driving Cycles" into ``data/raw/bmw_i3/``,
then ask Claude Code to run Prompt 4. Remember when reporting results: this is German driving data
with heater load, not Indian driving or AC load.
"""

from __future__ import annotations

from pathlib import Path

from ..paths import DATA_DIR

BMW_DIR: Path = DATA_DIR / "raw" / "bmw_i3"


def available() -> bool:
    """True when the dataset folder exists and contains at least one file."""
    return BMW_DIR.is_dir() and any(p.is_file() for p in BMW_DIR.rglob("*"))


def status() -> dict:
    if not available():
        return {"status": "blocked", "reason": f"{BMW_DIR} is missing or empty; download the dataset (free login)"}
    return {"status": "present_but_loader_not_written",
            "reason": "run Prompt 4 to inspect the real files and write the loader"}


def load_all() -> list:
    raise NotImplementedError(
        "BMW i3 loader not written: inspect the real files in data/raw/bmw_i3/ first (Prompt 4)."
    )
