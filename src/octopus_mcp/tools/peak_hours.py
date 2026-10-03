"""peak_hours thick tool."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from octopus_mcp.analysis.billing import ConsumptionPoint
from octopus_mcp.analysis.peaks import top_n_half_hours
from octopus_mcp.tools.meter_select import consumption_for
from octopus_mcp.tools.period import PeriodSpec, resolve_period


async def peak_hours(
    *,
    period: PeriodSpec,
    top_n: int = 10,
    ctx: Any,
    now: datetime | None = None,
) -> dict[str, Any]:
    now = now or datetime.now(UTC)
    await ctx.ensure_account_bootstrapped()
    period_from, period_to = resolve_period(period, now=now)

    out_fuels: dict[str, list[dict[str, Any]]] = {}
    for fuel in ("electricity", "gas"):
        # Import only: the highest-USAGE half-hours. Export peaks are not usage.
        rows = await consumption_for(
            ctx, fuel=fuel, period_from=period_from, period_to=period_to, now=now
        )
        if rows is None:
            continue
        points = [
            ConsumptionPoint(interval_start=r.interval_start, consumption_kwh=r.consumption_kwh)
            for r in rows
        ]
        peaks = top_n_half_hours(points, n=top_n)
        out_fuels[fuel] = [
            {"interval_start": p.interval_start.isoformat(), "consumption_kwh": p.consumption_kwh}
            for p in peaks
        ]

    return {
        "period": {"from": period_from.date().isoformat(), "to": period_to.date().isoformat()},
        "top_n": top_n,
        "fuels": out_fuels,
    }
