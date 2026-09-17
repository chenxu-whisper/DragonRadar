"""Frozen role rule definitions with canonical JSON identity."""

import hashlib
import json
from collections.abc import Mapping
from types import MappingProxyType
from typing import Any

ROLE_RULE_VERSION = "role-v1"

_DECIMAL_CONTEXT = {
    "precision": 34,
    "rounding": "ROUND_HALF_EVEN",
    "emin": -999999,
    "emax": 999999,
    "capitals": 1,
    "clamp": 0,
    "traps": ["DivisionByZero", "InvalidOperation", "Overflow"],
}

_FORMULA_CONTRACTS = {
    "adjusted_price": "close(d) * adjustment_factor(d)",
    "return": "adjusted_price(b) / adjusted_price(a) - 1",
    "relative_return": "security_return(a,b) - benchmark_return(a,b)",
    "maximum_drawdown": (
        "max(1 - adjusted_price(d) / "
        "max(adjusted_price(k) for k in [a,d]) for d in [a,b])"
    ),
    "topic_amount_share": (
        "amount(i,d) / sum(amount(member,d) for member in eligible_topic_members(d))"
    ),
    "mean": "sum(values) / count(values)",
    "mean_absolute_deviation_rate": ("mean(abs(value - mean(values))) / mean(values)"),
    "numeric_type": "Decimal",
    "price_basis": "same_market_snapshot_adjusted_close",
    "amount_basis": "raw_currency",
    "amount_currency_policy": "single_currency_no_conversion",
    "divisor_policy": "finite_and_strictly_positive",
}

_ROLE_RULES = {
    "trend": {
        "capability": "ready",
        "required_inputs": {
            "adjusted_prices": {"window": "T-60..T", "observations": 61},
            "market_benchmark": {
                "windows": ["T-60..T", "T-20..T"],
                "definition": "eligible_declared_market_pool_return_median",
            },
            "topic_benchmark": {
                "window": "T-60..T",
                "definition": "eligible_topic_member_mdd_median",
            },
        },
        "factors": {
            "relative60": ("return(security,T-60,T) - market_median_return(T-60,T)"),
            "relative20": ("return(security,T-20,T) - market_median_return(T-20,T)"),
            "mdd60": "maximum_drawdown(security,T-60,T)",
        },
        "candidate_conditions": [
            "relative60 > 0",
            "relative20 > 0",
            "mdd60 <= eligible_topic_median(mdd60)",
        ],
        "sort": [
            {"factor": "relative60", "direction": "desc"},
            {"factor": "relative20", "direction": "desc"},
            {"factor": "mdd60", "direction": "asc"},
        ],
    },
    "core": {
        "capability": "ready",
        "required_inputs": {
            "daily_amounts": {"window": "T-19..T", "observations": 20},
            "daily_topic_amount_coverage": {
                "window": "T-19..T",
                "status": "complete",
            },
        },
        "factors": {
            "share5": "mean(topic_amount_share(security,d) for d in T-4..T)",
            "avg20": "mean(amount[T-19..T])",
            "mad20": "mean_absolute_deviation_rate(amount[T-19..T])",
        },
        "candidate_conditions": [
            "share5 >= eligible_topic_median(share5)",
            "avg20 >= eligible_topic_median(avg20)",
        ],
        "sort": [
            {"factor": "share5", "direction": "desc"},
            {"factor": "avg20", "direction": "desc"},
            {"factor": "mad20", "direction": "asc"},
        ],
    },
    "emotion": {
        "capability": "simplified",
        "rule_id": "emotion-daily-v1",
        "required_inputs": {
            "daily_limit_data": {"window": "T"},
            "closing_limit_streak": {"window": "through_T"},
            "adjusted_prices": {"window": "T-5..T", "observations": 6},
            "daily_amount": {"window": "T"},
        },
        "factors": {
            "relative5": (
                "return(security,T-5,T) - eligible_topic_median_return(T-5,T)"
            ),
            "daily_topic_share": "topic_amount_share(security,T)",
        },
        "candidate_conditions": [
            "close(T) == limit_up_price(T)",
            "closing_limit_streak >= 1",
        ],
        "sort": [
            {"factor": "closing_limit_streak", "direction": "desc"},
            {"factor": "relative5", "direction": "desc"},
            {"factor": "daily_topic_share", "direction": "desc"},
        ],
    },
    "catchup": {
        "capability": "simplified",
        "rule_id": "catchup-daily-v1",
        "required_inputs": {
            "adjusted_prices": {"window": "T-25..T", "observations": 26},
            "daily_amounts": {"window": "T-24..T", "observations": 25},
            "topic_return_medians": {
                "windows": ["T-25..T-5", "T-5..T"],
            },
        },
        "factors": {
            "prior_lag": ("topic_median_return(T-25,T-5) - return(security,T-25,T-5)"),
            "current_excess": ("return(security,T-5,T) - topic_median_return(T-5,T)"),
            "amount_change": ("mean(amount[T-4..T]) / mean(amount[T-24..T-5]) - 1"),
        },
        "candidate_conditions": [
            "prior_lag > 0",
            "current_excess > 0",
            "amount_change > 0",
        ],
        "sort": [
            {"factor": "current_excess", "direction": "desc"},
            {"factor": "prior_lag", "direction": "desc"},
            {"factor": "amount_change", "direction": "desc"},
        ],
    },
    "follower": {
        "capability": "watch_only",
        "output_status": "watch",
        "required_inputs": {
            "reference_core": {
                "role": "trend",
                "tie_rank": 1,
                "cardinality": "exactly_one",
            },
            "adjusted_prices": {"window": "T-5..T", "observations": 6},
            "daily_amounts": {"window": "T-9..T", "observations": 10},
        },
        "factors": {
            "return5": "return(security,T-5,T)",
            "amount_change": ("mean(amount[T-4..T]) / mean(amount[T-9..T-5]) - 1"),
        },
        "candidate_conditions": [
            "return5 > 0",
            "return5 < reference_core_return5",
        ],
        "sort": [
            {"factor": "return5", "direction": "desc"},
            {"factor": "amount_change", "direction": "desc"},
        ],
    },
    "relay": {
        "capability": "pending_validation",
        "predecessor_identity": {
            "trade_session": "previous",
            "activity": "unique_active_run",
            "same_fields": [
                "rule_version",
                "visibility_mode",
                "market_benchmark",
                "topic_identity",
            ],
        },
        "core_identity": {
            "role": "trend",
            "previous": {"tie_rank": 1, "cardinality": "exactly_one"},
            "current": {"tie_rank": 1, "cardinality": "exactly_one"},
            "condition": "previous_security_id != current_security_id",
        },
    },
}


def _raw_rule_definition(
    version: str,
    *,
    allowed_boards: list[str],
    production_activatable: bool,
    minimum_listing_sessions: int,
    minimum_topic_members: int,
) -> dict[str, Any]:
    return {
        "rule_version": version,
        "allowed_boards": allowed_boards,
        "production_activatable": production_activatable,
        "decimal_context": _DECIMAL_CONTEXT,
        "minimum_listing_sessions": minimum_listing_sessions,
        "minimum_topic_members": minimum_topic_members,
        "formula_contracts": _FORMULA_CONTRACTS,
        "roles": _ROLE_RULES,
    }


_RAW_RULE_DEFINITIONS: dict[str, dict[str, Any]] = {
    "role-v1": _raw_rule_definition(
        "role-v1",
        allowed_boards=["sh_main", "sz_main"],
        production_activatable=True,
        minimum_listing_sessions=120,
        minimum_topic_members=5,
    ),
    "role-test-v1": _raw_rule_definition(
        "role-test-v1",
        allowed_boards=["synthetic_test"],
        production_activatable=False,
        minimum_listing_sessions=1,
        minimum_topic_members=1,
    ),
}


def _freeze(value: Any) -> Any:
    if isinstance(value, dict):
        return MappingProxyType({key: _freeze(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(_freeze(item) for item in value)
    return value


RULE_DEFINITIONS: Mapping[str, Mapping[str, Any]] = MappingProxyType(
    {
        version: _freeze(definition)
        for version, definition in _RAW_RULE_DEFINITIONS.items()
    }
)
del _RAW_RULE_DEFINITIONS


def canonical_rule_json(version: str) -> str:
    """Return the exact canonical JSON archived with each role run."""
    try:
        definition = RULE_DEFINITIONS[version]
    except KeyError as exc:
        raise ValueError(f"unknown role rule version: {version}") from exc

    def mutable_json(value: Any) -> Any:
        if isinstance(value, Mapping):
            return {key: mutable_json(item) for key, item in value.items()}
        if isinstance(value, tuple):
            return [mutable_json(item) for item in value]
        return value

    return json.dumps(
        mutable_json(definition),
        ensure_ascii=True,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )


ROLE_RULE_SHA256 = "cf8e5b5b9a944eec801aa95b6694e28dba9e7576e2077ff9802b5e9551ec6714"
ROLE_TEST_RULE_SHA256 = (
    "90948aeb3441fcd56a137585eec14c0fdcf9629a686fbb48c95833a2f8f92170"
)
_RULE_SHA256 = MappingProxyType(
    {
        "role-v1": ROLE_RULE_SHA256,
        "role-test-v1": ROLE_TEST_RULE_SHA256,
    }
)


def validate_rule_definition(version: str, canonical_json: str) -> None:
    """Reject content drift for a known immutable rule version."""
    try:
        expected = _RULE_SHA256[version]
    except KeyError as exc:
        raise RuntimeError(f"unknown role rule version: {version}") from exc
    actual = hashlib.sha256(canonical_json.encode("utf-8")).hexdigest()
    if actual != expected:
        raise RuntimeError(
            f"role rule digest mismatch for {version}: {actual} != {expected}"
        )


def validate_frozen_rule_definitions() -> None:
    """Reject digest drift in every registered immutable rule definition."""
    for version in RULE_DEFINITIONS:
        validate_rule_definition(version, canonical_rule_json(version))


validate_frozen_rule_definitions()
