from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

from octopus_mcp.cache.meters import MeterRow, TariffAssignmentRow
from octopus_mcp.cache.rates import RateRow
from octopus_mcp.tools.bill_summary import bill_summary
from octopus_mcp.tools.period import PeriodSpec


async def test_bill_summary_basic_electricity_only() -> None:
    ctx = MagicMock()
    ctx.creds.account_number = "A-1"
    ctx.meters_repo.list_meters_for_account.side_effect = lambda acct, fuel, is_export=None: (
        [MeterRow(account_number="A-1", fuel="electricity", mpan_or_mprn="123", serial_number="S")]
        if fuel == "electricity" and not is_export
        else []
    )
    ctx.meters_repo.current_tariff_assignment.return_value = TariffAssignmentRow(
        account_number="A-1",
        fuel="electricity",
        product_code="VAR",
        tariff_code="E-1R-VAR",
        valid_from=datetime(2024, 1, 1, tzinfo=UTC),
        valid_to=None,
    )

    consumer_syncer = AsyncMock()
    ctx.consumption_syncer = consumer_syncer

    class _R:
        def __init__(self, iso: str, kwh: float) -> None:
            self.interval_start = datetime.fromisoformat(iso.replace("Z", "+00:00"))
            self.interval_end = self.interval_start
            self.consumption_kwh = kwh

    consumer_syncer.ensure.return_value = [
        _R("2026-03-01T00:00:00Z", 1.0),
        _R("2026-03-15T00:00:00Z", 1.0),
    ]
    ctx.rates_repo.get_unit_rates.return_value = [
        RateRow(
            valid_from=datetime(2024, 1, 1, tzinfo=UTC),
            valid_to=None,
            value_inc_vat=25.0,
            value_exc_vat=23.0,
        )
    ]
    ctx.rates_repo.get_standing_charges.return_value = [
        RateRow(
            valid_from=datetime(2024, 1, 1, tzinfo=UTC),
            valid_to=None,
            value_inc_vat=50.0,
            value_exc_vat=47.0,
        )
    ]

    async def _ensure_rates(**_: object) -> None:
        return None

    async def _bootstrap() -> None:
        return None

    ctx.ensure_rates = _ensure_rates
    ctx.ensure_account_bootstrapped = _bootstrap

    out = await bill_summary(
        period=PeriodSpec(kind="last_month"), ctx=ctx, now=datetime(2026, 4, 1, tzinfo=UTC)
    )
    assert out["fuels"][0]["fuel"] == "electricity"
    # 2 kWh * 25p = 50p; 31 days * 50p = 1550p. Total 1600p.
    assert out["fuels"][0]["unit_pence"] == 50
    assert out["fuels"][0]["standing_pence"] == 1550
    assert out["totals"]["total_pence"] == 1600


async def test_bill_summary_no_gas_meter_lists_unavailable() -> None:
    ctx = MagicMock()
    ctx.creds.account_number = "A-1"
    ctx.meters_repo.list_meters_for_account.side_effect = lambda acct, fuel, is_export=None: (
        [MeterRow(account_number="A-1", fuel="electricity", mpan_or_mprn="123", serial_number="S")]
        if fuel == "electricity" and not is_export
        else []
    )
    ctx.meters_repo.current_tariff_assignment.return_value = TariffAssignmentRow(
        account_number="A-1",
        fuel="electricity",
        product_code="VAR",
        tariff_code="E-1R",
        valid_from=datetime(2024, 1, 1, tzinfo=UTC),
        valid_to=None,
    )
    ctx.rates_repo.get_unit_rates.return_value = []
    ctx.rates_repo.get_standing_charges.return_value = []
    ctx.consumption_syncer.ensure = AsyncMock(return_value=[])

    async def _ensure_rates(**_: object) -> None:
        return None

    async def _bootstrap() -> None:
        return None

    ctx.ensure_rates = _ensure_rates
    ctx.ensure_account_bootstrapped = _bootstrap

    out = await bill_summary(
        period=PeriodSpec(kind="last_month"), ctx=ctx, now=datetime(2026, 4, 1, tzinfo=UTC)
    )
    assert "gas" in out["fuels_unavailable"]
