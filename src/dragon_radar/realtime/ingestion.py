"""Typed orchestration boundary for internal realtime ingestion."""

from collections.abc import Sequence

from dragon_radar.realtime.models import (
    AnomalyRule,
    MinuteBatchInput,
    QuoteBatchInput,
    RealtimeImportResult,
    SectorFlowBatchInput,
)
from dragon_radar.realtime.repository import RealtimeRepository
from dragon_radar.storage.repository import Conflict


class RealtimeIngestionConflict(Exception):
    """A validated realtime command conflicts with immutable stored evidence."""


class RealtimeIngestion:
    """Validate complete domain models before invoking transactional storage."""

    def __init__(self, repository: RealtimeRepository):
        if not isinstance(repository, RealtimeRepository):
            raise TypeError("repository must be a RealtimeRepository")
        self.repository = repository

    @staticmethod
    def _validated(value: object, model_type: type, label: str):
        if not isinstance(value, model_type):
            raise TypeError(f"{label} must be a {model_type.__name__}")
        return model_type.model_validate(value.model_dump())

    def ingest_quote_batch(
        self,
        batch: QuoteBatchInput,
        *,
        key: str,
    ) -> RealtimeImportResult:
        validated = self._validated(batch, QuoteBatchInput, "batch")
        try:
            return self.repository.ingest_quote_batch(validated, key=key)
        except Conflict as error:
            raise RealtimeIngestionConflict(
                "Realtime quote batch conflicts with stored evidence"
            ) from error

    def ingest_minute_batch(
        self,
        batch: MinuteBatchInput,
        *,
        key: str,
        rules: Sequence[AnomalyRule] = (),
    ) -> RealtimeImportResult:
        validated = self._validated(batch, MinuteBatchInput, "batch")
        if isinstance(rules, (str, bytes)) or not isinstance(rules, Sequence):
            raise TypeError("rules must be a sequence of AnomalyRule")
        validated_rules = tuple(
            self._validated(rule, AnomalyRule, "rule") for rule in rules
        )
        try:
            return self.repository.ingest_minute_batch(
                validated,
                key=key,
                rules=validated_rules,
            )
        except Conflict as error:
            raise RealtimeIngestionConflict(
                "Realtime minute batch conflicts with stored evidence"
            ) from error

    def ingest_sector_flow_batch(
        self,
        batch: SectorFlowBatchInput,
        *,
        key: str,
    ) -> RealtimeImportResult:
        validated = self._validated(batch, SectorFlowBatchInput, "batch")
        try:
            return self.repository.ingest_sector_flow_batch(validated, key=key)
        except Conflict as error:
            raise RealtimeIngestionConflict(
                "Realtime sector flow batch conflicts with stored evidence"
            ) from error


__all__ = ["RealtimeIngestion", "RealtimeIngestionConflict"]
