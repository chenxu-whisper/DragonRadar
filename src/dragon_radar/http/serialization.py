"""Encode exact source numbers without FastAPI's float conversion."""

import math
from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

import simplejson
from starlette.responses import Response

from dragon_radar.http.errors import DataUnavailable


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
