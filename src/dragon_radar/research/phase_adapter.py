"""Reduce verified archived EX16 phases; never classify historical phases."""

from datetime import date, datetime
from typing import Literal
from uuid import UUID

from pydantic import Field

from dragon_radar.domain.models import Identifier
from dragon_radar.research.factor_adapters import Digest, exact_model
from dragon_radar.research.models import StrictFrozenModel
from dragon_radar.research.result_contracts import require
from dragon_radar.research.walk_forward import PhasePublication

Phase = Literal["ice", "ebb", "divergent", "repair", "climax"]


class PhaseEntry(StrictFrozenModel):
    publication_id: UUID
    publication_sequence: int
    trade_date: date
    available_at: datetime
    phase: Phase | None
    sentiment_method_version: Identifier
    input_sha256: Digest


class PhaseSeriesArchive(StrictFrozenModel):
    schema_tag: Literal["dragon-radar.research-phase-input/v1"] = Field(alias="schema")
    method_version: Identifier
    approval_evidence_sha256: Digest
    anchor_sequence: int
    entries: tuple[PhaseEntry, ...]


def adapt_phase_series(raw, *, anchor_sequence, verify, publications):
    archive = exact_model(PhaseSeriesArchive, raw)
    require(
        archive.anchor_sequence == anchor_sequence
        and archive.entries
        and verify(archive.approval_evidence_sha256, raw),
        "phase_series_unavailable",
    )
    result, unavailable, previous = [], [], 0
    for entry in archive.entries:
        pub = publications.get(entry.publication_id)
        require(
            pub is not None
            and previous < entry.publication_sequence <= anchor_sequence
            and entry.publication_sequence == pub["publication_sequence"]
            and entry.trade_date == pub["trade_date"]
            and entry.available_at.tzinfo is not None
            and entry.available_at <= pub["cutoff"]
            and pub["sentiment_method_version"] == entry.sentiment_method_version
            and pub["phase"] == entry.phase
            and pub["input_sha256"] == entry.input_sha256
            and verify(entry.input_sha256, pub),
            "phase archive binding unavailable",
        )
        previous = entry.publication_sequence
        if entry.phase is None:
            unavailable.append(entry.trade_date)
        else:
            result.append(
                PhasePublication(
                    trade_date=entry.trade_date,
                    publication_sequence=entry.publication_sequence,
                    phase=entry.phase,
                )
            )
    require(result, "phase_series_unavailable")
    return tuple(result), tuple(unavailable)
