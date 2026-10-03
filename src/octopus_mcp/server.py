"""MCP server entrypoint."""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from typing import Any, cast

from mcp.server.fastmcp import FastMCP
from platformdirs import user_cache_dir
from pydantic import BaseModel, Field

from octopus_mcp.cache.consumption import ConsumptionRepo
from octopus_mcp.cache.db import open_db
from octopus_mcp.cache.meters import MetersRepo
from octopus_mcp.cache.products import ProductsRepo
from octopus_mcp.cache.rates import RatesRepo
from octopus_mcp.cache.sync import ConsumptionSyncer
from octopus_mcp.cache.sync_state import SyncStateRepo
from octopus_mcp.logging_setup import configure_logging
from octopus_mcp.octopus.auth import resolve_credentials
from octopus_mcp.octopus.errors import OctopusError, RateLimitError
from octopus_mcp.octopus.rest import OctopusRestClient
from octopus_mcp.tools.bill_summary import bill_summary as _bill_summary_impl
from octopus_mcp.tools.compare_tariff import FuelChoice
from octopus_mcp.tools.compare_tariff import compare_tariff as _compare_tariff_impl
from octopus_mcp.tools.context import ToolContext, build_helpers
from octopus_mcp.tools.current_tariff import Direction
from octopus_mcp.tools.current_tariff import current_tariff as _current_tariff_impl
from octopus_mcp.tools.current_tariff import unit_rates as _unit_rates_impl
from octopus_mcp.tools.peak_hours import peak_hours as _peak_hours_impl
from octopus_mcp.tools.period import PeriodSpec
from octopus_mcp.tools.thin import get_account as _get_account_impl
from octopus_mcp.tools.thin import get_consumption_raw as _get_consumption_raw_impl
from octopus_mcp.tools.thin import get_product as _get_product_impl
from octopus_mcp.tools.thin import list_products as _list_products_impl
from octopus_mcp.tools.usage_breakdown import GroupByLiteral
from octopus_mcp.tools.usage_breakdown import usage_breakdown as _usage_breakdown_impl


class PeriodArg(BaseModel):
    kind: str = Field(..., description="last_month | last_7_days | last_quarter | ytd | explicit")
    from_date: date | None = None
    to_date: date | None = None

    def to_spec(self) -> PeriodSpec:
        return PeriodSpec(kind=self.kind, from_date=self.from_date, to_date=self.to_date)  # type: ignore[arg-type]


def _cache_path() -> Path:
    base = Path(user_cache_dir("octopus-mcp"))
    base.mkdir(parents=True, exist_ok=True)
    return base / "cache.db"


def _build_app(*, test_mode: bool = False) -> tuple[FastMCP, ToolContext | None]:
    app = FastMCP("octopus-mcp")
    ctx: ToolContext | None = None

    if not test_mode:
        creds = resolve_credentials()
        conn = open_db(_cache_path())
        rest = OctopusRestClient(creds)
        consumption_repo = ConsumptionRepo(conn)
        rates_repo = RatesRepo(conn)
        meters_repo = MetersRepo(conn)
        products_repo = ProductsRepo(conn)
        sync_state = SyncStateRepo(conn)
        ctx = ToolContext(
            creds=creds,
            rest=rest,
            conn=conn,
            consumption_repo=consumption_repo,
            rates_repo=rates_repo,
            meters_repo=meters_repo,
            products_repo=products_repo,
            sync_state=sync_state,
            consumption_syncer=ConsumptionSyncer(
                rest=rest, repo=consumption_repo, state=sync_state
            ),
        )

        from octopus_mcp.cache.kraken_repo import KrakenRepo
        from octopus_mcp.octopus.kraken import KrakenClient

        ctx.kraken = KrakenClient(creds)
        ctx.kraken_repo = KrakenRepo(conn)
        build_helpers(ctx)

    async def _wrap_call(coro: Any) -> Any:
        if ctx is None:
            raise RuntimeError("Server initialised in test_mode without ToolContext")
        try:
            return await coro
        except OctopusError as e:
            return {
                "code": type(e).__name__,
                "message": str(e),
                "retryable": isinstance(e, RateLimitError),
            }

    async def bill_summary(period: PeriodArg) -> dict[str, Any]:
        assert ctx is not None
        return cast(
            dict[str, Any],
            await _wrap_call(_bill_summary_impl(period=period.to_spec(), ctx=ctx)),
        )

    async def current_tariff() -> dict[str, Any]:
        assert ctx is not None
        return cast(dict[str, Any], await _wrap_call(_current_tariff_impl(ctx=ctx)))

    async def unit_rates(
        day: date | None = None, direction: Direction = "import"
    ) -> dict[str, Any]:
        assert ctx is not None
        return cast(
            dict[str, Any],
            await _wrap_call(_unit_rates_impl(ctx=ctx, day=day, direction=direction)),
        )

    async def usage_breakdown(period: PeriodArg, group_by: str = "day") -> dict[str, Any]:
        assert ctx is not None
        return cast(
            dict[str, Any],
            await _wrap_call(
                _usage_breakdown_impl(
                    period=period.to_spec(),
                    group_by=cast(GroupByLiteral, group_by),
                    ctx=ctx,
                )
            ),
        )

    async def peak_hours(period: PeriodArg, top_n: int = 10) -> dict[str, Any]:
        assert ctx is not None
        return cast(
            dict[str, Any],
            await _wrap_call(_peak_hours_impl(period=period.to_spec(), top_n=top_n, ctx=ctx)),
        )

    async def compare_tariff(
        target_product_code: str, period: PeriodArg, fuel: str = "both"
    ) -> dict[str, Any]:
        assert ctx is not None
        return cast(
            dict[str, Any],
            await _wrap_call(
                _compare_tariff_impl(
                    target_product_code=target_product_code,
                    period=period.to_spec(),
                    fuel=cast(FuelChoice, fuel),
                    ctx=ctx,
                )
            ),
        )

    async def get_account() -> dict[str, Any]:
        assert ctx is not None
        return cast(dict[str, Any], await _wrap_call(_get_account_impl(rest=ctx.rest)))

    async def list_products() -> list[dict[str, Any]]:
        assert ctx is not None
        return cast(list[dict[str, Any]], await _wrap_call(_list_products_impl(rest=ctx.rest)))

    async def get_product(code: str) -> dict[str, Any]:
        assert ctx is not None
        return cast(dict[str, Any], await _wrap_call(_get_product_impl(code, rest=ctx.rest)))

    async def get_consumption_raw(
        fuel: str,
        mpan_or_mprn: str,
        serial_number: str,
        period_from: str,
        period_to: str,
    ) -> list[dict[str, Any]]:
        assert ctx is not None
        return cast(
            list[dict[str, Any]],
            await _wrap_call(
                _get_consumption_raw_impl(
                    fuel=fuel,
                    mpan_or_mprn=mpan_or_mprn,
                    serial_number=serial_number,
                    period_from=datetime.fromisoformat(period_from),
                    period_to=datetime.fromisoformat(period_to),
                    rest=ctx.rest,
                )
            ),
        )

    app.add_tool(
        bill_summary, name="bill_summary", description="Total cost (£/pence) per fuel for a period"
    )
    app.add_tool(
        current_tariff,
        name="current_tariff",
        description="Tariff in force now for electricity import, export (if any) and gas",
    )
    app.add_tool(
        unit_rates,
        name="unit_rates",
        description="Electricity unit-rate bands for one UK day (e.g. peak/off-peak); "
        "day=YYYY-MM-DD, default today; direction=import|export",
    )
    app.add_tool(
        usage_breakdown, name="usage_breakdown", description="Aggregated kWh by hour/day/week/month"
    )
    app.add_tool(peak_hours, name="peak_hours", description="Top-N highest-usage half-hours")
    app.add_tool(
        compare_tariff,
        name="compare_tariff",
        description="Replay actual usage against another Octopus tariff",
    )
    app.add_tool(
        get_account, name="get_account", description="Account details + meters + tariff history"
    )
    app.add_tool(
        list_products, name="list_products", description="Browse Octopus product catalogue"
    )
    app.add_tool(get_product, name="get_product", description="Detail for one product code")
    app.add_tool(
        get_consumption_raw,
        name="get_consumption_raw",
        description="Half-hourly consumption rows for a meter",
    )

    from octopus_mcp.tools.kraken_passthrough import kraken_query as kraken_query_tool
    from octopus_mcp.tools.saving_sessions import (
        saving_session_history as saving_session_history_tool,
    )

    async def saving_session_history_handler() -> dict[str, Any]:
        assert ctx is not None
        return cast(dict[str, Any], await _wrap_call(saving_session_history_tool(ctx=ctx)))

    async def kraken_query_handler(
        query: str, variables: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        assert ctx is not None
        return cast(dict[str, Any], await _wrap_call(kraken_query_tool(query, variables, ctx=ctx)))

    app.add_tool(
        saving_session_history_handler,
        name="saving_session_history",
        description="Octoplus saving sessions joined and points/kWh earned",
    )
    app.add_tool(
        kraken_query_handler,
        name="kraken_query",
        description="Escape hatch: run an arbitrary Kraken GraphQL query",
    )

    return app, ctx


def run() -> int:
    configure_logging()
    app, _ = _build_app()
    app.run()
    return 0
