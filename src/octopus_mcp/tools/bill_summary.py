"""bill_summary thick tool."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from octopus_mcp.analysis.billing import ConsumptionPoint, compute_period_cost
from octopus_mcp.analysis.rate_lookup import RateStream
from octopus_mcp.octopus.errors import OctopusError
from octopus_mcp.tools.meter_select import consumption_for
from octopus_mcp.tools.period import PeriodSpec, resolve_period


async def bill_summary(
    *, period: PeriodSpec, ctx: Any, now: datetime | None = None
) -> dict[str, Any]:
    now = now or datetime.now(UTC)
    await ctx.ensure_account_bootstrapped()
    period_from, period_to = resolve_period(period, now=now)

    fuels_unavailable: list[str] = []
    fuel_outputs: list[dict[str, Any]] = []
    total_pence = 0
    export_credit_pence = 0
    export_problems: list[str] = []

    # (fuel, is_export). Export is optional: an account without it is normal, so a missing
    # export meter is skipped silently rather than reported as unavailable.
    for fuel, is_export in (("electricity", False), ("electricity", True), ("gas", False)):
        # Cheap local checks first, so no consumption is fetched for a fuel we cannot price.
        if not ctx.meters_repo.list_meters_for_account(
            ctx.creds.account_number, fuel=fuel, is_export=is_export
        ):
            if not is_export:
                fuels_unavailable.append(fuel)
            continue
        assignment = ctx.meters_repo.current_tariff_assignment(
            account_number=ctx.creds.account_number,
            fuel=fuel,
            at=period_from,
            is_export=is_export,
        )
        if assignment is None:
            if not is_export:
                fuels_unavailable.append(fuel)
            continue

        try:
            await ctx.ensure_rates(
                product_code=assignment.product_code,
                tariff_code=assignment.tariff_code,
                fuel=fuel,
            )
        except OctopusError as e:
            if not is_export:
                raise
            # Export is a side figure: never let it take the import and gas bill down.
            export_problems.append(f"Export not priced: {type(e).__name__}: {e}")
            continue

        unit_rate_rows = ctx.rates_repo.get_unit_rates(assignment.tariff_code)
        sc_rate_rows = ctx.rates_repo.get_standing_charges(assignment.tariff_code)

        consumption_rows = await consumption_for(
            ctx,
            fuel=fuel,
            period_from=period_from,
            period_to=period_to,
            now=now,
            is_export=is_export,
        )
        assert consumption_rows is not None  # a meter exists, checked above
        consumption = [
            ConsumptionPoint(interval_start=r.interval_start, consumption_kwh=r.consumption_kwh)
            for r in consumption_rows
        ]
        cost = compute_period_cost(
            consumption=consumption,
            unit_rates=RateStream.from_rows(unit_rate_rows),
            standing_charges=RateStream.from_rows(sc_rate_rows),
            period_from=period_from,
            period_to=period_to,
        )
        total_kwh = sum(
            c.consumption_kwh for c in consumption if period_from <= c.interval_start < period_to
        )
        entry: dict[str, Any] = {
            "fuel": fuel,
            "direction": "export" if is_export else "import",
            "tariff_code": assignment.tariff_code,
            "kwh": round(total_kwh, 4),
            "unit_pence": cost.unit_pence,
            "standing_pence": cost.standing_pence,
            "total_pence": cost.total_pence,
            "caveats": cost.caveats,
        }
        if is_export:
            # Money paid TO the customer: reported, but never added to the bill total.
            # Export tariffs have no standing charge, so its "missing" caveat is noise.
            entry["caveats"] = [
                *(c for c in cost.caveats if not c.startswith("Missing standing charge")),
                "Export: total_pence is a credit (paid to you), not a charge.",
            ]
            export_credit_pence += cost.total_pence
        else:
            total_pence += cost.total_pence
        fuel_outputs.append(entry)

    return {
        "period": {"from": period_from.date().isoformat(), "to": period_to.date().isoformat()},
        "fuels": fuel_outputs,
        "fuels_unavailable": fuels_unavailable,
        "caveats": export_problems,
        "totals": {
            "total_pence": total_pence,
            "pounds": f"£{total_pence / 100:.2f}",
            "export_credit_pence": export_credit_pence,
            "net_pence": total_pence - export_credit_pence,
            "net_pounds": f"£{(total_pence - export_credit_pence) / 100:.2f}",
        },
    }
