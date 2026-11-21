"""Screen a driver-recorded trip before it may feed battery calibration (pure; no I/O).

A trip is usable for calibration only if the battery was drained by driving alone: the energy model explains
the SoC drop between the first and last reading. A session with intermediate charging breaks that, so it is
kept as a record but flagged ineligible. Physics stays the baseline; nothing here changes an estimate.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

# Manual taps and voice readings are whole percents; a rise this large cannot be rounding or sensor jitter.
CHARGE_RISE_PCT = 2.0
# A smaller net drop is inside the reading noise, so it says nothing about the vehicle.
MIN_DROP_PCT = 3.0
MIN_READINGS = 2


@dataclass(frozen=True)
class Screening:
    intermediate_charging: bool
    eligible: bool
    reason: str
    delta_soc_pct: float
    n_readings: int


def screen_session(soc: Sequence[tuple[float, float]], declared_charging: bool) -> Screening:
    """``soc`` is (seconds since start, SoC %) in any order; ``declared_charging`` is the driver's own statement."""
    readings = sorted(soc)
    n = len(readings)
    delta = readings[0][1] - readings[-1][1] if n >= MIN_READINGS else 0.0
    rose = any(later[1] - earlier[1] >= CHARGE_RISE_PCT for earlier, later in zip(readings, readings[1:]))
    charging = declared_charging or rose
    if n < MIN_READINGS:
        return Screening(charging, False, "needs at least two SoC readings", delta, n)
    if declared_charging:
        return Screening(True, False, "driver reported charging during the trip", delta, n)
    if rose:
        return Screening(True, False, f"SoC rose by {CHARGE_RISE_PCT:g} points or more between readings", delta, n)
    if delta < MIN_DROP_PCT:
        return Screening(False, False, f"net SoC drop under {MIN_DROP_PCT:g} points is inside reading noise", delta, n)
    return Screening(False, True, "driving only: usable for calibration", delta, n)
