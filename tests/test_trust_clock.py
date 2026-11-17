"""The engine's report clock: simulated reports age on the frozen simulation clock, real ones by real elapsed time."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from verity.schemas import Report
from verity.trust.clock import rebase_real_reports
from verity.trust.reports_sim import NOW

REAL_NOW = datetime(2026, 11, 17, 15, 0, tzinfo=timezone.utc)  # three weeks after the simulation clock


def _report(t: datetime, *, simulated: bool) -> Report:
    return Report(charger_id="CH-1", source_id="s", status="working", t=t, is_simulated=simulated)


def test_a_simulated_report_is_returned_untouched() -> None:
    sim = _report(NOW - timedelta(days=3), simulated=True)
    assert rebase_real_reports([sim], REAL_NOW) == [sim]


def test_a_real_report_is_exactly_as_old_as_it_really_is() -> None:
    real = _report(REAL_NOW - timedelta(days=2, hours=6), simulated=False)
    (out,) = rebase_real_reports([real], REAL_NOW)
    assert NOW - out.t == timedelta(days=2, hours=6)


def test_a_real_report_in_the_engines_future_is_not_negative_or_frozen_fresh() -> None:
    # Dated 17 Nov, after NOW (27 Oct): unchanged it would stay age 0 for ever. Rebased, it ages as real time passes.
    real = _report(REAL_NOW - timedelta(hours=1), simulated=False)
    (today,) = rebase_real_reports([real], REAL_NOW)
    (next_week,) = rebase_real_reports([real], REAL_NOW + timedelta(days=7))
    assert NOW - today.t == timedelta(hours=1)
    assert NOW - next_week.t == timedelta(days=7, hours=1)


def test_a_report_stamped_after_the_real_clock_counts_as_age_zero() -> None:
    skewed = _report(REAL_NOW + timedelta(minutes=5), simulated=False)
    (out,) = rebase_real_reports([skewed], REAL_NOW)
    assert out.t == NOW


def test_the_stored_report_is_not_changed() -> None:
    real = _report(REAL_NOW - timedelta(days=1), simulated=False)
    before = real.t
    rebase_real_reports([real], REAL_NOW)
    assert real.t == before


@pytest.mark.parametrize("simulated", [True, False])
def test_other_fields_survive(simulated: bool) -> None:
    rep = Report(charger_id="CH-9", source_id="driver:driver_voice", status="broken", t=REAL_NOW,
                 evidence="driver report", is_simulated=simulated)
    (out,) = rebase_real_reports([rep], REAL_NOW)
    assert (out.charger_id, out.source_id, out.status, out.evidence, out.is_simulated) == (
        "CH-9", "driver:driver_voice", "broken", "driver report", simulated)
