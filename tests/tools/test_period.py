from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from octopus_mcp.tools.period import PeriodSpec, resolve_period


def _now() -> datetime:
    return datetime(2026, 4, 26, 12, 0, tzinfo=UTC)


def test_last_month() -> None:
    pf, pt = resolve_period(PeriodSpec(kind="last_month"), now=_now())
    # Boundaries are UK-local midnights: 1 Apr is in BST, i.e. 31 Mar 23:00 UTC.
    assert pf == datetime(2026, 3, 1, 0, tzinfo=UTC)
    assert pt == datetime(2026, 3, 31, 23, tzinfo=UTC)


def test_last_7_days() -> None:
    pf, pt = resolve_period(PeriodSpec(kind="last_7_days"), now=_now())
    assert (pt - pf).days == 7


def test_explicit_range() -> None:
    pf, pt = resolve_period(
        PeriodSpec(kind="explicit", from_date=date(2026, 4, 1), to_date=date(2026, 4, 15)),
        now=_now(),
    )
    london = ZoneInfo("Europe/London")
    assert pf.astimezone(london).date() == date(2026, 4, 1)
    assert pt.astimezone(london).date() == date(2026, 4, 15)
    assert pf.astimezone(london).hour == 0 and pt.astimezone(london).hour == 0


def test_period_too_long_rejected() -> None:
    with pytest.raises(ValueError):
        PeriodSpec(kind="explicit", from_date=date(2020, 1, 1), to_date=date(2025, 1, 1))


def test_period_in_future_rejected() -> None:
    with pytest.raises(ValueError):
        resolve_period(
            PeriodSpec(kind="explicit", from_date=date(2030, 1, 1), to_date=date(2030, 1, 2)),
            now=_now(),
        )


def test_a_bst_day_starts_at_uk_midnight_not_utc_midnight() -> None:
    """Found against a live Intelligent Go account: overnight cheap-rate charging at
    23:30-00:30 UK time landed on the wrong day when periods used UTC midnights."""
    pf, pt = resolve_period(
        PeriodSpec(kind="explicit", from_date=date(2026, 10, 2), to_date=date(2026, 10, 3)),
        now=datetime(2026, 10, 3, 22, tzinfo=UTC),
    )
    assert pf == datetime(2026, 10, 1, 23, tzinfo=UTC)
    assert pt == datetime(2026, 10, 2, 23, tzinfo=UTC)


def test_standing_charge_counts_uk_days_across_the_clock_change() -> None:
    from octopus_mcp.analysis.billing import compute_period_cost
    from octopus_mcp.analysis.rate_lookup import RateStream, RateWindow

    # 2026-10-25: clocks go back, so this week is 7 days + 1 hour long.
    pf, pt = resolve_period(
        PeriodSpec(kind="explicit", from_date=date(2026, 10, 22), to_date=date(2026, 10, 29)),
        now=datetime(2026, 11, 1, tzinfo=UTC),
    )
    assert pt - pf == timedelta(days=7, hours=1)
    sc = RateStream([RateWindow(datetime(2026, 1, 1, tzinfo=UTC), None, 50.0)])
    cost = compute_period_cost(
        consumption=[], unit_rates=sc, standing_charges=sc, period_from=pf, period_to=pt
    )
    assert cost.standing_pence == 350  # 7 days, not 8
