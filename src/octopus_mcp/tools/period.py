"""Period specification + resolution."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any, Literal

from octopus_mcp.uktime import LONDON, uk_date, uk_midnight

# Octopus bills UK days, and usage_breakdown buckets by UK-local day. Periods were built on
# UTC midnights, so during BST every boundary was an hour out - on a time-of-use tariff the
# overnight cheap-rate charging straddles exactly that hour.

PeriodKind = Literal["last_month", "last_7_days", "last_quarter", "ytd", "explicit"]


@dataclass(frozen=True)
class PeriodSpec:
    kind: PeriodKind
    from_date: date | None = None
    to_date: date | None = None

    def __post_init__(self) -> None:
        if self.kind == "explicit":
            if self.from_date is None or self.to_date is None:
                raise ValueError("explicit period requires from_date and to_date")
            if self.from_date >= self.to_date:
                raise ValueError("from_date must be before to_date")
            if (self.to_date - self.from_date).days > 366 * 2:
                raise ValueError("period exceeds maximum of 2 years")


_start_of = uk_midnight


def period_label(period_from: datetime, period_to: datetime) -> dict[str, Any]:
    """The {from, to} dates shown to the user, as UK dates (``to`` is exclusive)."""
    return {"from": uk_date(period_from).isoformat(), "to": uk_date(period_to).isoformat()}


def resolve_period(spec: PeriodSpec, *, now: datetime) -> tuple[datetime, datetime]:
    """Resolve to a [from, to) range of UTC datetimes on UK-local midnights."""
    now = now.astimezone(LONDON)
    if spec.kind == "last_7_days":
        # Seven UK DAYS, not 7 x 24 h: across a clock change those differ by an hour.
        return _start_of(now.date() - timedelta(days=7)), _start_of(now.date())
    if spec.kind == "last_month":
        first_this = now.date().replace(day=1)
        last_first = (first_this - timedelta(days=1)).replace(day=1)
        return _start_of(last_first), _start_of(first_this)
    if spec.kind == "last_quarter":
        first_this = now.date().replace(day=1)
        end = _start_of(first_this)
        m = first_this.month - 3
        y = first_this.year
        while m <= 0:
            m += 12
            y -= 1
        return _start_of(date(y, m, 1)), end
    if spec.kind == "ytd":
        return _start_of(date(now.year, 1, 1)), _start_of(now.date())
    if spec.kind == "explicit":
        assert spec.from_date and spec.to_date
        if _start_of(spec.to_date) > now:
            raise ValueError("period extends into the future")
        return _start_of(spec.from_date), _start_of(spec.to_date)
    raise ValueError(f"unknown PeriodKind: {spec.kind}")
