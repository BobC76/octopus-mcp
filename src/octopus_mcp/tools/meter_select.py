"""Choose which meters a tool reads, by direction, and merge their consumption.

Every consumption tool used ``list_meters_for_account(...)[0]``. On an account with solar
export the electricity meter points include an export MPAN, and nothing guaranteed the
first row was the import meter - so usage, peaks and bills could silently be built from
exported kWh. An MPAN can also carry more than one meter serial after a meter exchange,
and only one of them has data for any given period.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any


async def consumption_for(
    ctx: Any,
    *,
    fuel: str,
    period_from: datetime,
    period_to: datetime,
    now: datetime,
    is_export: bool = False,
) -> list[Any] | None:
    """Consumption rows for one fuel and direction, across every matching meter.

    Returns ``None`` when the account has no meter in that direction (so callers can tell
    "no export meter" from "export meter with no readings yet"), else rows sorted by
    ``interval_start``.

    Rows from several meters are MERGED by interval, never summed: after an exchange the
    old and new serial cover different periods, and some accounts report the same
    readings under both. If two meters disagree about one interval, the first meter (in
    insertion order) wins.
    """
    meters = ctx.meters_repo.list_meters_for_account(
        ctx.creds.account_number, fuel=fuel, is_export=is_export
    )
    if not meters:
        return None
    merged: dict[datetime, Any] = {}
    for meter in meters:
        rows = await ctx.consumption_syncer.ensure(
            fuel=fuel,
            mpan_or_mprn=meter.mpan_or_mprn,
            serial_number=meter.serial_number,
            period_from=period_from,
            period_to=period_to,
            now=now,
        )
        for r in rows:
            merged.setdefault(r.interval_start, r)
    return [merged[k] for k in sorted(merged)]
