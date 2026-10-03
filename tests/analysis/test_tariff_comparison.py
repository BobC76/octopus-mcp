from datetime import UTC, datetime

from octopus_mcp.analysis.billing import ConsumptionPoint
from octopus_mcp.analysis.rate_lookup import RateStream, RateWindow
from octopus_mcp.analysis.tariff_comparison import (
    FuelComparison,
    FuelInputs,
    TariffComparison,
    compare_tariffs,
)


def _w(start: str, end: str | None, v: float) -> RateWindow:
    s = datetime.fromisoformat(start.replace("Z", "+00:00"))
    e = datetime.fromisoformat(end.replace("Z", "+00:00")) if end else None
    return RateWindow(valid_from=s, valid_to=e, value_inc_vat=v)


def _c(iso: str, kwh: float) -> ConsumptionPoint:
    return ConsumptionPoint(
        interval_start=datetime.fromisoformat(iso.replace("Z", "+00:00")), consumption_kwh=kwh
    )


def test_target_cheaper_yields_negative_delta() -> None:
    consumption = [_c("2026-04-25T00:00:00Z", 1.0), _c("2026-04-25T00:30:00Z", 1.0)]
    current_unit = RateStream([_w("2026-04-01T00:00:00Z", None, 30.0)])
    target_unit = RateStream([_w("2026-04-01T00:00:00Z", None, 20.0)])
    sc = RateStream([_w("2026-04-01T00:00:00Z", None, 50.0)])

    inputs = [
        FuelInputs(
            fuel="electricity",
            consumption=consumption,
            current_unit_rates=current_unit,
            current_standing=sc,
            target_unit_rates=target_unit,
            target_standing=sc,
        )
    ]
    result = compare_tariffs(
        target_product_code="GO-VAR-22-10-14",
        period_from=datetime(2026, 4, 25, tzinfo=UTC),
        period_to=datetime(2026, 4, 26, tzinfo=UTC),
        fuels=inputs,
    )
    assert isinstance(result, TariffComparison)
    fc: FuelComparison = result.fuels[0]
    assert fc.current_total_pence == 110
    assert fc.target_total_pence == 90
    assert fc.delta_pence == -20
    assert result.total_delta_pence == -20
    assert "saves" in result.pounds_summary.lower() or "save" in result.pounds_summary.lower()


def test_includes_default_caveats() -> None:
    inputs = [
        FuelInputs(
            fuel="electricity",
            consumption=[],
            current_unit_rates=RateStream([_w("2026-04-01T00:00:00Z", None, 30.0)]),
            current_standing=RateStream([_w("2026-04-01T00:00:00Z", None, 50.0)]),
            target_unit_rates=RateStream([_w("2026-04-01T00:00:00Z", None, 20.0)]),
            target_standing=RateStream([_w("2026-04-01T00:00:00Z", None, 50.0)]),
        )
    ]
    result = compare_tariffs(
        target_product_code="X",
        period_from=datetime(2026, 4, 25, tzinfo=UTC),
        period_to=datetime(2026, 4, 26, tzinfo=UTC),
        fuels=inputs,
    )
    text = " ".join(result.caveats).lower()
    assert "saving sessions" in text
    assert "behaviour" in text or "behavior" in text


def test_per_day_breakdown_present() -> None:
    consumption = [
        _c("2026-04-25T12:00:00Z", 2.0),
        _c("2026-04-26T12:00:00Z", 3.0),
    ]
    inputs = [
        FuelInputs(
            fuel="electricity",
            consumption=consumption,
            current_unit_rates=RateStream([_w("2026-04-01T00:00:00Z", None, 30.0)]),
            current_standing=RateStream([_w("2026-04-01T00:00:00Z", None, 50.0)]),
            target_unit_rates=RateStream([_w("2026-04-01T00:00:00Z", None, 20.0)]),
            target_standing=RateStream([_w("2026-04-01T00:00:00Z", None, 50.0)]),
        )
    ]
    result = compare_tariffs(
        target_product_code="X",
        period_from=datetime(2026, 4, 25, tzinfo=UTC),
        period_to=datetime(2026, 4, 27, tzinfo=UTC),
        fuels=inputs,
    )
    assert len(result.breakdown_by_day) == 2
    days = [d.date.isoformat() for d in result.breakdown_by_day]
    assert "2026-04-25" in days


def test_daily_breakdown_is_dated_by_uk_day_in_bst() -> None:
    """Second review, confirmed by running it: 1-4 Sep came back as 31 Aug - 2 Sep."""
    from datetime import date

    from octopus_mcp.tools.period import PeriodSpec, resolve_period

    pf, pt = resolve_period(
        PeriodSpec(kind="explicit", from_date=date(2026, 9, 1), to_date=date(2026, 9, 4)),
        now=datetime(2026, 10, 3, tzinfo=UTC),
    )
    flat = RateStream([RateWindow(datetime(2026, 1, 1, tzinfo=UTC), None, 10.0)])
    result = compare_tariffs(
        target_product_code="X",
        period_from=pf,
        period_to=pt,
        fuels=[
            FuelInputs(
                fuel="electricity",
                consumption=[],
                current_unit_rates=flat,
                current_standing=flat,
                target_unit_rates=flat,
                target_standing=flat,
            )
        ],
    )
    assert result.period == (date(2026, 9, 1), date(2026, 9, 4))
    assert [d.date for d in result.breakdown_by_day] == [
        date(2026, 9, 1),
        date(2026, 9, 2),
        date(2026, 9, 3),
    ]
