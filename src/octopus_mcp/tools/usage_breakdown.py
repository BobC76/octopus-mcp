"""usage_breakdown thick tool."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal

from octopus_mcp.analysis.aggregations import GroupBy, aggregate, summary_stats
from octopus_mcp.analysis.billing import ConsumptionPoint
from octopus_mcp.tools.meter_select import consumption_for
from octopus_mcp.tools.period import PeriodSpec, period_label, resolve_period

GroupByLiteral = Literal["hour", "day", "week", "month"]


async def usage_breakdown(
    *,
    period: PeriodSpec,
    group_by: GroupByLiteral = "day",
    ctx: Any,
    now: datetime | None = None,
) -> dict[str, Any]:
    now = now or datetime.now(UTC)
    await ctx.ensure_account_bootstrapped()
    period_from, period_to = resolve_period(period, now=now)
    gb = GroupBy(group_by)

    breakdowns: dict[str, list[dict[str, Any]]] = {}
    stats: dict[str, dict[str, float]] = {}

    # Keys stay "electricity" (import) and "gas"; export, when the account has it, is
    # reported separately so it can never be mistaken for usage.
    for key, fuel, is_export in (
        ("electricity", "electricity", False),
        ("electricity_export", "electricity", True),
        ("gas", "gas", False),
    ):
        rows = await consumption_for(
            ctx,
            fuel=fuel,
            period_from=period_from,
            period_to=period_to,
            now=now,
            is_export=is_export,
        )
        if rows is None:
            continue
        points = [
            ConsumptionPoint(interval_start=r.interval_start, consumption_kwh=r.consumption_kwh)
            for r in rows
        ]
        buckets = aggregate(points, group_by=gb)
        breakdowns[key] = [{"label": b.label, "kwh": b.kwh} for b in buckets]
        s = summary_stats([b.kwh for b in buckets])
        stats[key] = {"min": s.min, "max": s.max, "mean": s.mean, "stdev": s.stdev}

    return {
        "period": period_label(period_from, period_to),
        "group_by": group_by,
        "fuel_breakdowns": breakdowns,
        "stats": stats,
    }
