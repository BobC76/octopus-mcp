"""Pydantic models for Octopus REST API responses."""

from __future__ import annotations

from datetime import datetime
from typing import Generic, TypeVar

from pydantic import BaseModel, ConfigDict, Field

_BASE_CONFIG = ConfigDict(extra="ignore", populate_by_name=True)

T = TypeVar("T")


class _Base(BaseModel):
    model_config = _BASE_CONFIG


class Page(_Base, Generic[T]):
    count: int
    next: str | None = None
    previous: str | None = None
    results: list[T]


class Agreement(_Base):
    tariff_code: str
    valid_from: datetime
    valid_to: datetime | None = None


class MeterRegister(_Base):
    identifier: str | None = None


class Meter(_Base):
    serial_number: str
    registers: list[MeterRegister] = Field(default_factory=list)


class ElectricityMeterPoint(_Base):
    mpan: str
    profile_class: int | None = None
    consumption_standard: int | None = None
    is_export: bool = False
    meters: list[Meter] = Field(default_factory=list)
    agreements: list[Agreement] = Field(default_factory=list)


class GasMeterPoint(_Base):
    mprn: str
    consumption_standard: int | None = None
    meters: list[Meter] = Field(default_factory=list)
    agreements: list[Agreement] = Field(default_factory=list)


class Property(_Base):
    id: int
    moved_in_at: datetime | None = None
    moved_out_at: datetime | None = None
    address_line_1: str | None = None
    postcode: str | None = None
    electricity_meter_points: list[ElectricityMeterPoint] = Field(default_factory=list)
    gas_meter_points: list[GasMeterPoint] = Field(default_factory=list)


class Account(_Base):
    number: str
    properties: list[Property] = Field(default_factory=list)


class ConsumptionRow(_Base):
    consumption: float
    interval_start: datetime
    interval_end: datetime


class ConsumptionPage(Page[ConsumptionRow]):
    pass


class Product(_Base):
    code: str
    display_name: str
    full_name: str | None = None
    description: str | None = None
    is_variable: bool = False
    is_green: bool = False
    is_tracker: bool = False
    is_prepay: bool = False
    is_business: bool = False
    brand: str | None = None
    available_from: datetime | None = None
    available_to: datetime | None = None


class ProductPage(Page[Product]):
    pass


class UnitRate(_Base):
    value_exc_vat: float
    value_inc_vat: float
    valid_from: datetime
    valid_to: datetime | None = None
    payment_method: str | None = None  # DIRECT_DEBIT / NON_DIRECT_DEBIT / None


class UnitRatePage(Page[UnitRate]):
    pass


class StandingCharge(_Base):
    value_exc_vat: float
    value_inc_vat: float
    valid_from: datetime
    valid_to: datetime | None = None
    payment_method: str | None = None


class StandingChargePage(Page[StandingCharge]):
    pass
