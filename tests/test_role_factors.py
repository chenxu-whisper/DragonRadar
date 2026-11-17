import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal, DecimalException
from functools import partial

import pytest
from factories import batch
from pydantic import ValidationError
from topic_factories import topic_batch

from dragon_radar.roles.factors import (
    AnalysisUnavailable,
    adjusted_price,
    decimal_mean,
    decimal_median,
    knowledge_cutoff,
    maximum_drawdown,
    mean_absolute_deviation_rate,
    period_return,
    relative_return,
    topic_amount_share,
)
from dragon_radar.roles.models import (
    ALLOWED_RESULT_STATUSES,
    ROLE_CAPABILITIES,
    RankedCandidate,
    RoleCandidate,
    RoleContext,
    RoleEvaluation,
    RoleTopicStatus,
)
from dragon_radar.roles.rules import (
    ROLE_RULE_SHA256,
    ROLE_RULE_VERSION,
    ROLE_TEST_RULE_SHA256,
    RULE_DEFINITIONS,
    canonical_rule_json,
    validate_rule_definition,
)


def test_adjusted_price_and_period_return_use_decimal34_half_even():
    start = adjusted_price(Decimal("3"), Decimal("1.1"))
    end = adjusted_price(Decimal("4"), Decimal("1.2"))

    assert start == Decimal("3.3")
    assert end == Decimal("4.8")
    assert period_return(start, end) == Decimal("0.454545454545454545454545454545455")


def test_adjusted_price_normalizes_each_operand_before_arithmetic():
    assert adjusted_price(
        Decimal("12345678901234567890123456789012345"),
        Decimal("9.8765432109876543210987654321098765"),
    ) == Decimal("1.219326311370217952261850327338667E+35")


def test_period_return_normalizes_each_operand_before_arithmetic():
    assert period_return(
        Decimal("12345678901234567890123456789012345"),
        Decimal("9876543210987654321098765432109876.5"),
    ) == Decimal("-0.1999999927099999336609993963150942")


def test_relative_return_subtracts_exact_decimal_returns():
    assert relative_return(Decimal("0.125"), Decimal("0.075")) == Decimal("0.050")


def test_relative_return_normalizes_each_operand_before_arithmetic():
    assert relative_return(
        Decimal("1.00000000000000000000000000000000005"),
        Decimal("1"),
    ) == Decimal("0E-33")


def test_decimal_mean_normalizes_each_sample_value_before_arithmetic():
    assert decimal_mean(
        tuple(
            map(
                Decimal,
                (
                    "46.894885471242686919379003172093007",
                    "3039355039044855267073712853.6843262",
                    "9958.5322555999020673426137355845753",
                    "535884462536398567.18760214960406783",
                ),
            )
        )
    ) == Decimal("759838759895184932402530356.5747672")


@pytest.mark.parametrize(
    ("values", "expected"),
    [
        ((Decimal("3"), Decimal("1"), Decimal("2")), Decimal("2")),
        ((Decimal("1"), Decimal("3")), Decimal("2")),
    ],
)
def test_decimal_median_is_exact_for_odd_and_even_samples(values, expected):
    assert decimal_median(values) == expected


def test_decimal_median_normalizes_values_before_sorting():
    assert decimal_median(
        tuple(
            map(
                Decimal,
                (
                    "2.00000000000000000000000000000000005",
                    "1.00000000000000000000000000000000005",
                    "1.00000000000000000000000000000000004",
                ),
            )
        )
    ) == Decimal("1.000000000000000000000000000000000")


def test_maximum_drawdown_uses_each_running_peak():
    assert maximum_drawdown(
        tuple(map(Decimal, ("100", "125", "100", "150", "90")))
    ) == Decimal("0.4")


def test_maximum_drawdown_normalizes_prices_before_arithmetic():
    assert maximum_drawdown(
        tuple(
            map(
                Decimal,
                (
                    "12345678901234567890123456789012345",
                    "9876543210987654321098765432109876.5",
                ),
            )
        )
    ) == Decimal("0.1999999927099999336609993963150942")


def test_topic_amount_share_and_average_share_are_exact():
    shares = (
        topic_amount_share(
            Decimal("25"),
            (Decimal("25"), Decimal("75")),
            currency="CNY",
            eligible_currencies=("CNY", "CNY"),
        ),
        topic_amount_share(
            Decimal("30"),
            (Decimal("30"), Decimal("70")),
            currency="CNY",
            eligible_currencies=("CNY", "CNY"),
        ),
    )

    assert shares == (Decimal("0.25"), Decimal("0.3"))
    assert decimal_mean(shares) == Decimal("0.275")


def test_topic_amount_share_normalizes_single_high_precision_member():
    amount = Decimal("12345678901234567890123456789012345")

    assert topic_amount_share(
        amount,
        (amount,),
        currency="CNY",
        eligible_currencies=("CNY",),
    ) == Decimal("1")


def test_topic_amount_share_keeps_multi_member_high_precision_math_decimal():
    amount = Decimal("12345678901234567890123456789012345")

    share = topic_amount_share(
        amount,
        (
            amount,
            Decimal("12345678901234567890123456789012344"),
            Decimal("1"),
        ),
        currency="CNY",
        eligible_currencies=("CNY", "CNY", "CNY"),
    )

    assert isinstance(share, Decimal)
    assert share == Decimal("0.5")


def test_topic_amount_share_is_closed_between_zero_and_one():
    common = {
        "eligible_amounts": (Decimal("25"), Decimal("75")),
        "currency": "CNY",
        "eligible_currencies": ("CNY", "CNY"),
    }

    assert topic_amount_share(Decimal("0"), **common) == Decimal("0")
    assert topic_amount_share(Decimal("100"), **common) == Decimal("1")
    with pytest.raises(AnalysisUnavailable, match="eligible"):
        topic_amount_share(Decimal("200"), **common)


def test_mean_absolute_deviation_rate_uses_decimal_mean():
    assert mean_absolute_deviation_rate(
        tuple(map(Decimal, ("10", "20", "30")))
    ) == Decimal("0.3333333333333333333333333333333334")


def test_mean_absolute_deviation_rate_normalizes_each_sample_value():
    assert mean_absolute_deviation_rate(
        tuple(
            map(
                Decimal,
                (
                    "1.9945971886712321777165847221885572",
                    "161099527986061665140.56328022854684",
                    "43120543752588642202.385024143078074",
                ),
            )
        )
    ) == Decimal("0.9110384689208679541576464349380312")


@pytest.mark.parametrize(
    ("function", "args"),
    [
        (period_return, (Decimal("0"), Decimal("1"))),
        (
            partial(
                topic_amount_share,
                Decimal("0"),
                (Decimal("0"), Decimal("0")),
                currency="CNY",
                eligible_currencies=("CNY", "CNY"),
            ),
            (),
        ),
        (mean_absolute_deviation_rate, ((Decimal("0"), Decimal("0")),)),
    ],
)
def test_formula_primitives_reject_nonpositive_denominators(function, args):
    with pytest.raises(AnalysisUnavailable, match="positive"):
        function(*args)


@pytest.mark.parametrize("value", [Decimal("NaN"), Decimal("Infinity")])
def test_formula_primitives_reject_nonfinite_values(value):
    with pytest.raises(AnalysisUnavailable, match="finite"):
        adjusted_price(value, Decimal("1"))


@pytest.mark.parametrize(
    ("operation", "function", "args"),
    [
        (
            "adjusted_price",
            adjusted_price,
            (Decimal("1e999999"), Decimal("10")),
        ),
        (
            "period_return",
            period_return,
            (Decimal("1e-999999"), Decimal("1e999999")),
        ),
        (
            "decimal_mean",
            decimal_mean,
            ((Decimal("1e1000000"),),),
        ),
        (
            "decimal_median",
            decimal_median,
            ((Decimal("1e1000000"),),),
        ),
        (
            "relative_return",
            relative_return,
            (
                Decimal("9.999999999999999999999999999999999e999999"),
                Decimal("-9.999999999999999999999999999999999e999999"),
            ),
        ),
        (
            "maximum_drawdown",
            maximum_drawdown,
            ((Decimal("1e1000000"),),),
        ),
        (
            "mean_absolute_deviation_rate",
            mean_absolute_deviation_rate,
            ((Decimal("1e1000000"),),),
        ),
        (
            "topic_amount_share",
            partial(
                topic_amount_share,
                Decimal("1e1000000"),
                (Decimal("1e1000000"),),
                currency="CNY",
                eligible_currencies=("CNY",),
            ),
            (),
        ),
    ],
)
def test_decimal_factor_errors_are_analysis_unavailable_with_operation_context(
    operation,
    function,
    args,
):
    with pytest.raises(AnalysisUnavailable, match=operation) as caught:
        function(*args)

    assert isinstance(caught.value.__cause__, DecimalException)


def test_topic_share_rejects_currency_mismatch():
    with pytest.raises(AnalysisUnavailable, match="currency"):
        topic_amount_share(
            Decimal("25"),
            (Decimal("25"), Decimal("75")),
            currency="CNY",
            eligible_currencies=("CNY", "USD"),
        )


def test_topic_share_rejects_incomplete_currency_metadata():
    with pytest.raises(AnalysisUnavailable, match="currency"):
        topic_amount_share(
            Decimal("25"),
            (Decimal("25"), Decimal("75")),
            currency="CNY",
            eligible_currencies=("CNY",),
        )


def test_topic_share_rejects_missing_currency_metadata():
    with pytest.raises(AnalysisUnavailable, match="currency"):
        topic_amount_share(Decimal("25"), (Decimal("25"), Decimal("75")))


@pytest.mark.parametrize(
    ("currency", "eligible_currencies"),
    [
        ("", ("CNY", "CNY")),
        ("CNY", ("CNY", "")),
    ],
)
def test_topic_share_rejects_empty_currency_metadata(
    currency,
    eligible_currencies,
):
    with pytest.raises(AnalysisUnavailable, match="currency"):
        topic_amount_share(
            Decimal("25"),
            (Decimal("25"), Decimal("75")),
            currency=currency,
            eligible_currencies=eligible_currencies,
        )


def test_knowledge_cutoff_is_earlier_snapshot_time_in_utc():
    market_as_of = datetime(2026, 9, 17, 8, tzinfo=UTC)
    topic_as_of = market_as_of - timedelta(minutes=5)

    assert knowledge_cutoff(market_as_of, topic_as_of) == topic_as_of


def test_knowledge_cutoff_rejects_naive_datetimes():
    with pytest.raises(ValueError, match="timezone"):
        knowledge_cutoff(datetime(2026, 9, 17), datetime(2026, 9, 17, tzinfo=UTC))


EXPECTED_DECIMAL_CONTEXT = {
    "precision": 34,
    "rounding": "ROUND_HALF_EVEN",
    "emin": -999999,
    "emax": 999999,
    "capitals": 1,
    "clamp": 0,
    "traps": ["DivisionByZero", "InvalidOperation", "Overflow"],
}

EXPECTED_FORMULA_CONTRACTS = {
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

EXPECTED_COMMON_CONTRACT = {
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

EXPECTED_ROLE_RULES = {
    "trend": {
        "capability": "ready",
        "allowed_result_statuses": [
            "candidate",
            "eligible_non_candidate",
            "excluded",
        ],
        "required_inputs": {
            "adjusted_prices": {
                "window": "T-60..T",
                "observations": 61,
            },
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
            "daily_amounts": {
                "window": "T-19..T",
                "observations": 20,
            },
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
            "adjusted_prices": {
                "window": "T-5..T",
                "observations": 6,
            },
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
            "adjusted_prices": {
                "window": "T-25..T",
                "observations": 26,
            },
            "daily_amounts": {
                "window": "T-24..T",
                "observations": 25,
            },
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
            "adjusted_prices": {
                "window": "T-5..T",
                "observations": 6,
            },
            "daily_amounts": {
                "window": "T-9..T",
                "observations": 10,
            },
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


def test_common_eligibility_benchmark_and_ranking_contract_is_canonical():
    production = json.loads(canonical_rule_json("role-v1"))
    test = json.loads(canonical_rule_json("role-test-v1"))

    assert production["common_contract"] == EXPECTED_COMMON_CONTRACT
    assert test["common_contract"] == EXPECTED_COMMON_CONTRACT


def test_formula_policies_are_complete_canonical_literals():
    formula_contracts = json.loads(canonical_rule_json("role-v1"))["formula_contracts"]

    assert formula_contracts == EXPECTED_FORMULA_CONTRACTS


def test_every_role_sort_key_declares_its_exact_raw_unit():
    roles = json.loads(canonical_rule_json("role-v1"))["roles"]
    expected_sorts = {
        role: definition["sort"]
        for role, definition in EXPECTED_ROLE_RULES.items()
        if "sort" in definition
    }

    assert {
        role: definition["sort"]
        for role, definition in roles.items()
        if "sort" in definition
    } == expected_sorts
    assert "sort" not in roles["relay"]


def test_relay_required_inputs_are_complete_canonical_literals():
    relay = json.loads(canonical_rule_json("role-v1"))["roles"]["relay"]

    assert relay["required_inputs"] == EXPECTED_ROLE_RULES["relay"]["required_inputs"]


def test_relay_pending_and_future_validation_contract_is_canonical():
    relay = json.loads(canonical_rule_json("role-v1"))["roles"]["relay"]

    assert relay == EXPECTED_ROLE_RULES["relay"]


def test_follower_and_relay_core_identity_requires_one_rank_one_candidate():
    roles = json.loads(canonical_rule_json("role-v1"))["roles"]

    assert (
        roles["follower"]["required_inputs"]["reference_core"]
        == (EXPECTED_ROLE_RULES["follower"]["required_inputs"]["reference_core"])
    )
    assert (
        roles["relay"]["core_identity"] == EXPECTED_ROLE_RULES["relay"]["core_identity"]
    )


def test_rule_definitions_are_complete_canonical_and_immutable():
    assert ROLE_RULE_VERSION == "role-v1"
    production = json.loads(canonical_rule_json("role-v1"))
    test = json.loads(canonical_rule_json("role-test-v1"))

    assert production == {
        "rule_version": "role-v1",
        "allowed_boards": ["sh_main", "sz_main"],
        "production_activatable": True,
        "decimal_context": EXPECTED_DECIMAL_CONTEXT,
        "minimum_listing_sessions": 120,
        "minimum_topic_members": 5,
        "formula_contracts": EXPECTED_FORMULA_CONTRACTS,
        "common_contract": EXPECTED_COMMON_CONTRACT,
        "roles": EXPECTED_ROLE_RULES,
    }
    assert test == {
        **production,
        "rule_version": "role-test-v1",
        "allowed_boards": ["synthetic_test"],
        "production_activatable": False,
        "minimum_listing_sessions": 1,
        "minimum_topic_members": 1,
    }
    with pytest.raises(TypeError):
        RULE_DEFINITIONS["role-v1"]["allowed_boards"] = ("synthetic_test",)
    with pytest.raises(TypeError):
        RULE_DEFINITIONS["role-v1"]["common_contract"]["ranking"]["rank_start"] = 2


def test_canonical_allowed_result_statuses_match_role_models():
    canonical_roles = json.loads(canonical_rule_json("role-v1"))["roles"]

    assert {
        role: frozenset(definition["allowed_result_statuses"])
        for role, definition in canonical_roles.items()
    } == dict(ALLOWED_RESULT_STATUSES)


def test_rule_definition_digests_are_locked_and_detect_behavior_drift():
    assert (
        ROLE_RULE_SHA256
        == "dfc0405105f273c8e7a7a398bfd7f8f67e84c69551a6f08971ab84a6afd13d9c"
    )
    assert (
        ROLE_TEST_RULE_SHA256
        == "0503f5d24b1a554dddd9ca7444b750440f5fe2041e82c5f7be361fe6de6b42ab"
    )
    validate_rule_definition("role-v1", canonical_rule_json("role-v1"))
    validate_rule_definition("role-test-v1", canonical_rule_json("role-test-v1"))

    changed = json.loads(canonical_rule_json("role-v1"))
    changed["minimum_topic_members"] += 1
    with pytest.raises(RuntimeError, match="digest"):
        validate_rule_definition(
            "role-v1",
            json.dumps(changed, sort_keys=True, separators=(",", ":")),
        )


def test_all_public_role_models_are_strict_frozen_contracts():
    for model in (
        RoleContext,
        RoleCandidate,
        RankedCandidate,
        RoleTopicStatus,
        RoleEvaluation,
    ):
        assert model.model_config["strict"] is True
        assert model.model_config["frozen"] is True
        assert model.model_config["extra"] == "forbid"
        assert model.model_config["allow_inf_nan"] is False

    context = RoleContext(market=batch(), topic=topic_batch())
    status = RoleTopicStatus(
        topic_id="topic-1",
        role="trend",
        capability="ready",
        status="ready",
        expected=1,
        valid=1,
        legal_excluded=0,
        missing=0,
    )
    candidate = RankedCandidate(
        topic_id="topic-1",
        security_id="600000.SH",
        role="trend",
        status="candidate",
        factors=(Decimal("0.1"),),
        tie_rank=1,
    )

    assert context.market == batch()
    assert context.topic == topic_batch()
    assert RoleEvaluation(topic_status=status, candidates=(candidate,)).candidates == (
        candidate,
    )


def test_role_context_rejects_mismatched_snapshot_trade_dates():
    market = batch()
    topic = topic_batch()
    mismatched_topic = topic.model_copy(
        update={"trade_date": topic.trade_date - timedelta(days=1)}
    )

    with pytest.raises(ValidationError, match="trade_date"):
        RoleContext(market=market, topic=mismatched_topic)


def test_role_topic_status_accepts_complete_count_partition():
    status = RoleTopicStatus(
        topic_id="topic-1",
        role="trend",
        capability="ready",
        status="ready",
        expected=3,
        valid=1,
        legal_excluded=1,
        missing=1,
    )

    assert status.expected == status.valid + status.legal_excluded + status.missing


def test_role_topic_status_rejects_impossible_count_partition():
    with pytest.raises(ValidationError, match="expected"):
        RoleTopicStatus(
            topic_id="topic-1",
            role="trend",
            capability="ready",
            status="ready",
            expected=2,
            valid=1,
            legal_excluded=1,
            missing=1,
        )


@pytest.mark.parametrize(
    ("role", "conflicting_capability"),
    [
        ("trend", "simplified"),
        ("core", "watch_only"),
        ("emotion", "ready"),
        ("catchup", "pending_validation"),
        ("follower", "simplified"),
        ("relay", "ready"),
    ],
)
def test_role_topic_status_capability_matches_canonical_role_rule(
    role,
    conflicting_capability,
):
    canonical_capability = json.loads(canonical_rule_json("role-v1"))["roles"][role][
        "capability"
    ]
    assert ROLE_CAPABILITIES[role] == canonical_capability
    values = {
        "topic_id": "topic-1",
        "role": role,
        "status": canonical_capability,
        "expected": 1,
        "valid": 1,
        "legal_excluded": 0,
        "missing": 0,
    }

    assert (
        RoleTopicStatus(capability=canonical_capability, **values).capability
        == canonical_capability
    )
    with pytest.raises(ValidationError, match="capability"):
        RoleTopicStatus(capability=conflicting_capability, **values)


@pytest.mark.parametrize(
    ("role", "canonical_status"),
    tuple(ROLE_CAPABILITIES.items()),
)
def test_role_topic_status_accepts_only_canonical_success_status(
    role,
    canonical_status,
):
    values = {
        "topic_id": "topic-1",
        "role": role,
        "capability": canonical_status,
        "expected": 1,
        "valid": 1,
        "legal_excluded": 0,
        "missing": 0,
    }

    assert RoleTopicStatus(status=canonical_status, **values).status == canonical_status
    for generic_status in ("insufficient", "no_candidate"):
        assert RoleTopicStatus(status=generic_status, **values).status == generic_status


@pytest.mark.parametrize(
    ("role", "conflicting_status"),
    [
        ("trend", "simplified"),
        ("core", "watch_only"),
        ("emotion", "ready"),
        ("catchup", "pending_validation"),
        ("follower", "simplified"),
        ("relay", "ready"),
    ],
)
def test_role_topic_status_rejects_success_status_for_another_capability(
    role,
    conflicting_status,
):
    with pytest.raises(ValidationError, match="status"):
        RoleTopicStatus(
            topic_id="topic-1",
            role=role,
            capability=ROLE_CAPABILITIES[role],
            status=conflicting_status,
            expected=1,
            valid=1,
            legal_excluded=0,
            missing=0,
        )


def test_role_evaluation_allows_sparse_no_candidate_results():
    status = RoleTopicStatus(
        topic_id="topic-1",
        role="trend",
        capability="ready",
        status="no_candidate",
        expected=1,
        valid=1,
        legal_excluded=0,
        missing=0,
    )

    assert RoleEvaluation(topic_status=status).candidates == ()


def test_role_topic_status_rejects_no_candidate_without_valid_members():
    with pytest.raises(ValidationError, match="no_candidate"):
        RoleTopicStatus(
            topic_id="topic-1",
            role="trend",
            capability="ready",
            status="no_candidate",
            expected=0,
            valid=0,
            legal_excluded=0,
            missing=0,
        )


def test_role_topic_status_allows_insufficient_without_valid_members():
    status = RoleTopicStatus(
        topic_id="topic-1",
        role="trend",
        capability="ready",
        status="insufficient",
        expected=1,
        valid=0,
        legal_excluded=0,
        missing=1,
    )

    assert status.valid == 0


def _role_topic_status(
    role,
    status,
    *,
    valid=1,
    legal_excluded=0,
    missing=0,
):
    return RoleTopicStatus(
        topic_id="topic-1",
        role=role,
        capability=ROLE_CAPABILITIES[role],
        status=status,
        expected=valid + legal_excluded + missing,
        valid=valid,
        legal_excluded=legal_excluded,
        missing=missing,
    )


def _ranked_role_result(role, status, security_id="600000.SH"):
    return RankedCandidate(
        topic_id="topic-1",
        security_id=security_id,
        role=role,
        status=status,
        factors=(),
        tie_rank=1,
    )


@pytest.mark.parametrize(
    ("role", "topic_status", "positive_status", "auxiliary_status"),
    [
        ("trend", "ready", "candidate", "eligible_non_candidate"),
        ("core", "ready", "candidate", "excluded"),
        ("emotion", "simplified", "simplified_candidate", "eligible_non_candidate"),
        ("catchup", "simplified", "simplified_candidate", "excluded"),
        ("follower", "watch_only", "watch", "excluded"),
        ("relay", "pending_validation", "pending_validation", "excluded"),
    ],
)
def test_role_evaluation_success_requires_matching_positive_output(
    role,
    topic_status,
    positive_status,
    auxiliary_status,
):
    excluded_rows = int(auxiliary_status == "excluded")
    status = _role_topic_status(
        role,
        topic_status,
        valid=2 - excluded_rows,
        legal_excluded=excluded_rows,
    )
    positive = _ranked_role_result(role, positive_status)
    auxiliary = _ranked_role_result(role, auxiliary_status, "600001.SH")

    evaluation = RoleEvaluation(
        topic_status=status,
        candidates=(positive, auxiliary),
    )

    assert evaluation.candidates == (positive, auxiliary)
    with pytest.raises(ValidationError, match="positive"):
        RoleEvaluation(topic_status=status, candidates=(auxiliary,))
    with pytest.raises(ValidationError, match="positive"):
        RoleEvaluation(topic_status=status)


@pytest.mark.parametrize("valid", [1, 2])
def test_role_evaluation_allows_sparse_results_up_to_valid_count(valid):
    status = _role_topic_status("trend", "ready", valid=valid)
    candidate = _ranked_role_result("trend", "candidate")

    assert RoleEvaluation(
        topic_status=status,
        candidates=(candidate,),
    ).candidates == (candidate,)


@pytest.mark.parametrize(
    ("role", "topic_status", "result_status"),
    [
        ("trend", "ready", "candidate"),
        ("emotion", "simplified", "simplified_candidate"),
        ("trend", "no_candidate", "eligible_non_candidate"),
        ("follower", "watch_only", "watch"),
        ("relay", "pending_validation", "pending_validation"),
    ],
)
def test_role_evaluation_rejects_calculable_result_when_valid_is_zero(
    role,
    topic_status,
    result_status,
):
    with pytest.raises(ValidationError, match="valid"):
        RoleEvaluation(
            topic_status=_role_topic_status(role, topic_status, valid=0),
            candidates=(_ranked_role_result(role, result_status),),
        )


def test_role_evaluation_rejects_more_calculable_results_than_valid_members():
    status = _role_topic_status("trend", "ready", valid=1)
    candidate = _ranked_role_result("trend", "candidate")
    non_candidate = _ranked_role_result(
        "trend",
        "eligible_non_candidate",
        "600001.SH",
    )

    with pytest.raises(ValidationError, match="valid"):
        RoleEvaluation(
            topic_status=status,
            candidates=(candidate, non_candidate),
        )


def test_role_evaluation_rejects_excluded_rows_over_legal_excluded_count():
    status = _role_topic_status("trend", "ready", valid=1, missing=1)
    candidate = _ranked_role_result("trend", "candidate")
    excluded = _ranked_role_result("trend", "excluded", "600001.SH")

    with pytest.raises(ValidationError, match="legal_excluded"):
        RoleEvaluation(
            topic_status=status,
            candidates=(candidate, excluded),
        )


def test_role_evaluation_rejects_result_rows_over_expected_count():
    status = _role_topic_status("trend", "ready", valid=1, legal_excluded=1)
    candidate = _ranked_role_result("trend", "candidate")
    first_excluded = _ranked_role_result("trend", "excluded", "600001.SH")
    second_excluded = _ranked_role_result("trend", "excluded", "600002.SH")

    with pytest.raises(ValidationError, match="expected"):
        RoleEvaluation(
            topic_status=status,
            candidates=(candidate, first_excluded, second_excluded),
        )


def test_role_evaluation_allows_sparse_valid_and_excluded_rows():
    status = _role_topic_status(
        "trend",
        "ready",
        valid=2,
        legal_excluded=2,
        missing=1,
    )
    candidate = _ranked_role_result("trend", "candidate")
    excluded = _ranked_role_result("trend", "excluded", "600001.SH")

    assert RoleEvaluation(
        topic_status=status,
        candidates=(candidate, excluded),
    ).candidates == (candidate, excluded)


def test_role_evaluation_rejects_duplicate_security_ids():
    status = _role_topic_status("trend", "ready", valid=2)
    candidate = _ranked_role_result("trend", "candidate")
    duplicate = _ranked_role_result(
        "trend",
        "eligible_non_candidate",
        candidate.security_id,
    )

    with pytest.raises(ValidationError, match="security_id"):
        RoleEvaluation(
            topic_status=status,
            candidates=(candidate, duplicate),
        )


@pytest.mark.parametrize(
    ("role", "positive_status"),
    [
        ("trend", "candidate"),
        ("core", "candidate"),
        ("emotion", "simplified_candidate"),
        ("catchup", "simplified_candidate"),
        ("follower", "watch"),
        ("relay", "pending_validation"),
    ],
)
def test_role_evaluation_no_candidate_rejects_positive_output(
    role,
    positive_status,
):
    status = _role_topic_status(role, "no_candidate")

    with pytest.raises(ValidationError, match="positive"):
        RoleEvaluation(
            topic_status=status,
            candidates=(_ranked_role_result(role, positive_status),),
        )


@pytest.mark.parametrize(
    ("role", "non_positive_status"),
    [
        ("trend", "eligible_non_candidate"),
        ("core", "excluded"),
        ("emotion", "eligible_non_candidate"),
        ("catchup", "excluded"),
        ("follower", "excluded"),
        ("relay", "excluded"),
    ],
)
def test_role_evaluation_no_candidate_preserves_legal_non_candidate_evidence(
    role,
    non_positive_status,
):
    candidate = _ranked_role_result(role, non_positive_status)
    legal_excluded = int(non_positive_status == "excluded")

    assert RoleEvaluation(
        topic_status=_role_topic_status(
            role,
            "no_candidate",
            legal_excluded=legal_excluded,
        ),
        candidates=(candidate,),
    ).candidates == (candidate,)


@pytest.mark.parametrize(
    ("role", "disallowed_status"),
    [
        ("trend", "candidate"),
        ("trend", "eligible_non_candidate"),
        ("emotion", "simplified_candidate"),
        ("emotion", "eligible_non_candidate"),
        ("follower", "watch"),
        ("relay", "pending_validation"),
    ],
)
def test_role_evaluation_insufficient_rejects_completed_qualification_output(
    role,
    disallowed_status,
):
    with pytest.raises(ValidationError, match="insufficient"):
        RoleEvaluation(
            topic_status=_role_topic_status(role, "insufficient"),
            candidates=(_ranked_role_result(role, disallowed_status),),
        )


@pytest.mark.parametrize("role", tuple(ROLE_CAPABILITIES))
def test_role_evaluation_insufficient_allows_only_empty_or_excluded_evidence(role):
    status = _role_topic_status(role, "insufficient", legal_excluded=1)
    excluded = _ranked_role_result(role, "excluded")

    assert RoleEvaluation(topic_status=status).candidates == ()
    assert RoleEvaluation(
        topic_status=status,
        candidates=(excluded,),
    ).candidates == (excluded,)


@pytest.mark.parametrize(
    ("candidate_changes", "error"),
    [
        ({"topic_id": "topic-2"}, "topic_id"),
        ({"role": "core"}, "role"),
    ],
)
def test_role_evaluation_rejects_candidate_context_mismatch(candidate_changes, error):
    status = RoleTopicStatus(
        topic_id="topic-1",
        role="trend",
        capability="ready",
        status="ready",
        expected=1,
        valid=1,
        legal_excluded=0,
        missing=0,
    )
    candidate = RankedCandidate(
        topic_id="topic-1",
        security_id="600000.SH",
        role="trend",
        status="candidate",
        factors=(Decimal("0.1"),),
        tie_rank=1,
    ).model_copy(update=candidate_changes)

    with pytest.raises(ValidationError, match=error):
        RoleEvaluation(topic_status=status, candidates=(candidate,))


@pytest.mark.parametrize(
    ("role", "status"),
    [
        ("trend", "candidate"),
        ("trend", "eligible_non_candidate"),
        ("trend", "excluded"),
        ("core", "candidate"),
        ("core", "eligible_non_candidate"),
        ("core", "excluded"),
        ("emotion", "simplified_candidate"),
        ("emotion", "eligible_non_candidate"),
        ("emotion", "excluded"),
        ("catchup", "simplified_candidate"),
        ("catchup", "eligible_non_candidate"),
        ("catchup", "excluded"),
        ("follower", "watch"),
        ("follower", "excluded"),
        ("relay", "pending_validation"),
        ("relay", "excluded"),
    ],
)
def test_role_candidate_accepts_status_allowed_by_role(role, status):
    candidate = RoleCandidate(
        topic_id="topic-1",
        security_id="600000.SH",
        role=role,
        status=status,
        factors=(),
    )

    assert candidate.status == status


@pytest.mark.parametrize(
    ("role", "status"),
    [
        ("trend", "simplified_candidate"),
        ("core", "watch"),
        ("emotion", "candidate"),
        ("catchup", "watch"),
        ("follower", "candidate"),
        ("follower", "simplified_candidate"),
        ("relay", "candidate"),
        ("relay", "watch"),
    ],
)
def test_role_candidate_rejects_status_conflicting_with_role(role, status):
    with pytest.raises(ValidationError, match="status"):
        RoleCandidate(
            topic_id="topic-1",
            security_id="600000.SH",
            role=role,
            status=status,
            factors=(),
        )


@pytest.mark.parametrize("value", ["1", True])
def test_role_count_fields_reject_strings_and_booleans(value):
    with pytest.raises(ValidationError, match="tie_rank"):
        RankedCandidate(
            topic_id="topic-1",
            security_id="600000.SH",
            role="trend",
            status="candidate",
            factors=(Decimal("0.1"),),
            tie_rank=value,
        )
    with pytest.raises(ValidationError, match="expected"):
        RoleTopicStatus(
            topic_id="topic-1",
            role="trend",
            capability="ready",
            status="ready",
            expected=value,
            valid=1,
            legal_excluded=0,
            missing=0,
        )


def test_role_literals_and_tuples_are_not_coerced():
    with pytest.raises(ValidationError, match="role"):
        RoleCandidate(
            topic_id="topic-1",
            security_id="600000.SH",
            role=b"trend",
            status="candidate",
            factors=(Decimal("0.1"),),
        )
    with pytest.raises(ValidationError, match="factors"):
        RoleCandidate(
            topic_id="topic-1",
            security_id="600000.SH",
            role="trend",
            status="candidate",
            factors=[Decimal("0.1")],
        )


def test_role_models_are_frozen_and_enforce_status_literals():
    candidate = RoleCandidate(
        topic_id="topic-1",
        security_id="600000.SH",
        role="trend",
        status="candidate",
        factors=(Decimal("0.1"),),
    )
    with pytest.raises(ValidationError, match="frozen"):
        candidate.status = "excluded"
    with pytest.raises(ValidationError, match="extra"):
        RoleCandidate(
            topic_id="topic-1",
            security_id="600000.SH",
            role="trend",
            status="candidate",
            factors=(),
            unknown=True,
        )
    with pytest.raises(ValidationError, match="Decimal"):
        RoleCandidate(
            topic_id="topic-1",
            security_id="600000.SH",
            role="trend",
            status="candidate",
            factors=(0.1,),
        )
    with pytest.raises(ValidationError, match="status"):
        RoleTopicStatus(
            topic_id="topic-1",
            role="trend",
            capability="ready",
            status="unknown",
            expected=1,
            valid=1,
            legal_excluded=0,
            missing=0,
        )
