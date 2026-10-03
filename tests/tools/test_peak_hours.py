from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

from octopus_mcp.cache.meters import MeterRow
from octopus_mcp.tools.peak_hours import peak_hours
from octopus_mcp.tools.period import PeriodSpec


class _R:
    def __init__(self, iso: str, kwh: float) -> None:
        self.interval_start = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        self.interval_end = self.interval_start
        self.consumption_kwh = kwh


async def test_peak_hours_returns_top_n_per_fuel() -> None:
    ctx = MagicMock()
    ctx.creds.account_number = "A-1"
    ctx.meters_repo.list_meters_for_account.side_effect = lambda acct, fuel, is_export=None: (
        [MeterRow(account_number="A-1", fuel=fuel, mpan_or_mprn="m", serial_number="s")]
        if fuel == "electricity" and not is_export
        else []
    )
    ctx.consumption_syncer.ensure = AsyncMock(
        return_value=[
            _R("2026-04-25T08:00:00Z", 1.0),
            _R("2026-04-25T18:00:00Z", 3.5),
            _R("2026-04-25T19:00:00Z", 2.7),
        ]
    )

    async def _bootstrap() -> None:
        return None

    ctx.ensure_account_bootstrapped = _bootstrap

    out = await peak_hours(
        period=PeriodSpec(kind="last_7_days"),
        top_n=2,
        ctx=ctx,
        now=datetime(2026, 4, 27, tzinfo=UTC),
    )
    elec = out["fuels"]["electricity"]
    assert len(elec) == 2
    assert elec[0]["consumption_kwh"] == 3.5
