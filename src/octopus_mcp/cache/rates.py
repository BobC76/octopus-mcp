"""Tariff unit-rate and standing-charge repository."""

from __future__ import annotations

import os
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime

DIRECT_DEBIT = "DIRECT_DEBIT"
NON_DIRECT_DEBIT = "NON_DIRECT_DEBIT"


def payment_method_preference() -> str:
    """Which published price to use where Octopus offers both: OCTOPUS_PAYMENT_METHOD,
    default DIRECT_DEBIT (what most customers pay, and what Home Assistant shows)."""
    value = os.environ.get("OCTOPUS_PAYMENT_METHOD", DIRECT_DEBIT).strip().upper()
    return NON_DIRECT_DEBIT if value == NON_DIRECT_DEBIT else DIRECT_DEBIT


def _iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC).isoformat()


def _parse(s: str | None) -> datetime | None:
    return datetime.fromisoformat(s.replace("Z", "+00:00")) if s else None


@dataclass(frozen=True)
class RateRow:
    valid_from: datetime
    valid_to: datetime | None
    value_inc_vat: float
    value_exc_vat: float
    payment_method: str = ""  # DIRECT_DEBIT / NON_DIRECT_DEBIT, or "" when not specified


class RatesRepo:
    """Rates are stored per payment method and one is chosen per window at read time.

    The API publishes DIRECT_DEBIT and NON_DIRECT_DEBIT rows for the same window; keying on
    (tariff_code, valid_from) alone let the last one written replace the other.
    """

    def __init__(self, conn: sqlite3.Connection, *, prefer: str | None = None) -> None:
        self._conn = conn
        self.prefer = prefer or payment_method_preference()

    def upsert_unit_rates(self, *, tariff_code: str, fuel: str, rows: list[RateRow]) -> None:
        self._upsert("unit_rates", tariff_code, fuel, rows)

    def upsert_standing_charges(self, *, tariff_code: str, fuel: str, rows: list[RateRow]) -> None:
        self._upsert("standing_charges", tariff_code, fuel, rows)

    def _upsert(self, table: str, tariff_code: str, fuel: str, rows: list[RateRow]) -> None:
        with self._conn:
            self._conn.executemany(
                f"""
                INSERT INTO {table}
                    (tariff_code, fuel, valid_from, valid_to, value_inc_vat, value_exc_vat,
                     payment_method)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(tariff_code, valid_from, payment_method)
                DO UPDATE SET valid_to=excluded.valid_to,
                              value_inc_vat=excluded.value_inc_vat,
                              value_exc_vat=excluded.value_exc_vat,
                              fuel=excluded.fuel
                """,
                [
                    (
                        tariff_code,
                        fuel,
                        _iso(r.valid_from),
                        _iso(r.valid_to),
                        r.value_inc_vat,
                        r.value_exc_vat,
                        r.payment_method or "",
                    )
                    for r in rows
                ],
            )

    def get_unit_rates(self, tariff_code: str) -> list[RateRow]:
        return self._select("unit_rates", tariff_code)

    def get_standing_charges(self, tariff_code: str) -> list[RateRow]:
        return self._select("standing_charges", tariff_code)

    def has_payment_choice(self, tariff_code: str) -> bool:
        """True when any window of this tariff is published under both payment methods."""
        row = self._conn.execute(
            """
            SELECT 1 FROM unit_rates WHERE tariff_code=? AND payment_method IN (?, ?)
             GROUP BY valid_from HAVING COUNT(DISTINCT payment_method) = 2 LIMIT 1
            """,
            (tariff_code, DIRECT_DEBIT, NON_DIRECT_DEBIT),
        ).fetchone()
        return row is not None

    def _select(self, table: str, tariff_code: str) -> list[RateRow]:
        rows = self._conn.execute(
            f"SELECT valid_from, valid_to, value_inc_vat, value_exc_vat, payment_method"
            f" FROM {table} WHERE tariff_code=? ORDER BY valid_from",
            (tariff_code,),
        ).fetchall()
        # One row per window, chosen PER WINDOW: the preferred method, then an unspecified
        # one, then the other. A window published under one method only is kept as is.
        rank = {self.prefer: 0, "": 1}
        best: dict[str, sqlite3.Row] = {}
        for r in rows:
            cur = best.get(r["valid_from"])
            if cur is None or rank.get(r["payment_method"], 2) < rank.get(cur["payment_method"], 2):
                best[r["valid_from"]] = r
        return [
            RateRow(
                valid_from=_parse(r["valid_from"]),  # type: ignore[arg-type]
                valid_to=_parse(r["valid_to"]),
                value_inc_vat=r["value_inc_vat"],
                value_exc_vat=r["value_exc_vat"],
                payment_method=r["payment_method"],
            )
            for r in best.values()
        ]
