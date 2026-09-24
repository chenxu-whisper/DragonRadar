"""Pure fail-closed regulation rule evaluation."""

from decimal import Decimal

from dragon_radar.regulation.models import (
    RegulationCalculation,
    RegulationObservation,
    RegulationRule,
    production_gate_reasons,
    rule_is_effective,
)

SUPPORTED_CALCULATION_METHODS = frozenset({"relative_return"})


def _value_state(rule: RegulationRule, value: Decimal) -> str:
    if rule.direction == "upper":
        if value >= rule.threshold:
            return "triggered"
        if value >= rule.warning_threshold:
            return "approaching_alert"
        return "idle"
    if value <= rule.threshold:
        return "triggered"
    if value <= rule.warning_threshold:
        return "approaching_alert"
    return "idle"


def _applicable(rule: RegulationRule, observation: RegulationObservation) -> bool:
    return (
        rule_is_effective(rule, observation.trade_date)
        and (
            rule.applicable_exchange is None
            or rule.applicable_exchange == observation.exchange
        )
        and (
            rule.applicable_board is None or rule.applicable_board == observation.board
        )
        and (
            rule.security_status is None
            or rule.security_status == observation.security_status
        )
    )


def evaluate_rule(
    rule: RegulationRule,
    observation: RegulationObservation,
    *,
    active: bool | None = None,
) -> RegulationCalculation:
    """Evaluate one rule without creating or changing formal event facts."""
    rule = RegulationRule.model_validate(rule.model_dump())
    observation = RegulationObservation.model_validate(observation.model_dump())
    effective_active = rule.active if active is None else active
    reasons = list(production_gate_reasons(rule))
    if effective_active:
        reasons = [reason for reason in reasons if reason != "rule_inactive"]
    elif "rule_inactive" not in reasons:
        reasons.append("rule_inactive")
    if rule.calculation_method not in SUPPORTED_CALCULATION_METHODS:
        reasons.append("unsupported_calculation_method")
    if rule.benchmark_index is None:
        reasons.append("benchmark_missing")
    if not _applicable(rule, observation):
        reasons.append("rule_not_applicable")
    if observation.input_state != "complete":
        reasons.append(f"input_{observation.input_state}")

    reasons = list(dict.fromkeys(reasons))
    state = (
        "pending" if reasons else _value_state(rule, observation.computed_value)  # type: ignore[arg-type]
    )
    return RegulationCalculation(
        rule_id=rule.rule_id,
        rule_version=rule.rule_version,
        security_id=observation.security_id,
        trade_date=observation.trade_date,
        window_days=rule.window_days,
        direction=rule.direction,
        threshold=rule.threshold,
        warning_threshold=rule.warning_threshold,
        review_switch=rule.review_switch,
        benchmark_index=rule.benchmark_index,
        recompute_condition=rule.recompute_condition,
        computed_value=observation.computed_value,
        state=state,
        reasons=tuple(reasons),
    )


def run_regression_cases(rule: RegulationRule) -> tuple[str, ...]:
    """Return stable machine-readable failures for the rule's frozen examples."""
    failures = []
    for case in rule.test_cases:
        actual = _value_state(rule, case.computed_value)
        if actual != case.expected_state:
            failures.append(f"{case.name}:expected_{case.expected_state}:got_{actual}")
    return tuple(failures)
