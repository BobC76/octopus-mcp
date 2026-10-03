"""Shared per-server context handed to each tool."""

from __future__ import annotations

import re
import sqlite3
from collections.abc import Callable, Coroutine
from dataclasses import dataclass
from datetime import UTC
from typing import Any

from octopus_mcp.cache.consumption import ConsumptionRepo
from octopus_mcp.cache.meters import MetersRepo
from octopus_mcp.cache.products import ProductsRepo
from octopus_mcp.cache.rates import RatesRepo
from octopus_mcp.cache.sync import ConsumptionSyncer
from octopus_mcp.cache.sync_state import SyncStateRepo
from octopus_mcp.octopus.auth import OctopusCredentials
from octopus_mcp.octopus.rest import OctopusRestClient

_TARIFF_RE = re.compile(r"^[EG]-(?:1R|2R)-(.+)-[A-Z]$")


def _product_from_tariff(tariff_code: str) -> str:
    """Derive product code from a tariff code.

    E.g. ``E-1R-VAR-22-11-01-A`` → ``VAR-22-11-01``.
    Falls back to returning the tariff code itself when the format is unrecognised.
    """
    m = _TARIFF_RE.match(tariff_code)
    if m:
        return m.group(1)
    return tariff_code


@dataclass
class ToolContext:
    creds: OctopusCredentials
    rest: OctopusRestClient
    conn: sqlite3.Connection
    consumption_repo: ConsumptionRepo
    rates_repo: RatesRepo
    meters_repo: MetersRepo
    products_repo: ProductsRepo
    sync_state: SyncStateRepo
    consumption_syncer: ConsumptionSyncer
    kraken: Any = None
    kraken_repo: Any = None


@dataclass
class _Helpers:
    ensure_rates: Callable[..., Coroutine[Any, Any, None]]
    resolve_target_tariff_code: Callable[..., Coroutine[Any, Any, str]]
    ensure_account_bootstrapped: Callable[..., Coroutine[Any, Any, None]]
    ensure_kraken_synced: Callable[..., Coroutine[Any, Any, None]]


def build_helpers(ctx: ToolContext) -> _Helpers:
    """Attach `ensure_rates` and `resolve_target_tariff_code` async closures to the context."""
    from datetime import datetime

    from octopus_mcp.cache.rates import RateRow
    from octopus_mcp.octopus.errors import NotFoundError

    async def ensure_rates(
        *, product_code: str, tariff_code: str, fuel: str, force: bool = False
    ) -> None:
        """Fetch and cache a tariff's rates (24 h TTL). ``force`` refetches regardless: day-
        ahead tariffs such as Agile publish tomorrow's prices mid-afternoon, inside the TTL."""
        key = f"rates:{fuel}:{tariff_code}"
        now = datetime.now(UTC)
        if not force and ctx.sync_state.is_fresh(key, now=now):
            return
        if fuel == "electricity":
            unit_rows = await ctx.rest.get_electricity_unit_rates(product_code, tariff_code)
            sc_rows = await ctx.rest.get_electricity_standing_charges(product_code, tariff_code)
        else:
            unit_rows = await ctx.rest.get_gas_unit_rates(product_code, tariff_code)
            sc_rows = await ctx.rest.get_gas_standing_charges(product_code, tariff_code)
        ctx.rates_repo.upsert_unit_rates(
            tariff_code=tariff_code,
            fuel=fuel,
            rows=[
                RateRow(
                    valid_from=r.valid_from,
                    valid_to=r.valid_to,
                    value_inc_vat=r.value_inc_vat,
                    value_exc_vat=r.value_exc_vat,
                )
                for r in unit_rows
            ],
        )
        ctx.rates_repo.upsert_standing_charges(
            tariff_code=tariff_code,
            fuel=fuel,
            rows=[
                RateRow(
                    valid_from=r.valid_from,
                    valid_to=r.valid_to,
                    value_inc_vat=r.value_inc_vat,
                    value_exc_vat=r.value_exc_vat,
                )
                for r in sc_rows
            ],
        )
        ctx.sync_state.touch(key, at=now, ttl_seconds=86400)

    async def resolve_target_tariff_code(product_code: str, fuel: str) -> str:
        product = await ctx.rest.get_product(product_code)
        section_key = (
            "single_register_electricity_tariffs"
            if fuel == "electricity"
            else "single_register_gas_tariffs"
        )
        section = product.get(section_key, {})
        if not section:
            raise NotFoundError(
                f"Product {product_code} has no {fuel} tariffs",
                resource=product_code,
            )
        first_region = next(iter(section.values()))
        if "direct_debit_monthly" in first_region:
            return first_region["direct_debit_monthly"]["code"]  # type: ignore[no-any-return]
        if "direct_debit_quarterly" in first_region:
            return first_region["direct_debit_quarterly"]["code"]  # type: ignore[no-any-return]
        for variant in first_region.values():
            if "code" in variant:
                return variant["code"]  # type: ignore[no-any-return]
        raise NotFoundError(
            f"No tariff code found in product {product_code}", resource=product_code
        )

    async def ensure_account_bootstrapped() -> None:
        from octopus_mcp.cache.meters import MeterRow, TariffAssignmentRow

        account_number = ctx.creds.account_number
        now = datetime.now(UTC)
        key = f"account:{account_number}"
        if ctx.sync_state.is_fresh(key, now=now):
            return
        account = await ctx.rest.get_account()
        meter_rows: list[MeterRow] = []
        assignment_rows: list[TariffAssignmentRow] = []
        for prop in account.properties:
            for emp in prop.electricity_meter_points:
                for meter in emp.meters:
                    meter_rows.append(
                        MeterRow(
                            account_number=account_number,
                            fuel="electricity",
                            mpan_or_mprn=emp.mpan,
                            serial_number=meter.serial_number,
                            is_export=emp.is_export,
                        )
                    )
                for agreement in emp.agreements:
                    assignment_rows.append(
                        TariffAssignmentRow(
                            account_number=account_number,
                            fuel="electricity",
                            product_code=_product_from_tariff(agreement.tariff_code),
                            tariff_code=agreement.tariff_code,
                            valid_from=agreement.valid_from,
                            valid_to=agreement.valid_to,
                            is_export=emp.is_export,
                        )
                    )
            for gmp in prop.gas_meter_points:
                for meter in gmp.meters:
                    meter_rows.append(
                        MeterRow(
                            account_number=account_number,
                            fuel="gas",
                            mpan_or_mprn=gmp.mprn,
                            serial_number=meter.serial_number,
                        )
                    )
                for agreement in gmp.agreements:
                    assignment_rows.append(
                        TariffAssignmentRow(
                            account_number=account_number,
                            fuel="gas",
                            product_code=_product_from_tariff(agreement.tariff_code),
                            tariff_code=agreement.tariff_code,
                            valid_from=agreement.valid_from,
                            valid_to=agreement.valid_to,
                        )
                    )
        ctx.meters_repo.upsert_meters(meter_rows)
        ctx.meters_repo.upsert_tariff_assignments(assignment_rows)
        ctx.sync_state.touch(key, at=now, ttl_seconds=604800)

    async def ensure_kraken_synced() -> None:
        from datetime import datetime

        from octopus_mcp.octopus.kraken_queries import (
            fetch_octoplus_events,
            fetch_saving_sessions,
        )

        if ctx.kraken is None or ctx.kraken_repo is None:
            return  # Kraken not configured; tool will return empty
        key = f"kraken:{ctx.creds.account_number}"
        now = datetime.now(UTC)
        if ctx.sync_state.is_fresh(key, now=now):
            return
        sessions = await fetch_saving_sessions(
            kraken=ctx.kraken, account_number=ctx.creds.account_number
        )
        events = await fetch_octoplus_events(
            kraken=ctx.kraken, account_number=ctx.creds.account_number
        )
        ctx.kraken_repo.upsert_saving_sessions(sessions)
        ctx.kraken_repo.upsert_octoplus_events(events)
        ctx.sync_state.touch(key, at=now, ttl_seconds=3600)

    ctx.ensure_rates = ensure_rates  # type: ignore[attr-defined]
    ctx.resolve_target_tariff_code = resolve_target_tariff_code  # type: ignore[attr-defined]
    ctx.ensure_account_bootstrapped = ensure_account_bootstrapped  # type: ignore[attr-defined]
    ctx.ensure_kraken_synced = ensure_kraken_synced  # type: ignore[attr-defined]
    return _Helpers(
        ensure_rates=ensure_rates,
        resolve_target_tariff_code=resolve_target_tariff_code,
        ensure_account_bootstrapped=ensure_account_bootstrapped,
        ensure_kraken_synced=ensure_kraken_synced,
    )
