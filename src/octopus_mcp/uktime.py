"""UK-local day boundaries.

Octopus bills UK days, so every period edge, standing-charge day and per-day breakdown
is a UK-local midnight. Kept in one place because the conversion is DST-sensitive: a UK
day is 23 or 25 hours long at the clock changes, so "a UTC midnight plus N x 24 h" is
wrong for part of every year.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time
from zoneinfo import ZoneInfo

LONDON = ZoneInfo("Europe/London")


def uk_midnight(d: date) -> datetime:
    """The UK-local midnight that starts ``d``, as a UTC datetime."""
    return datetime.combine(d, time(0), tzinfo=LONDON).astimezone(UTC)


def uk_date(ts: datetime) -> date:
    """The UK calendar date containing ``ts`` (naive values are taken as UTC)."""
    return (ts if ts.tzinfo else ts.replace(tzinfo=UTC)).astimezone(LONDON).date()
