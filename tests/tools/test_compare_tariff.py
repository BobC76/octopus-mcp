from datetime import UTC, date, datetime
from unittest.mock import AsyncMock, MagicMock

from octopus_mcp.cache.meters import MeterRow, TariffAssignmentRow
from octopus_mcp.cache.rates import RateRow
from octopus_mcp.tools.compare_tariff import compare_tariff
from octopus_mcp.tools.period import PeriodSpec


class _R:
    def __init__(self, iso: str, kwh: float) -> None:
        self.interval_start = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        self.interval_end = self.interval_start
        self.consumption_kwh = kwh


async def test_compare_tariff_returns_delta_and_caveats() -> None:
    ctx = MagicMock()
    ctx.creds.account_number = "A-1"
    ctx.meters_repo.list_meters_for_account.side_effect = lambda acct, fuel, is_export=None: (
        [MeterRow(account_number="A-1", fuel=fuel, mpan_or_mprn="m", serial_number="s")]
        if fuel == "electricity" and not is_export
        else []
    )
    ctx.meters_repo.current_tariff_assignment.return_value = TariffAssignmentRow(
        account_number="A-1",
        fuel="electricity",
        product_code="VAR-22-11-01",
        tariff_code="E-1R-CUR",
        valid_from=datetime(2024, 1, 1, tzinfo=UTC),
        valid_to=None,
    )
    ctx.consumption_syncer.ensure = AsyncMock(
        return_value=[
            _R("2026-04-25T00:00:00Z", 1.0),
            _R("2026-04-25T12:00:00Z", 1.0),
        ]
    )

    def _rates(tariff: str) -> list[RateRow]:
        v = 30.0 if tariff == "E-1R-CUR" else 20.0
        return [
            RateRow(
                valid_from=datetime(2024, 1, 1, tzinfo=UTC),
                valid_to=None,
                value_inc_vat=v,
                value_exc_vat=v,
            )
        ]

    def _sc(tariff: str) -> list[RateRow]:
        return [
            RateRow(
                valid_from=datetime(2024, 1, 1, tzinfo=UTC),
                valid_to=None,
                value_inc_vat=50.0,
                value_exc_vat=47.0,
            )
        ]

    ctx.rates_repo.get_unit_rates.side_effect = _rates
    ctx.rates_repo.get_standing_charges.side_effect = _sc

    async def _ensure_rates(**_: object) -> None:
        return None

    async def _bootstrap() -> None:
        return None

    ctx.ensure_rates = _ensure_rates
    ctx.ensure_account_bootstrapped = _bootstrap

    async def _resolve_target(product_code: str, fuel: str) -> str:
        return "E-1R-TGT"

    ctx.resolve_target_tariff_code = _resolve_target

    out = await compare_tariff(
        target_product_code="VAR-TARGET",
        period=PeriodSpec(kind="explicit", from_date=date(2026, 4, 25), to_date=date(2026, 4, 26)),
        fuel="both",
        ctx=ctx,
        now=datetime(2026, 4, 27, tzinfo=UTC),
    )
    fc = out["fuels"][0]
    assert fc["fuel"] == "electricity"
    # 2 kWh * 30p = 60p + 50p = 110p current
    # 2 kWh * 20p = 40p + 50p = 90p target
    assert fc["delta_pence"] == -20
    assert any("Saving Sessions" in c for c in out["caveats"])
