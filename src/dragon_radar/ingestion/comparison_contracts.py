"""Closed Q03 contracts. Evidence descriptors do not confer approval."""

from collections import Counter
from collections.abc import Mapping
from datetime import date
from decimal import Decimal, DecimalException
from types import MappingProxyType
from typing import Annotated, Literal

from pydantic import (
    AfterValidator,
    BeforeValidator,
    ConfigDict,
    Field,
    field_serializer,
    field_validator,
    model_validator,
)

from dragon_radar.domain.models import FrozenModel, UTCDateTime
from dragon_radar.ingestion.sample_archive import Digest

ALGORITHM = "paired-sample-decimal34-v1"
MAX_METADATA_BYTES = 4 * 1024 * 1024
MAX_ROWS = 100000
MAX_CELLS = 250000
MAX_REPORT_BYTES = 16 * 1024 * 1024
FIELDS = (
    "open",
    "high",
    "low",
    "close",
    "previous_close",
    "volume",
    "amount",
    "adjustment_factor",
    "benchmark_close",
)
FieldName = Literal[
    "open",
    "high",
    "low",
    "close",
    "previous_close",
    "volume",
    "amount",
    "adjustment_factor",
    "benchmark_close",
]
Token = Annotated[
    str,
    Field(
        strict=True,
        min_length=1,
        max_length=100,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:/+-]*$",
    ),
]
Nonnegative = Annotated[int, Field(strict=True, ge=0, le=MAX_CELLS)]
Status = Literal["pass", "fail", "incompatible", "blocked"]
Code = Literal[
    "arguments",
    "input_unavailable",
    "invalid_input",
    "output_conflict",
    "output_unavailable",
    "storage_denied",
    "operation_time",
    "budget",
    "archive_integrity",
    "sample_invalid",
    "sample_unavailable",
    "same_archive",
    "same_supplier",
    "same_upstream",
    "shared_lineage",
    "source_mismatch",
    "kind_mismatch",
    "identity_mapping",
    "identity_conflict",
    "state_unavailable",
    "cutoff_mismatch",
    "session_mismatch",
    "pool_mismatch",
    "calendar_mismatch",
    "benchmark_mismatch",
    "units_mismatch",
    "adjustment_mismatch",
    "evidence_unavailable",
    "evidence_invalid",
    "evidence_binding",
    "evidence_approval_unavailable",
    "precision_unavailable",
    "precision_mismatch",
    "numeric_conflict",
    "observation_unavailable",
    "suspended",
    "nonfinite",
    "numeric_budget",
]


def _decimal_string(value):
    if isinstance(value, Decimal):
        value = str(value)
    if type(value) is not str or not 1 <= len(value) <= 128:
        raise ValueError("decimal_string")
    try:
        number = Decimal(value)
    except DecimalException:
        raise ValueError("decimal_string") from None
    if not number.is_finite() or abs(number.adjusted()) > 1000:
        raise ValueError("finite_bounded_decimal")
    return number


def _nonnegative(value):
    if value < 0:
        raise ValueError("nonnegative_decimal")
    return value


ToleranceDecimal = Annotated[
    Decimal,
    BeforeValidator(_decimal_string),
    AfterValidator(_nonnegative),
]


class Contract(FrozenModel):
    model_config = ConfigDict(
        strict=True,
        frozen=True,
        extra="forbid",
        hide_input_in_errors=True,
    )


class Tolerance(Contract):
    abs: ToleranceDecimal = Decimal(0)
    rel: ToleranceDecimal = Decimal(0)


def _dates(value):
    if not isinstance(value, (tuple, list)):
        raise ValueError("explicit_dates")
    for day in value:
        if type(day) is date:
            continue
        if type(day) is not str or date.fromisoformat(day).isoformat() != day:
            raise ValueError("explicit_dates")
    return tuple(day if type(day) is date else date.fromisoformat(day) for day in value)


def _ordered(value):
    if tuple(sorted(set(value))) != value:
        raise ValueError("ordered_unique")
    return value


class ComparisonPlan(Contract):
    schema_version: Literal["dragon-radar.supplier-comparison/v1"] = Field(
        alias="schema"
    )
    kind: Literal["test", "supplier_sample"]
    left_archive_sha256: Digest
    right_archive_sha256: Digest
    left_supplier_id: Token
    right_supplier_id: Token
    left_upstream_id: Token
    right_upstream_id: Token
    independence_evidence_sha256: Digest
    as_of: UTCDateTime
    decision_sessions: tuple[date, ...] = Field(min_length=1, max_length=250)
    window: Literal[20]
    pool_sha256: Digest
    calendar_sha256: Digest
    adjustment_semantics: Literal["raw_ohlc_with_multiplier"]
    adjustment_evidence_sha256: Digest
    identity_mapping_sha256: Digest
    benchmark_definition_sha256: Digest
    fields: tuple[FieldName, ...] = Field(min_length=1, max_length=9)
    max_security_count: int = Field(default=100, strict=True, ge=1, le=100)
    tolerances: Mapping[FieldName, Tolerance] = Field(default_factory=dict)
    tolerance_evidence_sha256: Digest | None = None

    @field_validator("window", mode="before")
    @classmethod
    def integer_window(cls, value):
        if type(value) is not int:
            raise ValueError("integer_window")
        return value

    @field_validator("decision_sessions", mode="before")
    @classmethod
    def explicit_sessions(cls, value):
        return _dates(value)

    @field_validator("decision_sessions")
    @classmethod
    def ordered_sessions(cls, value):
        return _ordered(value)

    @field_validator("fields")
    @classmethod
    def canonical_fields(cls, value):
        if len(set(value)) != len(value):
            raise ValueError("duplicate_field")
        return tuple(f for f in FIELDS if f in value)

    @field_validator("fields", mode="before")
    @classmethod
    def explicit_fields(cls, value):
        if not isinstance(value, (list, tuple)):
            raise ValueError("explicit_fields")
        return tuple(value)

    @model_validator(mode="before")
    @classmethod
    def default_tolerances(cls, value):
        if isinstance(value, dict) and "tolerances" not in value:
            value = dict(value)
            fields = value.get("fields", ())
            if not isinstance(fields, (tuple, list)) or any(
                type(f) is not str for f in fields
            ):
                raise ValueError("explicit_fields")
            value["tolerances"] = {f: {"abs": "0", "rel": "0"} for f in fields}
        return value

    @model_validator(mode="after")
    def exact_tolerances(self):
        if set(self.tolerances) != set(self.fields):
            raise ValueError("tolerance_fields")
        object.__setattr__(self, "tolerances", MappingProxyType(dict(self.tolerances)))
        return self

    @field_serializer("tolerances")
    def serialize_tolerances(self, value):
        return dict(value)


class IdentityEntry(Contract):
    left_security_id: Token
    right_security_id: Token
    canonical_security_id: Token
    exchange: Literal["SSE"]
    code: Annotated[str, Field(strict=True, pattern=r"^[0-9]{6}$")]
    security_type: Literal["common_stock"]
    board_category: Literal["sh_main"]
    effective_from: date
    effective_to: date | None

    @model_validator(mode="after")
    def interval(self):
        if self.effective_to is not None and self.effective_to <= self.effective_from:
            raise ValueError("identity_interval")
        return self


class IdentityMap(Contract):
    schema_version: Literal["dragon-radar.supplier-identity-map/v1"] = Field(
        alias="schema"
    )
    entries: tuple[IdentityEntry, ...] = Field(min_length=1, max_length=MAX_ROWS)

    @model_validator(mode="after")
    def one_to_one(self):
        for field in ("left_security_id", "right_security_id", "canonical_security_id"):
            values = tuple(getattr(e, field) for e in self.entries)
            if len(set(values)) != len(values):
                raise ValueError("duplicate_identity")
            if field == "canonical_security_id":
                _ordered(values)
        if len({(e.exchange, e.code) for e in self.entries}) != len(self.entries):
            raise ValueError("duplicate_exchange_code")
        return self


class SourceEvidence(Contract):
    supplier_id: Token
    upstream_id: Token
    source_version: Token
    pool_version: Token
    calendar_version: Token
    adjustment_version: Token
    collection_fingerprint: Digest
    request_sha256: Digest
    extraction_sha256: Digest
    sample_file_sha256: Digest
    raw_sha256: tuple[Digest, ...] = Field(min_length=1, max_length=10000)
    benchmark_id: Token
    currency: Token
    price_unit: Token
    volume_unit: Token
    amount_unit: Token


class BenchmarkDefinition(Contract):
    canonical_benchmark_id: Token
    currency: Literal["CNY"]
    price_unit: Literal["currency/share"]
    method_version: Token


class EvidenceBasis(Contract):
    as_of: UTCDateTime
    decision_sessions: tuple[date, ...] = Field(min_length=1, max_length=250)
    pool_sha256: Digest
    calendar_sha256: Digest
    adjustment_semantics: Literal["raw_ohlc_with_multiplier"]
    benchmark_definition: BenchmarkDefinition


class ComparisonEvidence(Contract):
    schema_version: Literal["dragon-radar.supplier-comparison-evidence/v1"] = Field(
        alias="schema",
    )
    kind: Literal["test", "supplier_sample"]
    left: SourceEvidence
    right: SourceEvidence
    basis: EvidenceBasis
    tolerances: dict[FieldName, Tolerance] | None


class InputMetadata(Contract):
    archive_sha256: Digest
    source_id: Token
    source_version: Token
    upstream_id: Token
    pool_version: Token
    calendar_version: Token
    adjustment_version: Token
    collection_fingerprint: Digest
    request_sha256: Digest
    extraction_sha256: Digest
    sample_file_sha256: Digest
    raw_sha256: tuple[Digest, ...] = Field(min_length=1, max_length=10000)


class Inputs(Contract):
    left: InputMetadata | None = None
    right: InputMetadata | None = None


class Selection(Contract):
    security_ids: tuple[Token, ...] = Field(default=(), max_length=100)
    sessions: tuple[date, ...] = Field(default=(), max_length=250)
    fields: tuple[FieldName, ...] = Field(default=(), max_length=9)
    benchmark_id: Token | None = None


class Counts(Contract):
    expected: Nonnegative = 0
    agree: Nonnegative = 0
    conflict: Nonnegative = 0
    unavailable: Nonnegative = 0

    @model_validator(mode="after")
    def partition(self):
        if self.expected != self.agree + self.conflict + self.unavailable:
            raise ValueError("count_partition")
        return self


class Issue(Contract):
    code: Code
    status: Literal["fail", "incompatible", "blocked"]
    canonical_security_id: Token | None = None
    trade_date: date | None = None
    field: FieldName | None = None


def _wire_decimal(value):
    if type(value) is not str:
        raise ValueError("decimal_string")
    _decimal_string(value)
    return value


DecimalText = Annotated[str, BeforeValidator(_wire_decimal)]


class Cell(Contract):
    canonical_security_id: Token
    trade_date: date
    field: FieldName
    status: Literal["agree", "conflict", "unavailable"]
    left: DecimalText | None
    right: DecimalText | None
    absolute_difference: DecimalText | None
    limit: DecimalText | None
    reason: Code | None

    @model_validator(mode="after")
    def status_values(self):
        if self.status == "unavailable":
            if (
                self.absolute_difference is not None
                or self.limit is not None
                or not self.reason
            ):
                raise ValueError("unavailable_cell")
        elif any(
            v is None
            for v in (self.left, self.right, self.absolute_difference, self.limit)
        ) or self.reason != (None if self.status == "agree" else "numeric_conflict"):
            raise ValueError("numeric_cell")
        return self


class ComparisonReport(Contract):
    schema_version: Literal["dragon-radar.supplier-comparison-report/v1"] = Field(
        default="dragon-radar.supplier-comparison-report/v1",
        alias="schema",
    )
    comparison_id: Digest | None = None
    status: Status
    test_only: bool = True
    production_approved: Literal[False] = False
    historical_pit_status: Literal["unverified"] = "unverified"
    plan_sha256: Digest | None = None
    identity_mapping_sha256: Digest | None = None
    inputs: Inputs = Field(default_factory=Inputs)
    selection: Selection = Field(default_factory=Selection)
    counts: Counts = Field(default_factory=Counts)
    issues: tuple[Issue, ...] = Field(default=(), max_length=MAX_CELLS)
    cells: tuple[Cell, ...] = Field(default=(), max_length=MAX_CELLS)

    @model_validator(mode="after")
    def report_invariants(self):
        expected_status = next(
            (
                status
                for status in ("fail", "incompatible", "blocked")
                if any(i.status == status for i in self.issues)
            ),
            "pass",
        )
        if self.status != expected_status:
            raise ValueError("status_precedence")
        tally = Counter(c.status for c in self.cells)
        if (
            self.counts.expected != len(self.cells)
            or self.counts.agree != tally["agree"]
            or self.counts.conflict != tally["conflict"]
            or self.counts.unavailable != tally["unavailable"]
        ):
            raise ValueError("cell_partition")
        keys = [(c.canonical_security_id, c.trade_date, c.field) for c in self.cells]
        if len(set(keys)) != len(keys):
            raise ValueError("duplicate_cell")
        if self.status == "pass" and (
            self.issues
            or not self.cells
            or self.counts.agree != self.counts.expected
            or self.comparison_id is None
            or self.inputs.left is None
            or self.inputs.right is None
        ):
            raise ValueError("complete_agreement_required")
        return self
