"""current_tariff and unit_rates thick tools."""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from itertools import pairwise
from typing import Any, Literal
from zoneinfo import ZoneInfo

from octopus_mcp.analysis.rate_lookup import RateStream

_LONDON = ZoneInfo("Europe/London")

# (label, fuel, is_export). Import first: it is what "my tariff" means.
_SOURCES: tuple[tuple[str, str, bool], ...] = (
    ("electricity", "electricity", False),
    ("electricity_export", "electricity", True),
    ("gas", "gas", False),
)

_INTELLIGENT_CAVEAT = (
    "Intelligent Octopus: smart-charge dispatches can bill at the off-peak rate outside "
    "these windows. Dispatches are not modelled here."
)

Direction = Literal["import", "export"]


async def current_tariff(*, ctx: Any, now: datetime | None = None) -> dict[str, Any]:
    """The agreement in force now for import, export (if any) and gas, priced at ``now``.

    The rate reported is the one valid at ``now``. It used to be the most recently
    PUBLISHED rate, which on time-of-use or Agile tariffs is often a different band.
    """
    now = now or datetime.now(UTC)
    await ctx.ensure_account_bootstrapped()
    out: list[dict[str, Any]] = []

    for label, fuel, is_export in _SOURCES:
        assignment = ctx.meters_repo.current_tariff_assignment(
            account_number=ctx.creds.account_number, fuel=fuel, at=now, is_export=is_export
        )
        if assignment is None:
            continue
        rates = dict(
            product_code=assignment.product_code, tariff_code=assignment.tariff_code, fuel=fuel
        )
        await ctx.ensure_rates(**rates)
        unit_now = RateStream.from_rows(ctx.rates_repo.get_unit_rates(assignment.tariff_code))
        if unit_now.rate_at(now) is None:
            # Cached within the TTL but before the current window was published (day-ahead
            # tariffs): refetch once rather than report no rate.
            await ctx.ensure_rates(**rates, force=True)
            unit_now = RateStream.from_rows(ctx.rates_repo.get_unit_rates(assignment.tariff_code))
        sc_rates = ctx.rates_repo.get_standing_charges(assignment.tariff_code)
        day_start, day_end = _local_day_bounds(now.astimezone(_LONDON).date())
        todays_prices = {
            b["pence_inc_vat"]
            for b in day_bands(
                ctx.rates_repo.get_unit_rates(assignment.tariff_code), day_start, day_end
            )
        }
        entry: dict[str, Any] = {
            "fuel": fuel,
            "direction": "export" if is_export else "import",
            "label": label,
            "product_code": assignment.product_code,
            "tariff_code": assignment.tariff_code,
            "unit_rate_pence_inc_vat": unit_now.rate_at(now),
            "standing_charge_pence_inc_vat": RateStream.from_rows(sc_rates).rate_at(now),
        }
        # Only when the price changes WITHIN today - a flat tariff with past price-cap
        # changes has several historical values but one rate all day.
        if len(todays_prices) > 1:
            entry["note"] = (
                "Unit rate varies through the day; this is the rate right now. "
                "Use unit_rates for the full day's bands."
            )
        out.append(entry)

    return {"as_of": now.isoformat(), "fuels": out}


def _local_day_bounds(day: date) -> tuple[datetime, datetime]:
    start = datetime.combine(day, time(0), tzinfo=_LONDON)
    end = datetime.combine(day + timedelta(days=1), time(0), tzinfo=_LONDON)
    return start.astimezone(UTC), end.astimezone(UTC)


def day_bands(rows: Any, day_start: datetime, day_end: datetime) -> list[dict[str, Any]]:
    """Rate windows clipped to one day, with adjacent equal-priced windows merged."""
    clipped: list[tuple[datetime, datetime, float]] = []
    for r in sorted(rows, key=lambda r: r.valid_from):
        start = max(r.valid_from, day_start)
        end = min(r.valid_to, day_end) if r.valid_to is not None else day_end
        if start < end:
            clipped.append((start, end, r.value_inc_vat))
    merged: list[tuple[datetime, datetime, float]] = []
    for start, end, value in clipped:
        if merged and merged[-1][2] == value and merged[-1][1] == start:
            merged[-1] = (merged[-1][0], end, value)
        else:
            merged.append((start, end, value))
    return [
        {
            "from_local": s.astimezone(_LONDON).strftime("%H:%M"),
            "to_local": e.astimezone(_LONDON).strftime("%H:%M"),
            "from": s.isoformat(),
            "to": e.isoformat(),
            "pence_inc_vat": v,
        }
        for s, e, v in merged
    ]


def _covers(bands: list[dict[str, Any]], day_start: datetime, day_end: datetime) -> bool:
    """True when the bands run contiguously from day_start to day_end."""
    if not bands:
        return False
    edges = [(datetime.fromisoformat(b["from"]), datetime.fromisoformat(b["to"])) for b in bands]
    contiguous = all(prev[1] == nxt[0] for prev, nxt in pairwise(edges))
    return contiguous and edges[0][0] == day_start and edges[-1][1] == day_end


async def unit_rates(
    *,
    ctx: Any,
    day: date | None = None,
    direction: Direction = "import",
    now: datetime | None = None,
) -> dict[str, Any]:
    """Electricity unit-rate bands for one UK-local day, e.g. peak and off-peak."""
    now = now or datetime.now(UTC)
    day = day or now.astimezone(_LONDON).date()
    is_export = direction == "export"
    await ctx.ensure_account_bootstrapped()
    day_start, day_end = _local_day_bounds(day)

    assignment = ctx.meters_repo.current_tariff_assignment(
        account_number=ctx.creds.account_number,
        fuel="electricity",
        at=day_start,
        is_export=is_export,
    )
    if assignment is None:
        return {
            "date": day.isoformat(),
            "direction": direction,
            "bands": [],
            "caveats": [f"No electricity {direction} agreement covers this date."],
        }
    rates = dict(
        product_code=assignment.product_code,
        tariff_code=assignment.tariff_code,
        fuel="electricity",
    )
    await ctx.ensure_rates(**rates)
    bands = day_bands(ctx.rates_repo.get_unit_rates(assignment.tariff_code), day_start, day_end)
    if not _covers(bands, day_start, day_end):
        # e.g. Agile's tomorrow, published after the cache was filled: refetch once.
        await ctx.ensure_rates(**rates, force=True)
        bands = day_bands(ctx.rates_repo.get_unit_rates(assignment.tariff_code), day_start, day_end)

    caveats: list[str] = []
    if not bands:
        caveats.append(
            "No rates found for this date. Agile publishes the next day around 16:00, and "
            "only the most recent ~1500 rate windows are fetched."
        )
    elif not _covers(bands, day_start, day_end):
        caveats.append("Rates cover only part of this date; the rest is not yet published.")
    if assignment.product_code.startswith("INTELLI-") and not is_export:
        caveats.append(_INTELLIGENT_CAVEAT)
    return {
        "date": day.isoformat(),
        "direction": direction,
        "product_code": assignment.product_code,
        "tariff_code": assignment.tariff_code,
        "bands": bands,
        "caveats": caveats,
    }
