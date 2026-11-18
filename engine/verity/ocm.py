"""Open Charge Map check-ins as charger reports (W1.2). Pure: parsing only, the fetch lives in scripts/fetch_ocm_checkins.py.

A POI from ``GET /v3/poi?includecomments=true`` carries ``UserComments``; a check-in is a comment with a
``CheckinStatusType`` whose ``IsPositive`` says whether charging worked. UNVERIFIED: these field names are from
memory of the public API, not read from its documentation (the official docs page was not readable when this was
written, and reference data returned HTTP 403); ``tests/test_ocm.py`` uses a SYNTHETIC POI of this shape, and the first
real fetch must be checked against the saved raw response (docs/EVIDENCE.md).

Golden rule 1: a check-in with no clear positive/negative status, or no date, is skipped and counted, never guessed.
A negative check-in becomes ``broken`` (OCM does not say why: no_power / blocked are not inferred).
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Mapping
from datetime import datetime, timezone

from .schemas import Report

OCM_CHECKIN_SOURCE = "public:ocm_checkin"  # one source per channel, like the other public evidence (trust/channels.py)


def _parse_time(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        t = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return t if t.tzinfo else t.replace(tzinfo=timezone.utc)


def checkins_from_poi(poi: Mapping, charger_id: str) -> tuple[list[Report], Counter]:
    """(reports, skipped by reason) for every check-in comment on one OCM point, mapped to ``charger_id``."""
    reports: list[Report] = []
    skipped: Counter = Counter()
    for comment in poi.get("UserComments") or []:
        status_type = comment.get("CheckinStatusType")
        if not isinstance(status_type, Mapping):
            skipped["not_a_checkin"] += 1  # an ordinary comment or rating
            continue
        positive = status_type.get("IsPositive")
        t = _parse_time(comment.get("DateCreated"))
        if not isinstance(positive, bool):
            skipped["no_clear_status"] += 1
        elif t is None:
            skipped["no_date"] += 1
        else:
            reports.append(Report(charger_id=charger_id, source_id=OCM_CHECKIN_SOURCE,
                                  status="working" if positive else "broken", t=t,
                                  evidence=f"Open Charge Map check-in: {status_type.get('Title') or 'status'}"[:200],
                                  is_simulated=False))
    return reports, skipped


def checkins_from_pois(pois: Iterable[Mapping], charger_ids_by_ocm: Mapping[str, str]) -> tuple[list[Report], Counter]:
    """Check-ins for every point whose OCM id (``ocm:<ID>``) is in the directory; the rest are counted, not dropped silently."""
    reports: list[Report] = []
    skipped: Counter = Counter()
    for poi in pois:
        cid = charger_ids_by_ocm.get(f"ocm:{poi.get('ID')}")
        if cid is None:
            skipped["poi_not_in_directory"] += 1
            continue
        got, why = checkins_from_poi(poi, cid)
        reports.extend(got)
        skipped.update(why)
    return reports, skipped
