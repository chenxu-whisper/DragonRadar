"""Immutable contracts for reviewed regulation rules and disclosed events."""

from datetime import date
from decimal import Decimal
from typing import Annotated, Literal
from urllib.parse import urlsplit
from uuid import UUID

from pydantic import Field, StrictBool, StrictStr, field_validator, model_validator

from dragon_radar.domain.models import (
    ExactDecimal,
    FrozenModel,
    Identifier,
    UTCDateTime,
)

RuleDirection = Literal["upper", "lower"]
ReviewSwitch = Literal["pending", "approved", "rejected"]
CalculationState = Literal["pending", "idle", "approaching_alert", "triggered"]
InputState = Literal["complete", "missing", "conflict"]
EventType = Literal[
    "abnormal",
    "serious_abnormal",
    "monitoring",
    "restriction",
]
RuleLifecycleStatus = Literal["active", "withdrawn"]
EventSourceCompleteness = Literal["unknown", "complete"]
SUPPORTED_CALCULATION_METHODS = frozenset({"relative_return"})


def _https_url(value: str | None) -> str | None:
    if value is None:
        return None
    parsed = urlsplit(value)
    if (
        parsed.scheme != "https"
        or not parsed.netloc
        or parsed.username is not None
        or parsed.password is not None
    ):
        raise ValueError("Regulation source URL must be an absolute HTTPS URL")
    return value


class RegulationReviewRecord(FrozenModel):
    prepared_by: Identifier
    reviewed_by: Identifier
    published_by: Identifier
    reviewed_at: UTCDateTime
    published_at: UTCDateTime

    @model_validator(mode="after")
    def validate_independent_release(self):
        if self.prepared_by == self.reviewed_by:
            raise ValueError("Regulation review must be independent")
        if self.published_at < self.reviewed_at:
            raise ValueError("Regulation release cannot precede review")
        return self


class RegulationTestCase(FrozenModel):
    name: Identifier
    computed_value: ExactDecimal
    expected_state: Literal["idle", "approaching_alert", "triggered"]

    @field_validator("computed_value")
    @classmethod
    def finite_computed_value(cls, value: Decimal) -> Decimal:
        if not value.is_finite():
            raise ValueError("Regression values must be finite")
        return value


class RegulationRule(FrozenModel):
    rule_id: Annotated[StrictStr, Field(min_length=1, max_length=30)]
    rule_version: Annotated[StrictStr, Field(min_length=1, max_length=20)]
    applicable_exchange: Identifier | None = None
    applicable_board: Identifier | None = None
    security_status: Identifier | None = None
    effective_from: date
    effective_to: date | None = None
    window_days: Annotated[int, Field(gt=0)]
    direction: RuleDirection
    threshold: ExactDecimal
    warning_threshold: ExactDecimal
    benchmark_index: Identifier | None = None
    normal_anomaly_count_mode: Identifier | None = None
    recompute_condition: (
        Annotated[StrictStr, Field(min_length=1, max_length=100)] | None
    )
    calculation_method: Identifier
    official_source_url: (
        Annotated[StrictStr, Field(min_length=1, max_length=500)] | None
    ) = None
    review_switch: ReviewSwitch = "pending"
    review_record: RegulationReviewRecord | None = None
    test_cases: tuple[RegulationTestCase, ...] = ()
    regression_passed: StrictBool = False
    active: StrictBool = False

    _validated_source_url = field_validator("official_source_url")(_https_url)

    @model_validator(mode="after")
    def validate_rule_boundaries(self):
        if self.effective_to is not None and self.effective_to <= self.effective_from:
            raise ValueError("Rule effective interval must be nonempty")
        if not self.threshold.is_finite() or not self.warning_threshold.is_finite():
            raise ValueError("Rule thresholds must be finite")
        if self.direction == "upper" and not (self.warning_threshold < self.threshold):
            raise ValueError("Upper warning threshold must be below threshold")
        if self.direction == "lower" and not (self.warning_threshold > self.threshold):
            raise ValueError("Lower warning threshold must be above threshold")
        case_names = tuple(case.name for case in self.test_cases)
        if len(set(case_names)) != len(case_names):
            raise ValueError("Regulation test case names must be unique")
        return self


class RegulationRuleStatus(FrozenModel):
    id: UUID
    rule_id: Identifier
    rule_version: Identifier
    status: RuleLifecycleStatus
    reason: Identifier
    recorded_at: UTCDateTime


class RegulationObservation(FrozenModel):
    rule_id: Annotated[StrictStr, Field(min_length=1, max_length=30)]
    rule_version: Annotated[StrictStr, Field(min_length=1, max_length=20)]
    security_id: Identifier
    trade_date: date
    exchange: Identifier
    board: Identifier
    security_status: Identifier
    computed_value: ExactDecimal | None
    input_state: InputState

    @model_validator(mode="after")
    def validate_input_state(self):
        if self.input_state == "complete":
            if self.computed_value is None or not self.computed_value.is_finite():
                raise ValueError("Complete regulation input requires a finite value")
        elif self.computed_value is not None:
            raise ValueError("Incomplete regulation input cannot have a value")
        return self


class RegulationCalculation(FrozenModel):
    rule_id: Identifier
    rule_version: Identifier
    security_id: Identifier
    trade_date: date
    window_days: Annotated[int, Field(gt=0)]
    direction: RuleDirection
    threshold: ExactDecimal
    warning_threshold: ExactDecimal
    review_switch: ReviewSwitch
    benchmark_index: Identifier | None
    recompute_condition: StrictStr | None
    computed_value: ExactDecimal | None
    state: CalculationState
    reasons: tuple[Identifier, ...] = ()

    @model_validator(mode="after")
    def pending_has_reason(self):
        if self.state == "pending" and not self.reasons:
            raise ValueError("Pending regulation calculations require reasons")
        if self.state != "pending" and self.reasons:
            raise ValueError("Completed regulation calculations cannot have reasons")
        return self


class RegulationEvent(FrozenModel):
    event_id: Annotated[StrictStr, Field(min_length=1, max_length=32)]
    rule_id: Annotated[StrictStr, Field(min_length=1, max_length=30)]
    rule_version: Annotated[StrictStr, Field(min_length=1, max_length=20)]
    security_id: Annotated[StrictStr, Field(min_length=1, max_length=32)]
    event_type: EventType
    announcement_id: Annotated[StrictStr, Field(min_length=1, max_length=50)] | None
    announcement_url: Annotated[StrictStr, Field(min_length=1, max_length=500)] | None
    effective_from: date
    effective_to: date | None = None
    source_id: Annotated[StrictStr, Field(min_length=1, max_length=200)] | None = None
    source_version: Annotated[StrictStr, Field(min_length=1, max_length=200)] | None = (
        None
    )

    _validated_announcement_url = field_validator("announcement_url")(_https_url)

    @model_validator(mode="after")
    def validate_effective_interval(self):
        if self.effective_to is not None and self.effective_to <= self.effective_from:
            raise ValueError("Event effective interval must be nonempty")
        if (self.source_id is None) != (self.source_version is None):
            raise ValueError(
                "Event source identity and version must be provided together"
            )
        return self


class RegulationSnapshot(FrozenModel):
    id: UUID
    market_snapshot_id: UUID
    trade_date: date
    as_of: UTCDateTime
    event_source_completeness: EventSourceCompleteness = "unknown"
    event_source_id: Identifier | None = None
    event_source_version: Identifier | None = None
    created_at: UTCDateTime

    @model_validator(mode="after")
    def validate_event_source_evidence(self):
        source_is_identified = (
            self.event_source_id is not None and self.event_source_version is not None
        )
        if self.event_source_completeness == "complete":
            if not source_is_identified:
                raise ValueError("Complete event source requires identity and version")
        elif self.event_source_id is not None or self.event_source_version is not None:
            raise ValueError("Unknown event source cannot claim identity or version")
        return self


def rule_is_effective(rule: RegulationRule, day: date) -> bool:
    return rule.effective_from <= day and (
        rule.effective_to is None or day < rule.effective_to
    )


def event_is_effective(event: RegulationEvent, day: date) -> bool:
    return event.effective_from <= day and (
        event.effective_to is None or day < event.effective_to
    )


def production_gate_reasons(rule: RegulationRule) -> tuple[str, ...]:
    reasons = []
    if rule.review_switch != "approved":
        reasons.append("rule_not_approved")
    if not rule.active:
        reasons.append("rule_inactive")
    if rule.official_source_url is None:
        reasons.append("official_source_missing")
    if rule.review_record is None:
        reasons.append("review_record_missing")
    if not rule.test_cases:
        reasons.append("regression_cases_missing")
    if not rule.regression_passed:
        reasons.append("regression_not_passed")
    if rule.calculation_method not in SUPPORTED_CALCULATION_METHODS:
        reasons.append("unsupported_calculation_method")
    return tuple(reasons)
