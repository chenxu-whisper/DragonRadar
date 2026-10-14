"""LHB (龙虎榜) disclosure contracts and null-safe seat aggregation."""

from dragon_radar.lhb.models import (
    AggregatedSeat,
    AggregationCoverage,
    DisclosurePage,
    DisclosureRevisionInput,
    DisclosureSeatInput,
    DisclosureSelection,
    LHBImportBatch,
    LHBImportResult,
    PermissionSnapshot,
    SeatAggregation,
    SeatContribution,
    SeatHistory,
    SeatHistorySelection,
    SeatIdentity,
    SelectedDisclosure,
)
from dragon_radar.lhb.service import aggregate_seats, validate_import_batch

__all__ = [
    "AggregatedSeat",
    "AggregationCoverage",
    "DisclosurePage",
    "DisclosureRevisionInput",
    "DisclosureSeatInput",
    "DisclosureSelection",
    "LHBImportBatch",
    "LHBImportResult",
    "PermissionSnapshot",
    "SeatAggregation",
    "SeatContribution",
    "SeatHistory",
    "SeatHistorySelection",
    "SeatIdentity",
    "SelectedDisclosure",
    "aggregate_seats",
    "validate_import_batch",
]
