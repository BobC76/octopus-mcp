"""Choose which meters a tool reads, by direction, and combine their consumption.

Every consumption tool used ``list_meters_for_account(...)[0]``. On an account with solar
export the electricity meter points include an export MPAN, and nothing guaranteed the
first row was the import meter - so usage, peaks and bills could silently be built from
exported kWh. An MPAN can also carry more than one meter serial after a meter exchange,
and only one of them has data for any given period.
"""

from __future__ import annotations

import asyncio
from collections import defaultdict
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
    """Consumption for one fuel and direction, as ``(interval_start, consumption_kwh)`` rows.

    Returns ``None`` when the account has no meter in that direction (so callers can tell
    "no export meter" from "export meter with no readings yet"), else rows sorted by
    ``interval_start``.

    - Serials on the SAME MPAN are MERGED by interval, never summed: after an exchange the
      old and new serial cover different periods, and some accounts report the same
      readings under both. On a clash a non-zero reading beats a zero, then the serial
      with the newest data wins - so an exchanged-out meter reporting zeros cannot mask
      the live one.
    - DIFFERENT MPANs (e.g. a related Economy 7 storage-heater MPAN, or two properties)
      are separate supplies, so they are SUMMED.
    """
    meters = ctx.meters_repo.list_meters_for_account(
        ctx.creds.account_number, fuel=fuel, is_export=is_export
    )
    if not meters:
        return None
    fetched = await asyncio.gather(
        *(
            ctx.consumption_syncer.ensure(
                fuel=fuel,
                mpan_or_mprn=m.mpan_or_mprn,
                serial_number=m.serial_number,
                period_from=period_from,
                period_to=period_to,
                now=now,
            )
            for m in meters
        )
    )

    by_mpan: dict[str, list[list[Any]]] = defaultdict(list)
    for meter, rows in zip(meters, fetched, strict=True):
        by_mpan[meter.mpan_or_mprn].append(rows)

    totals: dict[datetime, float] = defaultdict(float)
    for serial_rows in by_mpan.values():
        # On a clashing interval: a non-zero reading beats a zero (an exchanged-out meter
        # can keep reporting 0.0), then the serial with the newest data wins.
        serial_rows.sort(key=_newest_reading, reverse=True)
        merged: dict[datetime, float] = {}
        for rows in serial_rows:
            for r in rows:
                if merged.get(r.interval_start, 0.0) == 0.0:
                    merged[r.interval_start] = r.consumption_kwh
        for start, kwh in merged.items():
            totals[start] += kwh
    return [_Point(start, totals[start]) for start in sorted(totals)]


def _newest_reading(rows: list[Any]) -> float:
    """Sort key: timestamp of a serial's latest reading; a serial with none sorts last."""
    return max((r.interval_start.timestamp() for r in rows), default=float("-inf"))


class _Point:
    """Minimal row: the tools only read ``interval_start`` and ``consumption_kwh``."""

    __slots__ = ("consumption_kwh", "interval_start")

    def __init__(self, interval_start: datetime, consumption_kwh: float) -> None:
        self.interval_start = interval_start
        self.consumption_kwh = consumption_kwh
