"""Frozen role rule definitions with canonical JSON identity."""

import hashlib
import json
from collections.abc import Mapping
from types import MappingProxyType
from typing import Any

ROLE_RULE_VERSION = "role-v1"

ROLE_NAMES: Mapping[str, str] = MappingProxyType(
    {
        "emotion": "emotion_leader",
        "trend": "trend_leader",
        "core": "zhongjun",
        "follower": "genfeng",
        "catchup": "buZhang",
        "relay": "jieli",
    }
)
RESET_APPROVED_RULE_VERSIONS = frozenset()

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
    "operand_policy": "all_operands_must_be_finite_decimal",
    "operand_normalization": "Decimal34_before_arithmetic",
    "decimal_error_policy": (
        "unrepresentable_or_trapped_decimal_arithmetic -> AnalysisUnavailable"
    ),
    "decimal_median": {
        "sample": "nonempty",
        "ordering": "ascending",
        "odd": "middle_decimal",
        "even": "arithmetic_mean_of_middle_two_decimals",
        "arithmetic": "Decimal34",
    },
    "price_basis": "same_market_snapshot_adjusted_close",
    "amount_basis": "raw_currency",
    "amount_currency_policy": "single_currency_no_conversion",
    "topic_amount_share_policy": {
        "currency_metadata": {
            "security": "mandatory_nonempty",
            "eligible_members": "mandatory_nonempty_one_per_amount",
            "matching": "all_exactly_equal_to_security_currency",
            "conversion": "forbidden",
            "default": "forbidden",
        },
        "amounts": {
            "security": "finite_decimal_nonnegative",
            "eligible_members": "finite_decimal_nonnegative",
        },
        "eligible_set": "includes_subject_security",
        "denominator": "finite_and_strictly_positive",
        "result_range": "closed_[0,1]",
    },
    "divisor_policy": "finite_and_strictly_positive",
}

_COMMON_CONTRACT = {
    "snapshot_binding": {
        "trade_date": "market_snapshot.trade_date == topic_snapshot.trade_date",
        "source_grants_at_materialization": {
            "market_snapshot": {
                "local_storage": True,
                "local_display": True,
            },
            "topic_snapshot": {
                "local_storage": True,
                "local_display": True,
            },
        },
        "knowledge_cutoff": "min(market_snapshot.as_of,topic_snapshot.as_of)",
        "available_at_refilter": [
            "market_bars",
            "security_states",
            "topic_definitions",
            "topic_memberships",
        ],
        "available_at_condition": "available_at <= knowledge_cutoff",
    },
    "security_eligibility": {
        "topic_membership": "effective_on_trade_date",
        "market_pool": "declared_market_pool",
        "security_state": "effective_on_trade_date",
        "required_state_values": {
            "security_type": "common_stock",
            "is_st": False,
            "is_delisting": False,
        },
        "required_non_null_fields": [
            "security_type",
            "board_category",
            "is_delisting",
        ],
        "missing_required_field": "excluded_from_formal_role_pool",
        "board_category": "in_allowed_boards",
    },
    "exchange_policy": {
        "mode": "single_exchange",
        "member_exchange": "market_calendar.exchange",
        "cross_exchange_topic": "insufficient",
    },
    "listing_sessions": {
        "calendar": "selected_market_calendar",
        "count": "count(d where listing_date <= d <= trade_date)",
        "minimum": "minimum_listing_sessions",
        "incomplete_calendar": "insufficient",
    },
    "role_pool": {
        "minimum_valid_members": "minimum_topic_members",
        "valid_member": ("valid_and_known_on_trade_date_satisfying_all_role_inputs"),
        "below_minimum": "insufficient",
    },
    "missing_input_policy": [
        "do_not_shorten_window",
        "do_not_zero_fill",
        "do_not_reallocate_factor_priority",
    ],
    "benchmark_members": {
        "window_factors": {
            "applies_to": ["window_return", "maximum_drawdown", "return_median"],
            "members": (
                "intersection_continuously_valid_for_entire_window_"
                "and_known_by_cutoff_with_complete_prices"
            ),
        },
        "daily_topic_amount_share": (
            "members_valid_and_known_on_day_with_complete_coverage"
        ),
        "multi_day_topic_amount_share": (
            "arithmetic_mean_of_daily_shares_each_using_that_days_member_set"
        ),
        "observation_day_qualification_count": (
            "members_satisfying_all_inputs_for_role"
        ),
    },
    "ranking": {
        "numeric_values": "unrounded_decimal",
        "comparison_unit": "each_sort_key_declared_raw_unit",
        "cross_unit_comparison": "forbidden",
        "unit_conversion": "forbidden",
        "population": "all_computable_rows_in_one_global_ranking",
        "tie_definition": "identical_complete_evidence_tuple",
        "rank_method": "dense",
        "rank_start": 1,
        "security_id": "stable_display_order_within_tie_only",
        "positive_status_gate": {
            "statuses": ["candidate", "simplified_candidate"],
            "requires": [
                "candidate_conditions_met",
                "global_first_tie_group",
            ],
        },
    },
}

_ROLE_RULES = {
    "trend": {
        "capability": "ready",
        "allowed_result_statuses": [
            "candidate",
            "eligible_non_candidate",
            "excluded",
        ],
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
            {"factor": "relative60", "direction": "desc", "unit": "ratio"},
            {"factor": "relative20", "direction": "desc", "unit": "ratio"},
            {"factor": "mdd60", "direction": "asc", "unit": "ratio"},
        ],
    },
    "core": {
        "capability": "ready",
        "allowed_result_statuses": [
            "candidate",
            "eligible_non_candidate",
            "excluded",
        ],
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
            {"factor": "share5", "direction": "desc", "unit": "ratio"},
            {"factor": "avg20", "direction": "desc", "unit": "currency_amount"},
            {"factor": "mad20", "direction": "asc", "unit": "ratio"},
        ],
    },
    "emotion": {
        "capability": "simplified",
        "allowed_result_statuses": [
            "simplified_candidate",
            "eligible_non_candidate",
            "excluded",
        ],
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
            {
                "factor": "closing_limit_streak",
                "direction": "desc",
                "unit": "trading_sessions",
            },
            {"factor": "relative5", "direction": "desc", "unit": "ratio"},
            {"factor": "daily_topic_share", "direction": "desc", "unit": "ratio"},
        ],
    },
    "catchup": {
        "capability": "simplified",
        "allowed_result_statuses": [
            "simplified_candidate",
            "eligible_non_candidate",
            "excluded",
        ],
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
            {"factor": "current_excess", "direction": "desc", "unit": "ratio"},
            {"factor": "prior_lag", "direction": "desc", "unit": "ratio"},
            {"factor": "amount_change", "direction": "desc", "unit": "ratio"},
        ],
    },
    "follower": {
        "capability": "watch_only",
        "allowed_result_statuses": ["watch", "excluded"],
        "output_status": "watch",
        "required_inputs": {
            "reference_core": {
                "role": "trend",
                "status": "candidate",
                "tie_rank": 1,
                "cardinality": "exactly_one",
                "zero_candidates": "insufficient",
                "tied_candidates": "insufficient",
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
            {"factor": "return5", "direction": "desc", "unit": "ratio"},
            {"factor": "amount_change", "direction": "desc", "unit": "ratio"},
        ],
    },
    "relay": {
        "capability": "pending_validation",
        "allowed_result_statuses": ["pending_validation", "excluded"],
        "output_status": "pending_validation",
        "same_day_output": "pending_validation_only_no_candidate",
        "predecessor_identity": {
            "trade_session": "previous",
            "activity": "unique_active_run",
            "same_fields": [
                "rule_version",
                "visibility_mode",
                "market_benchmark_definition",
                "topic_identity",
            ],
        },
        "comparability": {
            "topic_identity": "continuous",
            "missing_predecessor": "insufficient",
            "topic_incomparable": "insufficient",
            "same_day_changes": {
                "topic_revision": "not_comparable",
                "rule_version": "not_comparable",
                "visibility_mode": "not_comparable",
                "market_benchmark": "not_comparable",
            },
            "membership_change": "record_separately",
        },
        "core_identity": {
            "role": "trend",
            "previous": {
                "status": "candidate",
                "tie_rank": 1,
                "cardinality": "exactly_one",
                "zero_candidates": "insufficient",
                "tied_candidates": "insufficient",
            },
            "current": {
                "status": "candidate",
                "tie_rank": 1,
                "cardinality": "exactly_one",
                "zero_candidates": "insufficient",
                "tied_candidates": "insufficient",
            },
            "condition": "previous_security_id != current_security_id",
        },
        "required_inputs": {
            "previous_trend_candidate": {
                "reference": "core_identity.previous",
                "uniqueness": "required",
            },
            "current_trend_candidate": {
                "reference": "core_identity.current",
                "uniqueness": "required",
            },
            "old_adjusted_prices": {
                "subject": "previous_security",
                "window": "T-5..T",
                "observations": 6,
                "coverage": "complete",
            },
            "new_adjusted_prices": {
                "subject": "current_security",
                "window": "T-5..T",
                "observations": 6,
                "coverage": "complete",
            },
            "topic_return_benchmark": {
                "member_set": "fixed_eligible_topic_member_intersection",
                "window": "T-5..T",
                "observations_per_member": 6,
                "coverage": "complete",
            },
            "topic_daily_median_return": {
                "member_set": "fixed_eligible_topic_member_intersection",
                "window": "T-1..T",
                "observations_per_member": 2,
                "coverage": "complete",
            },
            "availability": {
                "applies_to": "all_required_inputs",
                "condition": "available_at <= knowledge_cutoff",
            },
            "predecessor_comparability": {
                "required": True,
                "contracts": ["predecessor_identity", "comparability"],
            },
        },
        "factors": {
            "old_relative5": (
                "return(previous_security,T-5,T) - eligible_topic_median_return(T-5,T)"
            ),
            "new_relative5": (
                "return(current_security,T-5,T) - eligible_topic_median_return(T-5,T)"
            ),
            "topic_daily_median_return": "eligible_topic_median_return(T-1,T)",
        },
        "pending_conditions": [
            "old_relative5 <= 0",
            "new_relative5 > 0",
            "topic_daily_median_return > 0",
        ],
        "validation": {
            "window": "T+1..T+5",
            "return_endpoints": "T..T+5",
            "run_visibility": (
                "daily_visible_active_runs_with_same_rule_visibility_"
                "and_continuous_topic_identity"
            ),
            "write_policy": (
                "append_immutable_validation_without_updating_original_result"
            ),
            "outcomes": {
                "supported": "new_return > old_return and new_return > topic_median",
                "weakened": "new_return > old_return and new_return <= topic_median",
                "refuted": "new_return <= old_return",
                "unavailable": {
                    "when": "benchmark_members.denominator_policy.failure_conditions",
                    "scope": "entire_validation",
                },
            },
            "benchmark_members": {
                "baseline": {
                    "observation_session": "T",
                    "member_set": "all_topic_members_known_and_valid_at_T",
                    "eligibility_contract": "common_contract.security_eligibility",
                    "fixed_for_validation": True,
                },
                "validation_requirements": {
                    "applies_to": "every_baseline_member",
                    "identity_continuity": {
                        "window": "T..T+5",
                        "required": True,
                    },
                    "adjusted_prices": {
                        "window": "T..T+5",
                        "observations": 6,
                        "coverage": "complete",
                    },
                },
                "denominator_policy": {
                    "member_removal": "forbidden",
                    "failure_outcome": "unavailable",
                    "failure_conditions": [
                        "any_baseline_member_suspended",
                        "any_baseline_member_identity_discontinuous",
                        "topic_membership_changed",
                        "any_baseline_member_price_missing",
                        "topic_incomparable",
                        "window_incomplete",
                    ],
                },
            },
            "record_identity": "pending_result_id",
            "record_fields": [
                "window_start",
                "window_end",
                "old_return_raw",
                "new_return_raw",
                "topic_median_raw",
                "input_run_ids",
                "reasons",
            ],
            "chain": (
                "one_current_validation_chain_per_pending_result_"
                "with_append_only_corrections"
            ),
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
        "common_contract": _COMMON_CONTRACT,
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


ROLE_RULE_SHA256 = "dfc0405105f273c8e7a7a398bfd7f8f67e84c69551a6f08971ab84a6afd13d9c"
ROLE_TEST_RULE_SHA256 = (
    "0503f5d24b1a554dddd9ca7444b750440f5fe2041e82c5f7be361fe6de6b42ab"
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
