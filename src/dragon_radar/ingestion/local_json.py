"""Filesystem adapter for operator-supplied licensed market JSON."""

from pathlib import Path

from pydantic import ValidationError

from dragon_radar.ingestion.contracts import LicensedMarketBatch


def load_licensed_market_json(path: Path) -> LicensedMarketBatch:
    try:
        raw = path.read_text(encoding="utf-8")
        return LicensedMarketBatch.model_validate_json(raw)
    except (OSError, UnicodeError, ValidationError):
        raise ValueError("Invalid licensed market JSON") from None
