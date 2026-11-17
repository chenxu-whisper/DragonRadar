"""Explicit offline supplier normalization, without identity or state inference.

Use MappingProfile.model_validate[_json] at the configuration trust boundary.
Nested dictionaries are copied and frozen; unchecked model_construct/model_copy
updates are not supported. Issues are safe metadata, not validation exceptions.
Normalization is structural validation, not downstream sample/quality approval.
"""

from collections.abc import Iterable, Mapping
from datetime import date
from decimal import MAX_EMAX, MIN_EMIN, Decimal, Inexact, localcontext
from types import MappingProxyType
from typing import Annotated, Literal, get_args

from pydantic import (
    ConfigDict,
    Field,
    TypeAdapter,
    ValidationError,
    field_serializer,
    model_validator,
)

from dragon_radar.domain.models import DailyBar, FrozenModel, Identifier, SecurityState

_MODELS = {"bar": DailyBar, "state": SecurityState}
_FIELDS = {kind: model.model_fields for kind, model in _MODELS.items()}
_TARGETS = frozenset().union(*_FIELDS.values())
_SOURCE_FIELDS = frozenset({"source_id", "source_version"})
_DATE_FIELDS = frozenset(
    {"trade_date", "limit_date", "effective_from", "effective_to", "listing_date"}
)
_ADAPTERS = {
    kind: {
        name: TypeAdapter(field.rebuild_annotation()) for name, field in fields.items()
    }
    for kind, fields in _FIELDS.items()
}


def _contains_decimal(annotation: object) -> bool:
    return annotation is Decimal or any(
        _contains_decimal(arg) for arg in get_args(annotation)
    )


_DECIMAL_FIELDS = frozenset(
    name
    for fields in _FIELDS.values()
    for name, field in fields.items()
    if _contains_decimal(field.annotation)
)

IssueCode = Literal[
    "missing_field",
    "source_mismatch",
    "unknown_value",
    "inexact_decimal",
    "nonfinite",
    "invalid_value",
    "invalid_row",
]


class _ConversionError(ValueError):
    def __init__(self, code: IssueCode):
        self.code = code
        super().__init__(code)


def _canonical_value(kind: str, target: str, value: object) -> object:
    if target in _DATE_FIELDS and value is not None and type(value) is not date:
        if type(value) is not str or date.fromisoformat(value).isoformat() != value:
            raise _ConversionError("invalid_value")
    if target in _DECIMAL_FIELDS and value is not None:
        if isinstance(value, (float, bool)):
            raise _ConversionError("inexact_decimal")
        if not isinstance(value, (str, int, Decimal)):
            raise _ConversionError("invalid_value")
        value = Decimal(value)
        if not value.is_finite():
            raise _ConversionError("nonfinite")
    return _ADAPTERS[kind][target].validate_python(value)


def _dictionary(value: object) -> dict:
    if not isinstance(value, Mapping) or any(type(key) is not str for key in value):
        raise ValueError("invalid_mapping_profile")
    return dict(value)


def _plain(value: object) -> object:
    if isinstance(value, Mapping):
        return {key: _plain(item) for key, item in value.items()}
    return value


class MappingProfile(FrozenModel):
    """Exact profile schema; configuration values are canonical immutable scalars.

    Shared target names in value_maps have the same meaning for both models and
    outputs must validate against every model declaring that target. Constants
    are already canonical: value_maps apply only to supplier column values.
    Decimal scales apply to explicitly supplied target values, including constants.
    """

    model_config = ConfigDict(
        strict=True, hide_input_in_errors=True, serialize_by_alias=True
    )

    schema_version: Literal["dragon-radar-supplier-mapping/v1"] = Field(alias="schema")
    profile_id: Identifier
    source_id: Identifier
    source_version: Identifier
    bar_fields: Mapping[str, str] = Field(default_factory=dict)
    state_fields: Mapping[str, str] = Field(default_factory=dict)
    bar_constants: Mapping[str, object] = Field(default_factory=dict)
    state_constants: Mapping[str, object] = Field(default_factory=dict)
    value_maps: Mapping[str, Mapping[str, object]] = Field(default_factory=dict)
    decimal_scales: Mapping[str, str] = Field(default_factory=dict)

    @model_validator(mode="before")
    @classmethod
    def validate_configuration(cls, value: object) -> object:
        if isinstance(value, cls):
            return value
        try:
            data = _dictionary(value)
            allowed = {field.alias or name for name, field in cls.model_fields.items()}
            if data.keys() - allowed:
                raise ValueError("invalid_mapping_profile")
            for kind in _MODELS:
                fields = _dictionary(data.get(f"{kind}_fields", {}))
                constants = _dictionary(data.get(f"{kind}_constants", {}))
                if (fields.keys() | constants.keys()) - _FIELDS[kind].keys():
                    raise ValueError("invalid_mapping_profile")
                if fields.keys() & constants.keys():
                    raise ValueError("invalid_mapping_profile")
                if any(
                    type(column) is not str or not column.strip()
                    for column in fields.values()
                ):
                    raise ValueError("invalid_mapping_profile")
                for target, constant in constants.items():
                    constant = _canonical_value(kind, target, constant)
                    if target in _SOURCE_FIELDS and constant != data.get(target):
                        raise ValueError("invalid_mapping_profile")
                    constants[target] = constant
                data[f"{kind}_fields"] = fields
                data[f"{kind}_constants"] = constants

            maps = _dictionary(data.get("value_maps", {}))
            if maps.keys() - _TARGETS:
                raise ValueError("invalid_mapping_profile")
            for target, entries in maps.items():
                entries = _dictionary(entries)
                for raw, output in entries.items():
                    for kind in _MODELS:
                        if target in _FIELDS[kind]:
                            output = _canonical_value(kind, target, output)
                    if target in _SOURCE_FIELDS and output != data.get(target):
                        raise ValueError("invalid_mapping_profile")
                    entries[raw] = output
                maps[target] = entries
            data["value_maps"] = maps
            scales = _dictionary(data.get("decimal_scales", {}))
            if scales.keys() - _DECIMAL_FIELDS:
                raise ValueError("invalid_mapping_profile")
            for scale in scales.values():
                if type(scale) is not str:
                    raise ValueError("invalid_mapping_profile")
                decimal = Decimal(scale)
                if not decimal.is_finite() or decimal <= 0:
                    raise ValueError("invalid_mapping_profile")
            data["decimal_scales"] = scales
            return data
        except Exception:
            # Never propagate provider column names, values or exception text.
            raise ValueError("invalid_mapping_profile") from None

    @model_validator(mode="after")
    def freeze_configuration(self) -> "MappingProfile":
        for name in (
            "bar_fields",
            "state_fields",
            "bar_constants",
            "state_constants",
            "decimal_scales",
        ):
            object.__setattr__(self, name, MappingProxyType(dict(getattr(self, name))))
        object.__setattr__(
            self,
            "value_maps",
            MappingProxyType(
                {
                    target: MappingProxyType(dict(entries))
                    for target, entries in self.value_maps.items()
                }
            ),
        )
        return self

    @field_serializer(
        "bar_fields",
        "state_fields",
        "bar_constants",
        "state_constants",
        "value_maps",
        "decimal_scales",
        mode="wrap",
    )
    def serialize_configuration(self, value, handler):
        return handler(_plain(value))


class NormalizationIssue(FrozenModel):
    model_config = ConfigDict(strict=True, hide_input_in_errors=True)

    kind: Literal["bar", "state"]
    row_index: Annotated[int, Field(ge=0)]
    field: str
    code: IssueCode

    @model_validator(mode="after")
    def safe_target(self) -> "NormalizationIssue":
        if self.field not in _FIELDS[self.kind]:
            raise ValueError("invalid_issue_field")
        return self


class NormalizationResult(FrozenModel):
    bars: tuple[DailyBar, ...]
    states: tuple[SecurityState, ...]
    issues: tuple[NormalizationIssue, ...]


def _scaled(value: Decimal, scale: str) -> Decimal:
    multiplier = Decimal(scale)
    # Multiplication needs at most the sum of coefficient lengths. Do not use
    # the caller's context precision or exponent limits.
    with localcontext() as context:
        context.prec = len(value.as_tuple().digits) + len(multiplier.as_tuple().digits)
        context.Emax = MAX_EMAX
        context.Emin = MIN_EMIN
        context.traps[Inexact] = True
        result = value * multiplier
    if not result.is_finite():
        raise ValueError("nonfinite")
    return result


def _normalize_rows(
    profile: MappingProfile,
    kind: Literal["bar", "state"],
    rows: Iterable[Mapping[str, object]],
) -> tuple[tuple, tuple[NormalizationIssue, ...]]:
    records, issues = [], []
    fields = getattr(profile, f"{kind}_fields")
    constants = getattr(profile, f"{kind}_constants")
    for index, row in enumerate(rows):
        errors: dict[str, IssueCode] = {}
        payload = {
            "source_id": profile.source_id,
            "source_version": profile.source_version,
        }
        payload.update(constants)
        if not isinstance(row, Mapping):
            errors["security_id"] = "invalid_row"
        else:
            for target, column in fields.items():
                try:
                    if column not in row:
                        errors[target] = "missing_field"
                        continue
                    value = row[column]
                    if target in _SOURCE_FIELDS and value != getattr(profile, target):
                        errors[target] = "source_mismatch"
                        continue
                    if target in profile.value_maps and value is not None:
                        entries = profile.value_maps[target]
                        if type(value) is not str or value not in entries:
                            errors[target] = "unknown_value"
                            continue
                        value = entries[value]
                    payload[target] = _canonical_value(kind, target, value)
                except _ConversionError as exc:
                    errors[target] = exc.code
                except Exception:
                    errors[target] = "invalid_value"
            for target, scale in profile.decimal_scales.items():
                if target in payload and payload[target] is not None:
                    try:
                        payload[target] = _scaled(payload[target], scale)
                    except Exception:
                        errors[target] = "invalid_value"

            # Validate even when conversion failed to report other bad/missing
            # canonical fields. Conversion errors take priority over missing.
            try:
                record = _MODELS[kind].model_validate(payload)
            except ValidationError as exc:
                for error in exc.errors(
                    include_url=False, include_input=False, include_context=False
                ):
                    target = error["loc"][0] if error["loc"] else "security_id"
                    if target not in _FIELDS[kind]:
                        target = "security_id"
                    errors.setdefault(
                        target,
                        "missing_field"
                        if error["type"] == "missing"
                        else "invalid_value",
                    )
            except Exception:
                errors.setdefault("security_id", "invalid_row")
            else:
                if not errors:
                    records.append(record)
        issues.extend(
            NormalizationIssue(kind=kind, row_index=index, field=target, code=code)
            for target, code in sorted(errors.items())
        )
    return tuple(records), tuple(issues)


def normalize_records(
    profile: MappingProfile,
    *,
    bar_rows: Iterable[Mapping[str, object]],
    state_rows: Iterable[Mapping[str, object]],
) -> NormalizationResult:
    """Return valid rows plus safe issues for rejected rows, with zero-based indices.

    Callers must retain/check issues before claiming completeness and still run
    canonical quality/sample validation. Unused supplier columns are ignored.
    """
    bars, bar_issues = _normalize_rows(profile, "bar", bar_rows)
    states, state_issues = _normalize_rows(profile, "state", state_rows)
    return NormalizationResult(
        bars=bars, states=states, issues=bar_issues + state_issues
    )
