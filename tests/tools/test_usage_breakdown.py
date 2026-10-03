from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

from octopus_mcp.cache.meters import MeterRow
from octopus_mcp.tools.period import PeriodSpec
from octopus_mcp.tools.usage_breakdown import usage_breakdown


class _R:
    def __init__(self, iso: str, kwh: float) -> None:
        self.interval_start = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        self.interval_end = self.interval_start
        self.consumption_kwh = kwh


async def test_usage_breakdown_groups_by_day() -> None:
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
            _R("2026-04-25T20:00:00Z", 2.0),
            _R("2026-04-26T08:00:00Z", 0.5),
        ]
    )

    async def _bootstrap() -> None:
        return None

    ctx.ensure_account_bootstrapped = _bootstrap

    out = await usage_breakdown(
        period=PeriodSpec(kind="last_7_days"),
        group_by="day",
        ctx=ctx,
        now=datetime(2026, 4, 27, tzinfo=UTC),
    )
    elec = out["fuel_breakdowns"]["electricity"]
    assert any(b["label"] == "2026-04-25" and b["kwh"] == 3.0 for b in elec)
    assert out["stats"]["electricity"]["mean"] > 0
