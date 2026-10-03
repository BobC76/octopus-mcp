"""Accounts with solar export and a meter exchange.

The shape that broke 0.1.0, from a real account: the EXPORT meter point is stored first,
the import MPAN carries two meter serials (old and new), and import and export agreements
run concurrently. Every electricity tool read ``meters[0]`` (the export meter) and
current_tariff returned whichever agreement started most recently (the export one).
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock

from octopus_mcp.cache.db import _list_migrations, _split_sql, open_db, run_migrations
from octopus_mcp.cache.meters import MetersRepo
from octopus_mcp.cache.rates import RateRow, RatesRepo
from octopus_mcp.cache.sync_state import SyncStateRepo
from octopus_mcp.octopus.models import (
    Account,
    Agreement,
    ElectricityMeterPoint,
    Meter,
    Property,
)
from octopus_mcp.tools.bill_summary import bill_summary
from octopus_mcp.tools.context import build_helpers
from octopus_mcp.tools.current_tariff import current_tariff, unit_rates
from octopus_mcp.tools.peak_hours import peak_hours
from octopus_mcp.tools.period import PeriodSpec
from octopus_mcp.tools.usage_breakdown import usage_breakdown

IMPORT_TARIFF = "E-1R-INTELLI-VAR-24-10-29-A"
EXPORT_TARIFF = "E-1R-OUTGOING-VAR-24-10-26-A"
SAME_DAY = datetime(2024, 7, 18, 23, tzinfo=UTC)


def _account() -> Account:
    return Account(
        number="A-1",
        properties=[
            Property(
                id=1,
                electricity_meter_points=[
                    # Export first, as the API returned it for the reporting account.
                    ElectricityMeterPoint(
                        mpan="EXPORT-MPAN",
                        is_export=True,
                        meters=[Meter(serial_number="EXP1")],
                        agreements=[
                            Agreement(
                                tariff_code="E-1R-OUTGOING-FIX-12M-19-05-13-A",
                                valid_from=SAME_DAY,
                                valid_to=datetime(2025, 7, 18, 23, tzinfo=UTC),
                            ),
                            Agreement(
                                tariff_code=EXPORT_TARIFF,
                                valid_from=datetime(2025, 7, 18, 23, tzinfo=UTC),
                            ),
                        ],
                    ),
                    ElectricityMeterPoint(
                        mpan="IMPORT-MPAN",
                        meters=[Meter(serial_number="OLD1"), Meter(serial_number="NEW1")],
                        agreements=[
                            # Starts the same instant as an export agreement: 001's primary
                            # key let one overwrite the other.
                            Agreement(
                                tariff_code="E-1R-INTELLI-VAR-22-10-14-A",
                                valid_from=SAME_DAY,
                                valid_to=datetime(2025, 6, 30, 23, tzinfo=UTC),
                            ),
                            Agreement(
                                tariff_code=IMPORT_TARIFF,
                                valid_from=datetime(2025, 6, 30, 23, tzinfo=UTC),
                            ),
                        ],
                    ),
                ],
            )
        ],
    )


@dataclass
class _Row:
    interval_start: datetime
    consumption_kwh: float


def _rows(start: datetime, kwh: list[float]) -> list[_Row]:
    return [_Row(start + timedelta(minutes=30 * i), v) for i, v in enumerate(kwh)]


DAY1 = datetime(2026, 9, 1, tzinfo=UTC)
DAY2 = datetime(2026, 9, 2, tzinfo=UTC)
CONSUMPTION = {
    # Export is large so any leak into an import figure is obvious.
    "EXP1": _rows(DAY1, [9.0, 9.0]) + _rows(DAY2, [9.0, 9.0]),
    # Meter exchanged between the two days; OLD1 also re-reports DAY2 00:00 identically.
    "OLD1": _rows(DAY1, [1.0, 2.0]) + _rows(DAY2, [3.0]),
    "NEW1": _rows(DAY2, [3.0, 4.0]),
}


async def _ctx() -> Any:
    conn = open_db(":memory:")
    ctx = MagicMock()
    ctx.creds.account_number = "A-1"
    ctx.conn = conn
    ctx.meters_repo = MetersRepo(conn)
    ctx.rates_repo = RatesRepo(conn)
    ctx.sync_state = SyncStateRepo(conn)
    ctx.rest = AsyncMock()
    ctx.rest.get_account.return_value = _account()

    async def _ensure(**k: Any) -> list[_Row]:
        return [
            r
            for r in CONSUMPTION[k["serial_number"]]
            if k["period_from"] <= r.interval_start < k["period_to"]
        ]

    ctx.consumption_syncer.ensure = AsyncMock(side_effect=_ensure)
    build_helpers(ctx)
    ctx.ensure_rates = AsyncMock()  # rates are seeded directly below
    return ctx


def _seed_rates(ctx: Any) -> None:
    # Intelligent Go shape as the API returns it: off-peak 23:30-05:30 UK time, i.e.
    # 22:30-04:30 UTC during BST.
    windows = []
    for d in range(-2, 3):
        base = datetime(2026, 10, 3, tzinfo=UTC) + timedelta(days=d)
        windows.append(
            (base - timedelta(hours=1, minutes=30), base + timedelta(hours=4, minutes=30), 7.0)
        )
        windows.append(
            (base + timedelta(hours=4, minutes=30), base + timedelta(hours=22, minutes=30), 28.0)
        )
    ctx.rates_repo.upsert_unit_rates(
        tariff_code=IMPORT_TARIFF,
        fuel="electricity",
        rows=[
            RateRow(valid_from=f, valid_to=t, value_inc_vat=v, value_exc_vat=v)
            for f, t, v in windows
        ],
    )
    for tariff, sc, unit in ((IMPORT_TARIFF, 55.0, None), (EXPORT_TARIFF, 0.0, 12.0)):
        ctx.rates_repo.upsert_standing_charges(
            tariff_code=tariff,
            fuel="electricity",
            rows=[
                RateRow(
                    valid_from=datetime(2025, 1, 1, tzinfo=UTC),
                    valid_to=None,
                    value_inc_vat=sc,
                    value_exc_vat=sc,
                )
            ],
        )
        if unit is not None:
            ctx.rates_repo.upsert_unit_rates(
                tariff_code=tariff,
                fuel="electricity",
                rows=[
                    RateRow(
                        valid_from=datetime(2025, 1, 1, tzinfo=UTC),
                        valid_to=None,
                        value_inc_vat=unit,
                        value_exc_vat=unit,
                    )
                ],
            )


async def test_agreements_are_kept_per_direction_including_same_day_starts() -> None:
    ctx = await _ctx()
    await ctx.ensure_account_bootstrapped()
    repo, now = ctx.meters_repo, datetime(2026, 10, 3, tzinfo=UTC)

    imp = repo.current_tariff_assignment(account_number="A-1", fuel="electricity", at=now)
    exp = repo.current_tariff_assignment(
        account_number="A-1", fuel="electricity", at=now, is_export=True
    )
    assert imp is not None and imp.tariff_code == IMPORT_TARIFF and not imp.is_export
    assert exp is not None and exp.tariff_code == EXPORT_TARIFF and exp.is_export

    then = SAME_DAY + timedelta(days=1)
    old_imp = repo.current_tariff_assignment(account_number="A-1", fuel="electricity", at=then)
    old_exp = repo.current_tariff_assignment(
        account_number="A-1", fuel="electricity", at=then, is_export=True
    )
    assert old_imp is not None and old_imp.product_code == "INTELLI-VAR-22-10-14"
    assert old_exp is not None and old_exp.product_code == "OUTGOING-FIX-12M-19-05-13"


async def test_current_tariff_reports_import_and_export_separately_at_the_live_rate() -> None:
    ctx = await _ctx()
    _seed_rates(ctx)
    # 02:00 BST on 2026-10-03 = 01:00 UTC: off-peak.
    out = await current_tariff(ctx=ctx, now=datetime(2026, 10, 3, 1, tzinfo=UTC))
    by_label = {f["label"]: f for f in out["fuels"]}

    assert by_label["electricity"]["tariff_code"] == IMPORT_TARIFF
    assert by_label["electricity"]["direction"] == "import"
    assert by_label["electricity"]["unit_rate_pence_inc_vat"] == 7.0
    assert "note" in by_label["electricity"]  # rate varies through the day
    assert by_label["electricity_export"]["tariff_code"] == EXPORT_TARIFF
    assert by_label["electricity_export"]["unit_rate_pence_inc_vat"] == 12.0

    peak = await current_tariff(ctx=ctx, now=datetime(2026, 10, 3, 12, tzinfo=UTC))
    assert {f["label"]: f for f in peak["fuels"]}["electricity"]["unit_rate_pence_inc_vat"] == 28.0


async def test_usage_never_reads_the_export_meter_and_merges_a_meter_exchange() -> None:
    ctx = await _ctx()
    period = PeriodSpec(kind="explicit", from_date=date(2026, 9, 1), to_date=date(2026, 9, 3))
    out = await usage_breakdown(
        period=period, group_by="day", ctx=ctx, now=datetime(2026, 10, 3, tzinfo=UTC)
    )

    imported = sum(b["kwh"] for b in out["fuel_breakdowns"]["electricity"])
    # OLD1 day 1 (1+2) + day 2 merged with NEW1: 3.0 at 00:00 once (not twice) + 4.0.
    assert imported == 10.0
    assert sum(b["kwh"] for b in out["fuel_breakdowns"]["electricity_export"]) == 36.0


async def test_peak_hours_are_import_only() -> None:
    ctx = await _ctx()
    period = PeriodSpec(kind="explicit", from_date=date(2026, 9, 1), to_date=date(2026, 9, 3))
    out = await peak_hours(period=period, top_n=3, ctx=ctx, now=datetime(2026, 10, 3, tzinfo=UTC))
    assert all(p["consumption_kwh"] < 9.0 for p in out["fuels"]["electricity"])


async def test_bill_counts_export_as_a_credit_not_a_charge() -> None:
    ctx = await _ctx()
    _seed_rates(ctx)
    # Seed an import rate covering September too, so the bill has prices.
    ctx.rates_repo.upsert_unit_rates(
        tariff_code=IMPORT_TARIFF,
        fuel="electricity",
        rows=[
            RateRow(
                valid_from=datetime(2026, 8, 1, tzinfo=UTC),
                valid_to=datetime(2026, 9, 30, tzinfo=UTC),
                value_inc_vat=20.0,
                value_exc_vat=20.0,
            )
        ],
    )
    period = PeriodSpec(kind="explicit", from_date=date(2026, 9, 1), to_date=date(2026, 9, 3))
    out = await bill_summary(period=period, ctx=ctx, now=datetime(2026, 10, 3, tzinfo=UTC))

    entries = {(f["fuel"], f["direction"]): f for f in out["fuels"]}
    imp, exp = entries[("electricity", "import")], entries[("electricity", "export")]
    assert imp["kwh"] == 10.0 and exp["kwh"] == 36.0
    assert out["totals"]["total_pence"] == imp["total_pence"]  # export not added to the bill
    assert out["totals"]["export_credit_pence"] == exp["total_pence"]
    assert out["totals"]["net_pence"] == imp["total_pence"] - exp["total_pence"]
    assert "gas" in out["fuels_unavailable"] and "electricity" not in out["fuels_unavailable"]


async def test_unit_rates_gives_uk_local_bands_with_intelligent_caveat() -> None:
    ctx = await _ctx()
    _seed_rates(ctx)
    out = await unit_rates(
        ctx=ctx, day=date(2026, 10, 3), now=datetime(2026, 10, 3, 12, tzinfo=UTC)
    )

    assert [(b["from_local"], b["to_local"], b["pence_inc_vat"]) for b in out["bands"]] == [
        ("00:00", "05:30", 7.0),
        ("05:30", "23:30", 28.0),
        ("23:30", "00:00", 7.0),
    ]
    assert any("Intelligent Octopus" in c for c in out["caveats"])

    exp = await unit_rates(ctx=ctx, day=date(2026, 10, 3), direction="export")
    assert [b["pence_inc_vat"] for b in exp["bands"]] == [12.0]
    assert not any("Intelligent Octopus" in c for c in exp["caveats"])


def test_migration_002_rebuilds_assignments_and_forces_account_refetch() -> None:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    version1 = dict(_list_migrations())[1]
    for statement in _split_sql(version1):
        conn.execute(statement)
    conn.execute("PRAGMA user_version = 1")
    conn.execute(
        "INSERT INTO tariff_assignments VALUES ('A-1','electricity','P','T','2025-01-01',NULL)"
    )
    conn.execute("INSERT INTO sync_state VALUES ('account:A-1','2026-10-03T00:00:00+00:00',604800)")
    conn.execute(
        "INSERT INTO sync_state VALUES ('rates:electricity:T','2026-10-03T00:00:00+00:00',86400)"
    )
    conn.commit()

    run_migrations(conn)

    cols = [r["name"] for r in conn.execute("PRAGMA table_info(tariff_assignments)")]
    assert "is_export" in cols
    assert conn.execute("SELECT COUNT(*) FROM tariff_assignments").fetchone()[0] == 0
    resources = [r[0] for r in conn.execute("SELECT resource FROM sync_state")]
    assert resources == ["rates:electricity:T"]  # only the account cache is invalidated


# --- Review findings (Claude code review of this branch) -------------------------------


async def test_separate_import_mpans_are_summed_not_merged() -> None:
    """Two import MPANs (e.g. an Economy 7 storage-heater MPAN) are separate supplies."""
    ctx = await _ctx()
    await ctx.ensure_account_bootstrapped()
    from octopus_mcp.cache.meters import MeterRow
    from octopus_mcp.tools.meter_select import consumption_for

    ctx.meters_repo.upsert_meters(
        [
            MeterRow(
                account_number="A-1", fuel="electricity", mpan_or_mprn="E7-MPAN", serial_number="E7"
            )
        ]
    )
    CONSUMPTION["E7"] = _rows(DAY1, [0.5, 0.5])
    try:
        rows = await consumption_for(
            ctx, fuel="electricity", period_from=DAY1, period_to=DAY1 + timedelta(hours=1), now=DAY2
        )
    finally:
        del CONSUMPTION["E7"]
    assert rows is not None
    assert [r.consumption_kwh for r in rows] == [1.5, 2.5]  # OLD1 (1, 2) + E7 (0.5, 0.5)


async def test_exchanged_out_meter_reporting_zeros_does_not_mask_the_live_one() -> None:
    ctx = await _ctx()
    await ctx.ensure_account_bootstrapped()
    from octopus_mcp.tools.meter_select import consumption_for

    saved = CONSUMPTION["OLD1"]
    # The old serial (stored first) keeps reporting 0.0 for the new meter's period.
    CONSUMPTION["OLD1"] = _rows(DAY2, [0.0, 0.0])
    try:
        rows = await consumption_for(
            ctx, fuel="electricity", period_from=DAY2, period_to=DAY2 + timedelta(hours=1), now=DAY2
        )
    finally:
        CONSUMPTION["OLD1"] = saved
    assert rows is not None
    assert [r.consumption_kwh for r in rows] == [3.0, 4.0]  # NEW1's readings, not zeros


async def test_flat_tariff_with_price_history_gets_no_time_of_use_note() -> None:
    ctx = await _ctx()
    _seed_rates(ctx)  # gives the export tariff one flat rate
    # Add a past price-cap change to export: two historical values, one rate all day.
    ctx.rates_repo.upsert_unit_rates(
        tariff_code=EXPORT_TARIFF,
        fuel="electricity",
        rows=[
            RateRow(
                valid_from=datetime(2024, 1, 1, tzinfo=UTC),
                valid_to=datetime(2025, 1, 1, tzinfo=UTC),
                value_inc_vat=15.0,
                value_exc_vat=15.0,
            )
        ],
    )
    out = await current_tariff(ctx=ctx, now=datetime(2026, 10, 3, 12, tzinfo=UTC))
    by_label = {f["label"]: f for f in out["fuels"]}
    assert "note" not in by_label["electricity_export"]
    assert "note" in by_label["electricity"]  # genuinely peak/off-peak


async def test_rates_cached_before_publication_are_refetched_once() -> None:
    """Day-ahead tariffs: the 24 h rate cache can predate the window being asked about."""
    ctx = await _ctx()
    calls: list[bool] = []

    async def _ensure_rates(**k: Any) -> None:
        calls.append(bool(k.get("force")))
        if k.get("force"):
            _seed_rates(ctx)  # what a fresh fetch would now return

    ctx.ensure_rates = AsyncMock(side_effect=_ensure_rates)
    out = await unit_rates(ctx=ctx, day=date(2026, 10, 3))
    assert calls[:2] == [False, True]
    assert len(out["bands"]) == 3


async def test_an_export_pricing_failure_does_not_fail_the_whole_bill() -> None:
    from octopus_mcp.octopus.errors import NotFoundError

    ctx = await _ctx()
    _seed_rates(ctx)

    async def _ensure_rates(**k: Any) -> None:
        if k["tariff_code"] == EXPORT_TARIFF:
            raise NotFoundError("no standing charges for export product", resource=EXPORT_TARIFF)

    ctx.ensure_rates = AsyncMock(side_effect=_ensure_rates)
    period = PeriodSpec(kind="explicit", from_date=date(2026, 9, 1), to_date=date(2026, 9, 3))
    out = await bill_summary(period=period, ctx=ctx, now=datetime(2026, 10, 3, tzinfo=UTC))
    assert [f["direction"] for f in out["fuels"]] == ["import"]
    assert out["caveats"] and "Export not priced" in out["caveats"][0]


async def test_list_meters_defaults_to_import_only() -> None:
    ctx = await _ctx()
    await ctx.ensure_account_bootstrapped()
    meters = ctx.meters_repo.list_meters_for_account("A-1", fuel="electricity")
    assert meters and not any(m.is_export for m in meters)
    both = ctx.meters_repo.list_meters_for_account("A-1", fuel="electricity", is_export=None)
    assert any(m.is_export for m in both)
