"""Single-charger failures that open an impassable gap on an otherwise passable route."""

from collections.abc import Sequence

from .chargers import RouteCharger


def critical(near: Sequence[RouteCharger], length: float, reach: float) -> tuple[float, list[dict]]:
    ordered = sorted(near, key=lambda stop: stop.km_along)
    points = [0.0, *(stop.km_along for stop in ordered), length]
    full = max(b - a for a, b in zip(points, points[1:]))
    if full > reach:
        return full, []
    rows = []
    for i, stop in enumerate(ordered, start=1):
        # Only the two gaps adjacent to the removed charger can change.
        gap = points[i + 1] - points[i - 1]
        if gap > reach:
            rows.append({"charger_id": stop.charger.id, "name": stop.charger.name,
                         "km": round(stop.km_along, 1), "gap_if_lost_km": round(gap, 1)})
    return full, rows
