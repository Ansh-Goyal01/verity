"""Open Charge Map check-ins -> reports (W1.2). The POIs here are SYNTHETIC, in the shape verity/ocm.py expects (unverified
against the real API until the first fetch is checked); no network."""

from __future__ import annotations

from datetime import datetime, timezone

from verity.ocm import OCM_CHECKIN_SOURCE, checkins_from_poi, checkins_from_pois

SYNTHETIC_POI = {"ID": 101, "UserComments": [
    {"DateCreated": "2026-11-01T08:30:00Z", "CheckinStatusType": {"Title": "synthetic positive", "IsPositive": True}},
    {"DateCreated": "2026-11-02T09:00:00Z", "CheckinStatusType": {"Title": "synthetic negative", "IsPositive": False}},
    {"DateCreated": "2026-11-03T09:00:00Z", "Comment": "just a comment, no check-in"},
    {"DateCreated": "2026-11-04T09:00:00Z", "CheckinStatusType": {"Title": "synthetic unclear", "IsPositive": None}},
    {"DateCreated": "not a date", "CheckinStatusType": {"Title": "synthetic undated", "IsPositive": True}},
]}


def test_positive_is_working_negative_is_broken_and_nothing_else_is_guessed() -> None:
    reports, skipped = checkins_from_poi(SYNTHETIC_POI, "CH-1")
    assert [r.status for r in reports] == ["working", "broken"]
    assert dict(skipped) == {"not_a_checkin": 1, "no_clear_status": 1, "no_date": 1}


def test_reports_are_real_dated_and_from_the_ocm_check_in_source() -> None:
    (first, _), _ = checkins_from_poi(SYNTHETIC_POI, "CH-1")
    assert first.source_id == OCM_CHECKIN_SOURCE == "public:ocm_checkin"
    assert first.is_simulated is False and first.t == datetime(2026, 11, 1, 8, 30, tzinfo=timezone.utc)
    assert first.charger_id == "CH-1" and "Open Charge Map check-in" in first.evidence


def test_points_outside_the_directory_are_counted_not_silently_dropped() -> None:
    reports, skipped = checkins_from_pois([SYNTHETIC_POI, {"ID": 999, "UserComments": []}], {"ocm:101": "CH-1"})
    assert len(reports) == 2 and skipped["poi_not_in_directory"] == 1


def test_a_point_without_comments_gives_no_reports() -> None:
    assert checkins_from_poi({"ID": 5}, "CH-1")[0] == []
    assert checkins_from_poi({"ID": 5, "UserComments": None}, "CH-1")[0] == []
