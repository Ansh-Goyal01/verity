"""Hand-collected corpus: templates match the specs, seeds are clean drafts, the validator is specific."""

from __future__ import annotations

import subprocess

import pytest

import scripts.validate_corpus as cli
from verity import paths
from verity.corpus import (
    APP_REVIEWS,
    APP_SNAPSHOTS,
    CHARGER_EVIDENCE,
    ERROR,
    SPECS,
    TRIPS,
    WARN,
    read_csv,
    usable_rows,
    validate_row,
    validate_table,
)


def _trip(**overrides: str) -> dict[str, str]:
    row = {c: "" for c in TRIPS.header}
    row.update(record_id="T9001", record_type="soc_trip", source_url="https://example.org/trip",
               source_kind="magazine_test", source_published_date="2024-05-01", make="Tata", model="Nexon EV",
               origin="Noida", destination="Agra", one_way_or_round="one_way", distance_km="230",
               start_soc_pct="90", end_soc_pct="20", speed_band="highway_80_100", ac="on",
               trip_date_or_month="2024-04", entered_by="tester", verified="yes", verified_by="checker")
    row.update(overrides)
    return row


def _messages(findings: list[tuple[str, str]], level: str = ERROR) -> str:
    return " | ".join(message for lvl, message in findings if lvl == level)


@pytest.mark.parametrize("spec", SPECS, ids=lambda s: s.filename)
def test_committed_template_header_matches_spec(spec):
    header, _ = read_csv(paths.CORPUS_DIR / spec.filename)
    assert header == spec.header


def test_seed_trips_are_unverified_drafts_without_errors():
    header, rows = read_csv(paths.CORPUS_DIR / TRIPS.filename)
    issues = validate_table(TRIPS, header, rows)
    assert len(rows) >= 6
    seeds = [row for row in rows if row["entered_by"] == "seed:claude-code"]
    assert all(row["verified"] == "no" for row in seeds)
    assert [i.render() for i in issues if i.level == ERROR] == []


def test_complete_verified_trip_passes():
    assert _messages(validate_row(TRIPS, _trip())) == ""
    assert len(usable_rows(TRIPS, [_trip()])) == 1


def test_unverified_rows_never_reach_results():
    assert usable_rows(TRIPS, [_trip(verified="no")]) == []


def test_start_soc_must_exceed_end_soc():
    findings = validate_row(TRIPS, _trip(start_soc_pct="20", end_soc_pct="40"))
    assert "must be larger than end_soc_pct" in _messages(findings)


def test_enum_error_lists_allowed_values():
    text = _messages(validate_row(TRIPS, _trip(ac="yes")))
    assert "ac is 'yes'" in text and "on | off | unknown" in text


def test_numeric_range_and_range_text_rejected():
    assert "outside the plausible range" in _messages(validate_row(TRIPS, _trip(start_soc_pct="140")))
    assert "plain number" in _messages(validate_row(TRIPS, _trip(avg_speed_kmh="65-70")))


def test_dates_must_be_iso_and_not_in_future():
    assert "YYYY-MM-DD" in _messages(validate_row(TRIPS, _trip(source_published_date="01/05/2024")))
    assert "in the future" in _messages(validate_row(TRIPS, _trip(source_published_date="2999-01-01")))


def test_url_is_required_and_must_be_a_link():
    assert "required on every row" in _messages(validate_row(TRIPS, _trip(source_url="")))
    assert "full link" in _messages(validate_row(TRIPS, _trip(source_url="team-bhp thread")))


def test_missing_fields_are_errors_when_verified_but_warnings_in_drafts():
    verified = validate_row(TRIPS, _trip(distance_km=""))
    draft = validate_row(TRIPS, _trip(distance_km="", verified="no", verified_by=""))
    assert "marked verified=yes but missing: distance_km" in _messages(verified)
    assert _messages(draft) == ""
    assert "draft: fill verified_by, distance_km" in _messages(draft, WARN)
    assert usable_rows(TRIPS, [_trip(distance_km="")]) == []


def test_consumption_row_needs_only_energy():
    row = _trip(record_type="consumption", distance_km="", start_soc_pct="", end_soc_pct="", energy_wh_per_km="150")
    assert _messages(validate_row(TRIPS, row)) == ""


def test_duplicate_ids_use_spreadsheet_row_numbers():
    issues = validate_table(TRIPS, TRIPS.header, [_trip(), _trip()])
    duplicate = [i for i in issues if "already used" in i.message]
    assert len(duplicate) == 1 and duplicate[0].sheet_row == 3 and "row 2" in duplicate[0].message


def test_wrong_header_reports_missing_and_extra_columns():
    header = [c for c in TRIPS.header if c != "verified_by"] + ["Verified By"]
    (issue,) = validate_table(TRIPS, header, [])
    assert "missing columns: verified_by" in issue.message and "unexpected columns: Verified By" in issue.message


def _evidence(**overrides: str) -> dict[str, str]:
    row = {c: "" for c in CHARGER_EVIDENCE.header}
    row.update(evidence_id="E9001", charger_name="Test Hub", operator="TestOp", place_url="https://maps.example/x",
               source_kind="plugshare", evidence_date="2026-11-01", evidence_time="18:45", status="broken",
               source_url="https://example.org/checkin", paraphrase="Fast charger showed error, session would not start",
               collected_by="tester", verified="yes")
    row.update(overrides)
    return row


def test_charger_evidence_paraphrase_rules():
    long_text = " ".join(["word"] * 16)
    assert "at most 15" in _messages(validate_row(CHARGER_EVIDENCE, _evidence(paraphrase=long_text)))
    assert "remove handles" in _messages(validate_row(CHARGER_EVIDENCE, _evidence(paraphrase="told @someone it failed")))
    assert _messages(validate_row(CHARGER_EVIDENCE, _evidence())) == ""


def test_charger_evidence_needs_a_location_and_valid_time():
    assert "place_url or lat+lon" in _messages(validate_row(CHARGER_EVIDENCE, _evidence(place_url="")))
    assert "both lat and lon" in _messages(validate_row(CHARGER_EVIDENCE, _evidence(lat="28.6")))
    assert "HH:MM" in _messages(validate_row(CHARGER_EVIDENCE, _evidence(evidence_time="6:45 PM")))


def test_snapshot_datetime_and_review_stars():
    snap = {c: "" for c in APP_SNAPSHOTS.header}
    snap.update(snapshot_id="S1", app="statiq", charger_name="Hub", datetime_ist="2026-11-01 6pm",
                status_shown="available", collected_by="tester")
    assert "YYYY-MM-DD HH:MM" in _messages(validate_row(APP_SNAPSHOTS, snap))
    review = {c: "" for c in APP_REVIEWS.header}
    review.update(review_id="R1", app="statiq", review_date="2026-10-01", stars="6", language="hi", collected_by="t")
    assert "outside the plausible range 1 to 5" in _messages(validate_row(APP_REVIEWS, review))


def test_reader_handles_excel_bom_and_blank_rows(tmp_path):
    path = tmp_path / "x.csv"
    content = "﻿" + ",".join(TRIPS.header) + "\n" + "," * (len(TRIPS.header) - 1) + "\n"
    path.write_bytes(content.encode("utf-8"))
    header, rows = read_csv(path)
    assert header == TRIPS.header and rows == []


def test_cli_exit_codes(tmp_path, capsys):
    assert cli.main(["--corpus", str(paths.CORPUS_DIR), "--private", str(tmp_path)]) == 0
    assert cli.main(["--corpus", str(tmp_path), "--private", str(tmp_path)]) == 1
    assert "file not found" in capsys.readouterr().out


def test_private_folder_is_gitignored():
    result = subprocess.run(["git", "check-ignore", "-q", "data/private/app_reviews_text.csv"],
                            cwd=paths.REPO_ROOT, check=False)
    assert result.returncode == 0
