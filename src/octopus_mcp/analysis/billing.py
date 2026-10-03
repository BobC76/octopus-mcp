"""Period billing math: unit cost + standing charge."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo

from octopus_mcp.analysis.rate_lookup import RateStream

_LONDON = ZoneInfo("Europe/London")


@dataclass(frozen=True)
class ConsumptionPoint:
    interval_start: datetime
    consumption_kwh: float


@dataclass(frozen=True)
class PeriodCost:
    unit_pence: int
    standing_pence: int
    total_pence: int
    caveats: list[str] = field(default_factory=list)


def _round_pence(value_pence_float: float) -> int:
    return round(value_pence_float)


def compute_period_cost(
    *,
    consumption: Iterable[ConsumptionPoint],
    unit_rates: RateStream,
    standing_charges: RateStream,
    period_from: datetime,
    period_to: datetime,
) -> PeriodCost:
    caveats: list[str] = []

    # --- unit cost ---
    unit_total_p = 0.0
    skipped = 0
    for c in consumption:
        if c.interval_start < period_from or c.interval_start >= period_to:
            continue
        rate = unit_rates.rate_at(c.interval_start)
        if rate is None:
            skipped += 1
            continue
        unit_total_p += c.consumption_kwh * rate
    if skipped:
        caveats.append(f"Skipped {skipped} half-hour(s) with missing rate data")

    # --- standing charge: one per UK calendar day ---
    # Count local dates, not 24 h steps: a period spanning a clock change is 23 or 25 hours
    # longer or shorter, and stepping 24 h from a UTC midnight both mis-dates every day in
    # BST and adds a phantom day when the clocks go back.
    def _local(ts: datetime) -> datetime:
        return (ts if ts.tzinfo else ts.replace(tzinfo=UTC)).astimezone(_LONDON)

    # One charge per UK midnight in [period_from, period_to).
    standing_total_p = 0.0
    standing_skipped = 0
    start = _local(period_from)
    d = (
        start.date()
        if start.timetz().replace(tzinfo=None) == time(0)
        else start.date() + timedelta(days=1)
    )
    while (midnight := datetime.combine(d, time(0), tzinfo=_LONDON).astimezone(UTC)) < period_to:
        rate = standing_charges.rate_at(midnight)
        if rate is None:
            standing_skipped += 1
        else:
            standing_total_p += rate
        d += timedelta(days=1)
    if standing_skipped:
        caveats.append(f"Missing standing charge for {standing_skipped} day(s)")

    unit_p = _round_pence(unit_total_p)
    standing_p = _round_pence(standing_total_p)
    return PeriodCost(
        unit_pence=unit_p,
        standing_pence=standing_p,
        total_pence=unit_p + standing_p,
        caveats=caveats,
    )
