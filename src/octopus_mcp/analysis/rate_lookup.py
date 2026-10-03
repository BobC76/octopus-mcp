"""Step-function lookup over a tariff rate stream."""

from __future__ import annotations

from bisect import bisect_right
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from typing import Any


@dataclass(frozen=True)
class RateWindow:
    valid_from: datetime
    valid_to: datetime | None
    value_inc_vat: float


class RateStream:
    """Sorted rate windows; O(log n) lookup."""

    @classmethod
    def from_rows(cls, rows: Iterable[Any]) -> RateStream:
        """Build from any rows with valid_from / valid_to / value_inc_vat (e.g. RateRow)."""
        return cls(
            [
                RateWindow(
                    valid_from=r.valid_from, valid_to=r.valid_to, value_inc_vat=r.value_inc_vat
                )
                for r in rows
            ]
        )

    def __init__(self, windows: list[RateWindow]) -> None:
        self._windows = sorted(windows, key=lambda w: w.valid_from)
        self._starts = [w.valid_from for w in self._windows]

    def rate_at(self, ts: datetime) -> float | None:
        if not self._windows:
            return None
        i = bisect_right(self._starts, ts) - 1
        if i < 0:
            return None
        win = self._windows[i]
        if win.valid_to is not None and ts >= win.valid_to:
            return None
        return win.value_inc_vat
