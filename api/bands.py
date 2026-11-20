"""WP5: which band method a release uses, read once from data/processed/conformal.json (built by
experiments/e7_honest_bands.py from the TRAINING split only).

* Real cars: ``split conformal`` with the file's n and k; the Monte Carlo is widened by ``k_applied`` (never < 1).
* Simulated demo vehicles: ``model only``: their posteriors come from the simulator, so a k measured on real Indian
  trips does not describe them (and the demo answers stay exactly as they were).
* No file (or no finite k): ``model only`` for everyone, said so.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path

from verity.conformal import GUARANTEE_NOTE, applied_scale
from verity.paths import PROCESSED_DIR

log = logging.getLogger(__name__)
CONFORMAL_FILE = PROCESSED_DIR / "conformal.json"
SIMULATED_NOTE = "Simulated demo vehicle: its bands come from the simulator and are not calibrated on real trips."
MISSING_NOTE = "No real-trip calibration is available yet: the bands are the physics model's own spread."


@dataclass(frozen=True)
class BandCalibration:
    n: int | None
    k: float | None
    k_applied: float
    sources: tuple[str, ...]
    created_at: str | None

    @property
    def available(self) -> bool:
        return self.k is not None and bool(self.n)

    def block(self, *, vehicle_simulated: bool) -> dict:
        """The release's ``band_method``: what the bands are built from, with the real n behind them."""
        if vehicle_simulated or not self.available:
            return {"name": "model only", "n": None, "k": None, "k_applied": 1.0, "sources": [],
                    "note": SIMULATED_NOTE if vehicle_simulated and self.available else MISSING_NOTE}
        widened = (f"widened by {self.k_applied:.2f}" if self.k_applied > 1.0 else
                   "not widened: the model's own 90 % band already covered the calibration trips (k "
                   f"{self.k:.2f}; bands are never narrowed)")
        return {"name": "split conformal", "n": self.n, "k": round(self.k, 3), "k_applied": round(self.k_applied, 3),
                "sources": list(self.sources),
                "note": f"Calibrated on {self.n} real Indian trips, {widened}. {GUARANTEE_NOTE}"}

    def scale_for(self, *, vehicle_simulated: bool) -> float:
        return 1.0 if vehicle_simulated or not self.available else self.k_applied


def load_band_calibration(path: Path = CONFORMAL_FILE) -> BandCalibration:
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
        k = doc.get("k")
        return BandCalibration(int(doc["n"]), None if k is None else float(k), applied_scale(k),
                               tuple(doc.get("sources", ())), doc.get("created_at"))
    except FileNotFoundError:
        return BandCalibration(None, None, 1.0, (), None)
    except (OSError, ValueError, KeyError, TypeError) as exc:  # a broken file must never make bands narrower or crash
        log.warning("conformal.json unreadable (%s): bands are model only", type(exc).__name__)
        return BandCalibration(None, None, 1.0, (), None)
