"""compare_tariff thick tool."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal

from octopus_mcp.analysis.billing import ConsumptionPoint
from octopus_mcp.analysis.rate_lookup import RateStream, RateWindow
from octopus_mcp.analysis.tariff_comparison import FuelInputs, compare_tariffs
from octopus_mcp.octopus.errors import NotFoundError
from octopus_mcp.tools.meter_select import consumption_for
from octopus_mcp.tools.period import PeriodSpec, resolve_period

FuelChoice = Literal["electricity", "gas", "both"]


def _windows(rows: Any) -> list[RateWindow]:
    return [
        RateWindow(valid_from=r.valid_from, valid_to=r.valid_to, value_inc_vat=r.value_inc_vat)
        for r in rows
    ]


def _intelligent_octopus_guard(target_product_code: str) -> None:
    if target_product_code.upper().startswith("INTELLI"):
        raise NotFoundError(
            "Intelligent Octopus comparison requires dispatch history not available without active enrolment. "
            "Try Cosy or Go for time-of-use comparison.",
            resource=target_product_code,
        )


async def compare_tariff(
    *,
    target_product_code: str,
    period: PeriodSpec,
    fuel: FuelChoice = "both",
    ctx: Any,
    now: datetime | None = None,
) -> dict[str, Any]:
    _intelligent_octopus_guard(target_product_code)
    now = now or datetime.now(UTC)
    await ctx.ensure_account_bootstrapped()
    period_from, period_to = resolve_period(period, now=now)

    fuels: list[FuelInputs] = []
    fuels_to_check: tuple[str, ...] = ("electricity", "gas") if fuel == "both" else (fuel,)

    for f in fuels_to_check:
        # Import only. Swapping the import tariff does not change export payments, so the
        # export meter is out of this comparison by design (see the caveat added below).
        rows = await consumption_for(
            ctx, fuel=f, period_from=period_from, period_to=period_to, now=now
        )
        if rows is None:
            continue
        current = ctx.meters_repo.current_tariff_assignment(
            account_number=ctx.creds.account_number, fuel=f, at=period_from
        )
        if current is None:
            continue
        target_tariff = await ctx.resolve_target_tariff_code(target_product_code, f)

        await ctx.ensure_rates(
            product_code=current.product_code, tariff_code=current.tariff_code, fuel=f
        )
        await ctx.ensure_rates(product_code=target_product_code, tariff_code=target_tariff, fuel=f)

        fuels.append(
            FuelInputs(
                fuel=f,  # type: ignore[arg-type]
                consumption=[
                    ConsumptionPoint(
                        interval_start=r.interval_start, consumption_kwh=r.consumption_kwh
                    )
                    for r in rows
                ],
                current_unit_rates=RateStream(
                    _windows(ctx.rates_repo.get_unit_rates(current.tariff_code))
                ),
                current_standing=RateStream(
                    _windows(ctx.rates_repo.get_standing_charges(current.tariff_code))
                ),
                target_unit_rates=RateStream(
                    _windows(ctx.rates_repo.get_unit_rates(target_tariff))
                ),
                target_standing=RateStream(
                    _windows(ctx.rates_repo.get_standing_charges(target_tariff))
                ),
            )
        )

    if not fuels:
        raise NotFoundError("No fuels available to compare", resource="account")

    caveats_extra: list[str] = []
    if "electricity" in fuels_to_check and ctx.meters_repo.list_meters_for_account(
        ctx.creds.account_number, fuel="electricity", is_export=True
    ):
        caveats_extra.append(
            "This account also exports electricity. Export is excluded: it is paid on its own "
            "export tariff, which an import tariff swap does not change - but check whether "
            "your export tariff requires a particular import tariff."
        )

    result = compare_tariffs(
        target_product_code=target_product_code,
        period_from=period_from,
        period_to=period_to,
        fuels=fuels,
    )
    return {
        "period": {"from": result.period[0].isoformat(), "to": result.period[1].isoformat()},
        "target_product_code": result.target_product_code,
        "fuels": [
            {
                "fuel": fc.fuel,
                "current_unit_pence": fc.current_unit_pence,
                "current_standing_pence": fc.current_standing_pence,
                "current_total_pence": fc.current_total_pence,
                "target_unit_pence": fc.target_unit_pence,
                "target_standing_pence": fc.target_standing_pence,
                "target_total_pence": fc.target_total_pence,
                "delta_pence": fc.delta_pence,
            }
            for fc in result.fuels
        ],
        "total_current_pence": result.total_current_pence,
        "total_target_pence": result.total_target_pence,
        "total_delta_pence": result.total_delta_pence,
        "pounds_summary": result.pounds_summary,
        "breakdown_by_day": [
            {
                "date": d.date.isoformat(),
                "current_pence": d.current_pence,
                "target_pence": d.target_pence,
                "delta_pence": d.delta_pence,
            }
            for d in result.breakdown_by_day
        ],
        "caveats": [*result.caveats, *caveats_extra],
    }
