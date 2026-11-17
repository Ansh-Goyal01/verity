"""The engine's clock for report age (the S7 ``NOW`` fix).

Simulated reports are dated relative to ``reports_sim.NOW`` (27 Oct 2026): that is a frozen snapshot, and every pinned
demo decision was made against it. Real reports (a driver's spoken note, an Open Charge Map check-in) carry real
timestamps. Fed to the trust engine unchanged they are wrong both ways: a report dated after ``NOW`` has age 0 for ever
(the engine clamps negative ages), and one dated before it is aged by the wrong amount.

``rebase_real_reports`` keeps the engine's single clock and ages each kind of report by its own elapsed time:

* simulated reports are returned untouched (their age is still ``NOW - t``),
* a real report is moved to ``NOW - (real_now - t)``: it is exactly as old as it really is, never negative.

It is a view for the trust engine; the stored report (graph, API response) keeps its true time. Pure, no I/O.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timedelta

from ..schemas import Report
from .reports_sim import NOW


def rebase_real_reports(reports: Sequence[Report], real_now: datetime, sim_now: datetime = NOW) -> list[Report]:
    """The same reports with every real (non-simulated) timestamp expressed on the engine's simulation clock."""
    out: list[Report] = []
    for r in reports:
        if r.is_simulated:
            out.append(r)
            continue
        age = max(real_now - r.t, timedelta(0))  # a report from "the future" (clock skew) is age 0, not negative
        out.append(r.model_copy(update={"t": sim_now - age}))
    return out
