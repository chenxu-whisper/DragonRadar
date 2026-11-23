"""Encode exact source numbers without FastAPI's float conversion."""

import math
from collections.abc import Mapping
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from types import MappingProxyType
from typing import Any
from uuid import UUID

import simplejson
from starlette.responses import Response

from dragon_radar.http.errors import DataUnavailable

FINANCIAL_DECIMAL_FIELDS = frozenset(
    {
        "amount",
        "amount_change_ratio",
        "baseline_mean",
        "benchmark_return_ratio",
        "change_ratio",
        "close",
        "close_over_ma20",
        "close_over_ma60",
        "drawdown",
        "excess_return_ratio",
        "high",
        "last",
        "low",
        "max_drawdown_ratio",
        "median_return",
        "net_inflow",
        "normalized",
        "open",
        "previous_close",
        "return_ratio",
        "seal_amount",
        "today_total",
        "value",
        "volume",
        "volatility_20",
        "yesterday_limit_up_median_return",
    }
)

_SCALAR_TYPES = frozenset({str, int, float, bool, type(None), Decimal})


def restore_decimal_fields(value: Any, key: str | None = None) -> Any:
    value_type = type(value)
    if value_type is list or value_type is tuple:
        return [restore_decimal_fields(item, key) for item in value]
    # Frozen archive trees contain mappingproxy/tuple and ordinary JSON scalars.
    # Avoid Mapping's ABC dispatch at every scalar, while preserving the generic
    # Mapping-first behavior for custom containers and subclasses.
    if value_type not in _SCALAR_TYPES:
        if (
            value_type is dict
            or value_type is MappingProxyType
            or isinstance(value, Mapping)
        ):
            return {
                item_key: restore_decimal_fields(item, item_key)
                for item_key, item in value.items()
            }
        if isinstance(value, (list, tuple)):
            return [restore_decimal_fields(item, key) for item in value]
    if key in FINANCIAL_DECIMAL_FIELDS and isinstance(value, str):
        try:
            restored = Decimal(value)
        except InvalidOperation:
            raise DataUnavailable from None
        if not restored.is_finite():
            raise DataUnavailable
        return restored
    return value


def _validate_numbers(value: object) -> None:
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise DataUnavailable
    elif isinstance(value, float):
        if not math.isfinite(value):
            raise DataUnavailable
    elif isinstance(value, dict):
        for item in value.values():
            _validate_numbers(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            _validate_numbers(item)


def _default(value: object) -> str:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, UUID):
        return str(value)
    raise TypeError("Unsupported response value")


class DecimalJSONResponse(Response):
    media_type = "application/json"

    def render(self, content: object) -> bytes:
        _validate_numbers(content)
        return simplejson.dumps(
            content,
            use_decimal=True,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            default=_default,
        ).encode("utf-8")
