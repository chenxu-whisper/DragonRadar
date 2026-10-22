"""Typed AST, canonical hashing, wire contract and the query_records table.

Boundary between parser-layer ``unsupported_value`` and direct model errors:

* The deterministic parser (``parser.py``) lexes raw text first. A value that
  is lexically produced but semantically illegal (out of range, too precise,
  unknown enum value, ...) is reported to the client as a ``200 unsupported``
  item with code ``unsupported_value``; it never raises.
* Direct construction of these models is a trust boundary: every illegal
  field/op/value combination raises ``pydantic.ValidationError`` (a
  ``ValueError`` subclass) and HTTP routes normalize it to ``400``.

``return_ratio`` is canonicalized by *both* layers onto the six-decimal-place
ROUND_HALF_UP grid: the parser quantizes text-period, and the models re-apply
the idempotent quantization on direct construction (spec 3.2 line 105), so a
too-precise model-proposed AST cannot be laundered through validation. Zero is
normalized to the positive ``Decimal("0.000000")`` token (negative zero and
exponent spellings such as ``1E+1`` collapse onto one canonical value/hash).
"""

import enum
import hashlib
import json
import re
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Literal

from pydantic import (
    ConfigDict,
    StrictStr,
    ValidationInfo,
    field_validator,
    model_validator,
)
from pydantic import (
    Field as PydanticField,
)
from sqlalchemy import (
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    String,
    Table,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID

from dragon_radar.domain.models import FrozenModel
from dragon_radar.owner.models import owner_principals  # noqa: F401
from dragon_radar.publication.tables import publications  # noqa: F401
from dragon_radar.roles.rules import ROLE_NAMES
from dragon_radar.storage.models import metadata

PARSER_VERSION = "zh-whitelist-v1"
AST_VERSION = 1

_RETURN_RATIO_QUANTUM = Decimal("0.000001")
_SECURITY_ID_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,32}$")
_HASH_RE = re.compile(r"^[0-9a-f]{64}$")


class Field(enum.Enum):
    """Whitelisted query fields (spec 3.2)."""

    SECURITY_ID = "security_id"
    SECTOR_ID = "sector_id"
    RETURN_RATIO = "return_ratio"
    BOARDS = "boards"
    ROLE = "role"


class Op(enum.Enum):
    EQ = "eq"
    IN = "in"
    GT = "gt"
    GTE = "gte"
    LT = "lt"
    LTE = "lte"


class SortDirection(enum.Enum):
    ASC = "asc"
    DESC = "desc"


class ParserKind(enum.Enum):
    DETERMINISTIC = "deterministic"
    MODEL = "model"


class UnsupportedCode(enum.Enum):
    UNKNOWN_FIELD = "unknown_field"
    UNKNOWN_OPERATOR = "unknown_operator"
    ORPHAN_TOKEN = "orphan_token"
    UNSUPPORTED_LOGIC = "unsupported_logic"
    UNSUPPORTED_EXPRESSION = "unsupported_expression"
    UNSUPPORTED_VALUE = "unsupported_value"
    EMPTY_INPUT = "empty_input"
    MODEL_AST_REJECTED = "model_ast_rejected"
    MODEL_UNAVAILABLE = "model_unavailable"


ROLE_EXTERNAL_VALUES = frozenset(ROLE_NAMES.values())

_COMPARISON_OPS = frozenset({Op.GT, Op.GTE, Op.LT, Op.LTE})
_ALLOWED_OPS: dict[Field, frozenset[Op]] = {
    Field.SECURITY_ID: frozenset({Op.EQ, Op.IN}),
    Field.SECTOR_ID: frozenset({Op.EQ, Op.IN}),
    Field.ROLE: frozenset({Op.EQ, Op.IN}),
    Field.RETURN_RATIO: frozenset(_COMPARISON_OPS),
    Field.BOARDS: frozenset({Op.EQ, Op.IN, *_COMPARISON_OPS}),
}


class _StrictFrozenModel(FrozenModel):
    model_config = ConfigDict(
        strict=True,
        frozen=True,
        extra="forbid",
        allow_inf_nan=False,
    )


def _dedupe(values: tuple[object, ...]) -> tuple[object, ...]:
    seen: list[object] = []
    for value in values:
        if value not in seen:
            seen.append(value)
    return tuple(seen)


def _security_scalar(value: object) -> str:
    if not isinstance(value, str) or not _SECURITY_ID_RE.fullmatch(value):
        raise ValueError("security_id must match ^[A-Za-z0-9_.:-]{1,32}$")
    return value


def _sector_scalar(value: object) -> str:
    if not isinstance(value, str) or not 1 <= len(value) <= 64:
        raise ValueError("sector_id must be a non-empty string of at most 64 chars")
    return value


def _return_ratio_scalar(value: object) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, Decimal):
        raise ValueError("return_ratio must be a Decimal, never float/bool")
    if not value.is_finite():
        raise ValueError("return_ratio must be finite")
    if not is_return_ratio_precision_supported(value):
        raise ValueError(
            "return_ratio must carry at most six decimal places (spec 3.2 line 105)"
        )
    # Idempotent on parser-quantized values; canonicalizes spellings such as
    # 1E+1 / 10 / -0 onto one stable grid token (spec N5 / review M3-M4).
    quantized = quantize_return_ratio(value)
    if quantized == 0:
        quantized = Decimal("0.000000")
    if not Decimal(-1) <= quantized <= Decimal(10):
        raise ValueError("return_ratio must be within [-1, 10]")
    return quantized


def _boards_scalar(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError("boards must be an int")
    if not 0 <= value <= 99:
        raise ValueError("boards must be within 0..99")
    return value


def _role_scalar(value: object) -> str:
    if not isinstance(value, str) or value not in ROLE_EXTERNAL_VALUES:
        raise ValueError("role must be one of the six external role enum values")
    return value


_SCALAR_VALIDATORS = {
    Field.SECURITY_ID: _security_scalar,
    Field.SECTOR_ID: _sector_scalar,
    Field.RETURN_RATIO: _return_ratio_scalar,
    Field.BOARDS: _boards_scalar,
    Field.ROLE: _role_scalar,
}
_IN_LIST_MAX = {
    Field.SECURITY_ID: 100,
    Field.SECTOR_ID: 100,
    # RETURN_RATIO intentionally absent: IN is rejected earlier by _ALLOWED_OPS.
    Field.BOARDS: 100,
    Field.ROLE: 6,
}


def _validate_predicate_value(field: Field, op: Op, value: object) -> object:
    allowed = _ALLOWED_OPS[field]
    if op not in allowed:
        raise ValueError(f"op {op.value!r} is not allowed for field {field.value!r}")
    scalar = _SCALAR_VALIDATORS[field]
    if op is Op.IN:
        if isinstance(value, str) or not isinstance(value, tuple | list):
            raise ValueError("in value must be a tuple of scalars")
        if not value:
            raise ValueError("in list must be non-empty")
        normalized = tuple(scalar(item) for item in value)
        limit = _IN_LIST_MAX[field]
        if len(normalized) > limit:
            raise ValueError(
                f"in list for {field.value!r} may contain <= {limit} items"
            )
        return _dedupe(normalized)
    return scalar(value)


class Predicate(_StrictFrozenModel):
    """One conjunctive predicate.

    ``value`` is ``object`` because the domain depends on ``field``/``op``;
    the model validator enforces the full field x op x value matrix (spec
    3.2). ``in`` values are normalized to a deduplicated tuple preserving
    first-occurrence order.
    """

    field: Field
    op: Op
    value: object

    @field_validator("value")
    @classmethod
    def _value_matches_matrix(cls, value: object, info: ValidationInfo) -> object:
        field = info.data.get("field")
        op = info.data.get("op")
        # field/op carry their own enum validation errors; let those surface.
        if field is None or op is None:
            return value
        return _validate_predicate_value(field, op, value)


class SortSpec(_StrictFrozenModel):
    field: Field
    direction: SortDirection


class QueryAst(_StrictFrozenModel):
    version: Literal[1] = 1
    predicates: tuple[Predicate, ...] = PydanticField(min_length=1, max_length=5)
    sort: SortSpec | None = None
    limit: int = PydanticField(default=50, ge=1, le=100)


class TypedAstWire(_StrictFrozenModel):
    """EX12 typed_ast: the four body keys plus the embedded ``ast_hash``."""

    version: Literal[1] = 1
    ast_hash: StrictStr = PydanticField(pattern=_HASH_RE)
    predicates: tuple[Predicate, ...] = PydanticField(min_length=1, max_length=5)
    sort: SortSpec | None = None
    limit: int = PydanticField(default=50, ge=1, le=100)

    @model_validator(mode="after")
    def _ast_hash_matches_body(self) -> "TypedAstWire":
        body = QueryAst(
            version=self.version,
            predicates=self.predicates,
            sort=self.sort,
            limit=self.limit,
        )
        if self.ast_hash != ast_hash_of(body):
            raise ValueError("ast_hash must equal ast_hash_of(ast body)")
        return self


def canonical_ast(ast: QueryAst) -> str:
    """Canonical JSON over exactly {version, predicates, sort, limit}.

    Decimal values become JSON string tokens via ``model_dump(mode="json")``
    so hashing is stable across processes and never float-dependent.
    """

    body = ast.model_dump(mode="json")
    return json.dumps(
        body,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    )


def ast_hash_of(ast: QueryAst) -> str:
    return hashlib.sha256(canonical_ast(ast).encode("utf-8")).hexdigest()


def wire_ast(ast: QueryAst) -> TypedAstWire:
    return TypedAstWire(
        version=ast.version,
        ast_hash=ast_hash_of(ast),
        predicates=ast.predicates,
        sort=ast.sort,
        limit=ast.limit,
    )


def quantize_return_ratio(value: Decimal) -> Decimal:
    """ROUND_HALF_UP to six decimal places.

    Precondition: ``value`` is a finite Decimal whose quantization fits the
    active decimal context precision. Callers (parser and model layers) must
    gate through :func:`is_return_ratio_precision_supported` first; otherwise
    ``Decimal.quantize`` may raise ``decimal.InvalidOperation`` (an
    ``ArithmeticError``, *not* a ``ValueError``).
    """

    return value.quantize(_RETURN_RATIO_QUANTUM, rounding=ROUND_HALF_UP)


def is_return_ratio_precision_supported(value: Decimal) -> bool:
    """True when six-place quantization preserves the threshold.

    Trailing-zero scales such as ``0.0500000`` are supported because the
    quantized value is unchanged; ``0.1234567`` is not (quantization would
    silently move the user's threshold). Values whose quantization overflows
    the decimal context (e.g. 23+ integer digits under the default precision
    28) answer ``False`` instead of escaping ``decimal.InvalidOperation``
    (review B1): the parser maps them to ``unsupported_value`` rather than a
    500.
    """

    if not isinstance(value, Decimal) or not value.is_finite():
        return False
    try:
        quantized = quantize_return_ratio(value)
    except (InvalidOperation, ValueError):
        return False
    return quantized == value


query_records = Table(
    "query_records",
    metadata,
    Column("id", PGUUID(as_uuid=True), primary_key=True),
    Column(
        "owner_id",
        PGUUID(as_uuid=True),
        ForeignKey("owner_principals.id", ondelete="RESTRICT"),
        nullable=False,
    ),
    Column(
        "snapshot_id",
        PGUUID(as_uuid=True),
        ForeignKey("publications.id", ondelete="RESTRICT"),
        nullable=False,
    ),
    Column("idempotency_key", String(200), nullable=False),
    Column("request_hash", String(64), nullable=False),
    Column("ast", JSONB, nullable=False),
    Column("ast_hash", String(64), nullable=False),
    Column("parser_version", String(40), nullable=False),
    Column("parser_kind", String(20), nullable=False),
    Column("model_version", String(100)),
    Column("created_at", DateTime(timezone=True), nullable=False),
    UniqueConstraint("owner_id", "idempotency_key"),
    CheckConstraint("parser_kind IN ('deterministic', 'model')"),
    # Frozen wording for the 0013_query migration DDL (spec 3.5, review m1):
    # deterministic parses never carry a model_version; model parses must.
    CheckConstraint(
        "parser_kind = 'deterministic' OR model_version IS NOT NULL",
        name="query_records_kind_model_version",
    ),
)


__all__ = [
    "AST_VERSION",
    "PARSER_VERSION",
    "ROLE_EXTERNAL_VALUES",
    "Field",
    "Op",
    "ParserKind",
    "Predicate",
    "QueryAst",
    "SortDirection",
    "SortSpec",
    "TypedAstWire",
    "UnsupportedCode",
    "ast_hash_of",
    "canonical_ast",
    "is_return_ratio_precision_supported",
    "quantize_return_ratio",
    "query_records",
    "wire_ast",
]
