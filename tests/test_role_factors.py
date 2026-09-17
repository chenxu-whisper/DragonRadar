import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
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


def test_relative_return_subtracts_exact_decimal_returns():
    assert relative_return(Decimal("0.125"), Decimal("0.075")) == Decimal("0.050")


@pytest.mark.parametrize(
    ("values", "expected"),
    [
        ((Decimal("3"), Decimal("1"), Decimal("2")), Decimal("2")),
        ((Decimal("1"), Decimal("3")), Decimal("2")),
    ],
)
def test_decimal_median_is_exact_for_odd_and_even_samples(values, expected):
    assert decimal_median(values) == expected


def test_maximum_drawdown_uses_each_running_peak():
    assert maximum_drawdown(
        tuple(map(Decimal, ("100", "125", "100", "150", "90")))
    ) == Decimal("0.4")


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


def test_mean_absolute_deviation_rate_uses_decimal_mean():
    assert mean_absolute_deviation_rate(
        tuple(map(Decimal, ("10", "20", "30")))
    ) == Decimal("0.3333333333333333333333333333333334")


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
        "denominator": "finite_and_strictly_positive",
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
        "tie_definition": "identical_complete_evidence_tuple",
        "rank_method": "dense",
        "rank_start": 1,
        "security_id": "stable_display_order_within_tie_only",
    },
}

EXPECTED_ROLE_RULES = {
    "trend": {
        "capability": "ready",
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


def test_rule_definition_digests_are_locked_and_detect_behavior_drift():
    assert (
        ROLE_RULE_SHA256
        == "78602ac54b28d763b00cad702897c244ff144bb0136d7fbd6b04332b7126a41e"
    )
    assert (
        ROLE_TEST_RULE_SHA256
        == "caad5612b15f1aae9745c4f8c1233a46854610c738a74e67075229298a0d5c2f"
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
