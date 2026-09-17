import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from pydantic import ValidationError

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
from dragon_radar.roles.models import RoleCandidate, RoleTopicStatus
from dragon_radar.roles.rules import (
    ROLE_RULE_SHA256,
    ROLE_RULE_VERSION,
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
        (topic_amount_share, (Decimal("0"), (Decimal("0"), Decimal("0")))),
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


def test_knowledge_cutoff_is_earlier_snapshot_time_in_utc():
    market_as_of = datetime(2026, 9, 17, 8, tzinfo=UTC)
    topic_as_of = market_as_of - timedelta(minutes=5)

    assert knowledge_cutoff(market_as_of, topic_as_of) == topic_as_of


def test_knowledge_cutoff_rejects_naive_datetimes():
    with pytest.raises(ValueError, match="timezone"):
        knowledge_cutoff(datetime(2026, 9, 17), datetime(2026, 9, 17, tzinfo=UTC))


def test_rule_definitions_are_canonical_immutable_and_digest_checked():
    assert ROLE_RULE_VERSION == "role-v1"
    assert len(ROLE_RULE_SHA256) == 64
    assert RULE_DEFINITIONS["role-v1"]["allowed_boards"] == (
        "sh_main",
        "sz_main",
    )
    assert RULE_DEFINITIONS["role-test-v1"]["allowed_boards"] == ("synthetic_test",)
    assert RULE_DEFINITIONS["role-test-v1"]["production_activatable"] is False
    with pytest.raises(TypeError):
        RULE_DEFINITIONS["role-v1"]["allowed_boards"] = ("synthetic_test",)

    canonical = canonical_rule_json(ROLE_RULE_VERSION)
    validate_rule_definition(ROLE_RULE_VERSION, canonical)
    changed = json.loads(canonical)
    changed["minimum_topic_members"] += 1
    with pytest.raises(RuntimeError, match="digest"):
        validate_rule_definition(
            ROLE_RULE_VERSION,
            json.dumps(changed, sort_keys=True, separators=(",", ":")),
        )


def test_role_models_are_frozen_strict_and_enforce_status_literals():
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
