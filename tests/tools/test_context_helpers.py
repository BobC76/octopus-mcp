from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

from octopus_mcp.cache.db import open_db
from octopus_mcp.cache.meters import MetersRepo
from octopus_mcp.cache.rates import RatesRepo
from octopus_mcp.cache.sync_state import SyncStateRepo
from octopus_mcp.tools.context import build_helpers


async def test_ensure_rates_calls_rest_then_caches() -> None:
    conn = open_db(":memory:")
    rates_repo = RatesRepo(conn)
    rest = AsyncMock()

    class _Rate:
        def __init__(self, vf: datetime, vi: float) -> None:
            self.valid_from = vf
            self.valid_to: datetime | None = None
            self.value_inc_vat = vi
            self.value_exc_vat = vi - 1.0

    rest.get_electricity_unit_rates.return_value = [_Rate(datetime(2026, 4, 1, tzinfo=UTC), 25.0)]
    rest.get_electricity_standing_charges.return_value = [
        _Rate(datetime(2026, 4, 1, tzinfo=UTC), 50.0)
    ]

    ctx = MagicMock()
    ctx.rest = rest
    ctx.rates_repo = rates_repo
    ctx.sync_state = MagicMock()
    ctx.sync_state.is_fresh.return_value = False

    helpers = build_helpers(ctx)

    await helpers.ensure_rates(
        product_code="VAR-22-11-01", tariff_code="E-1R-VAR-22-11-01-A", fuel="electricity"
    )
    rows = rates_repo.get_unit_rates("E-1R-VAR-22-11-01-A")
    assert rows and rows[0].value_inc_vat == 25.0


async def test_bootstrap_populates_meters_and_assignments_from_account() -> None:
    """First call: fetch account, upsert meters+assignments, mark fresh."""
    from octopus_mcp.octopus.models import (
        Account,
        Agreement,
        ElectricityMeterPoint,
        GasMeterPoint,
        Meter,
        Property,
    )

    conn = open_db(":memory:")
    meters_repo = MetersRepo(conn)
    sync_state = SyncStateRepo(conn)
    rest = AsyncMock()
    rest.get_account.return_value = Account(
        number="A-1",
        properties=[
            Property(
                id=1,
                electricity_meter_points=[
                    ElectricityMeterPoint(
                        mpan="111",
                        meters=[Meter(serial_number="ELEC1")],
                        agreements=[
                            Agreement(
                                tariff_code="E-1R-VAR-22-11-01-A",
                                valid_from=datetime(2024, 1, 1, tzinfo=UTC),
                                valid_to=None,
                            )
                        ],
                    )
                ],
                gas_meter_points=[
                    GasMeterPoint(
                        mprn="999",
                        meters=[Meter(serial_number="GAS1")],
                        agreements=[
                            Agreement(
                                tariff_code="G-1R-VAR-22-11-01-A",
                                valid_from=datetime(2024, 1, 1, tzinfo=UTC),
                                valid_to=None,
                            )
                        ],
                    )
                ],
            )
        ],
    )

    ctx = MagicMock()
    ctx.rest = rest
    ctx.creds.account_number = "A-1"
    ctx.meters_repo = meters_repo
    ctx.sync_state = sync_state

    helpers = build_helpers(ctx)
    await helpers.ensure_account_bootstrapped()

    elec = meters_repo.list_meters_for_account("A-1", fuel="electricity")
    gas = meters_repo.list_meters_for_account("A-1", fuel="gas")
    assert len(elec) == 1 and elec[0].mpan_or_mprn == "111"
    assert len(gas) == 1 and gas[0].mpan_or_mprn == "999"

    current = meters_repo.current_tariff_assignment(
        account_number="A-1", fuel="electricity", at=datetime(2026, 4, 25, tzinfo=UTC)
    )
    assert current is not None
    assert current.product_code == "VAR-22-11-01"
    assert current.tariff_code == "E-1R-VAR-22-11-01-A"

    # Second call should not re-fetch (TTL fresh)
    await helpers.ensure_account_bootstrapped()
    assert rest.get_account.await_count == 1


async def test_product_code_derivation() -> None:
    from octopus_mcp.tools.context import _product_from_tariff

    assert _product_from_tariff("E-1R-VAR-22-11-01-A") == "VAR-22-11-01"
    assert _product_from_tariff("G-1R-VAR-22-11-01-A") == "VAR-22-11-01"
    assert _product_from_tariff("E-1R-AGILE-FLEX-22-11-25-A") == "AGILE-FLEX-22-11-25"
    assert _product_from_tariff("E-1R-GO-VAR-22-10-14-A") == "GO-VAR-22-10-14"
    # Fallback for non-matching shapes
    assert _product_from_tariff("WEIRD-FORMAT") == "WEIRD-FORMAT"


async def test_ensure_rates_keeps_direct_debit_when_both_payment_methods_are_published() -> None:
    """The API returns DIRECT_DEBIT and NON_DIRECT_DEBIT rows for the same window; the
    cache key let the last one written win, so gas could be ~5% high."""
    conn = open_db(":memory:")
    rates_repo = RatesRepo(conn)
    rest = AsyncMock()

    class _Rate:
        def __init__(self, vi: float, pm: str) -> None:
            self.valid_from = datetime(2026, 9, 30, 23, tzinfo=UTC)
            self.valid_to: datetime | None = None
            self.value_inc_vat = vi
            self.value_exc_vat = vi
            self.payment_method = pm

    both = [_Rate(8.0, "DIRECT_DEBIT"), _Rate(8.4, "NON_DIRECT_DEBIT")]
    rest.get_gas_unit_rates.return_value = both
    rest.get_gas_standing_charges.return_value = both

    ctx = MagicMock()
    ctx.rest = rest
    ctx.rates_repo = rates_repo
    ctx.sync_state = MagicMock()
    ctx.sync_state.is_fresh.return_value = False
    helpers = build_helpers(ctx)

    await helpers.ensure_rates(
        product_code="VAR-22-11-01", tariff_code="G-1R-VAR-22-11-01-A", fuel="gas"
    )
    assert [r.value_inc_vat for r in rates_repo.get_unit_rates("G-1R-VAR-22-11-01-A")] == [8.0]
