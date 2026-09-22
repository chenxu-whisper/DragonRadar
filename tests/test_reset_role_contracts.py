from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from factories import bar, batch, state
from pydantic import ValidationError
from topic_factories import (
    coverage_cell,
    topic_batch,
    topic_definition,
    topic_membership,
)

from dragon_radar.domain.models import ExchangeCalendar
from dragon_radar.roles.adapters import ROLE_NAMES, adapt_role_evaluation
from dragon_radar.roles.models import (
    RankedCandidate,
    RoleEvaluation,
    RoleTopicStatus,
)
from dragon_radar.roles.research_factors import (
    FactorCoverage,
    research_role_factors,
)

T = date(2026, 9, 8)
CUTOFF = datetime(2026, 9, 9, 12, tzinfo=UTC)


def _sessions(count: int = 130) -> tuple[date, ...]:
    result = []
    current = T
    while len(result) < count:
        if current.weekday() < 5:
            result.append(current)
        current -= timedelta(days=1)
    return tuple(reversed(result))


SESSIONS = _sessions()


def _market(
    *,
    subject_days: tuple[date, ...] = SESSIONS,
    include_benchmark: bool = True,
    subject_changes: dict | None = None,
):
    subject_changes = subject_changes or {}
    bars = []
    for index, day in enumerate(subject_days, start=1):
        values = {
            "security_id": "STOCK",
            "trade_date": day,
            "close": Decimal(index),
            "previous_close": Decimal(index - 1) if index > 1 else None,
            "amount": Decimal(index * 100),
            "turnover_rate": Decimal("0.01"),
            "float_mv": Decimal("1000000") if day == T else None,
            **subject_changes,
        }
        bars.append(bar(**values))
    if include_benchmark:
        bars.extend(
            bar(
                security_id="CSI300",
                trade_date=day,
                close=Decimal(index * 2),
                amount=None,
            )
            for index, day in enumerate(SESSIONS, start=1)
        )
    states = [
        state(
            security_id="STOCK",
            effective_from=SESSIONS[0],
            listing_date=SESSIONS[0],
        )
    ]
    if include_benchmark:
        states.append(
            state(
                security_id="CSI300",
                security_type="index",
                board_category=None,
                effective_from=SESSIONS[0],
                listing_date=SESSIONS[0],
            )
        )
    calendar = ExchangeCalendar(
        exchange="TEST", version="research-c1", sessions=SESSIONS
    )
    return batch(
        trade_date=T,
        as_of=CUTOFF,
        calendar=calendar,
        calendar_version=calendar.version,
        expected_security_ids=("STOCK", "CSI300"),
        security_states=tuple(states),
        bars=tuple(bars),
    )


def _topic(market):
    members = ("STOCK", "PEER")
    peer_bar = bar(
        security_id="PEER",
        trade_date=T,
        close=Decimal("10"),
        amount=Decimal("13000"),
    )
    market = market.model_copy(
        update={
            "expected_security_ids": (*market.expected_security_ids, "PEER"),
            "security_states": (
                *market.security_states,
                state(
                    security_id="PEER",
                    effective_from=SESSIONS[0],
                    listing_date=SESSIONS[0],
                ),
            ),
            "bars": (*market.bars, peer_bar),
        }
    )
    topic = topic_batch(
        trade_date=T,
        as_of=CUTOFF,
        calendar=market.calendar,
        calendar_version=market.calendar_version,
        coverage_from=SESSIONS[0],
        coverage_to=T,
        coverage_cells=(
            coverage_cell(
                trade_date=T,
                expected_member_count=2,
                received_member_count=2,
                complete_as_of=datetime(2026, 9, 8, 17, tzinfo=UTC),
            ),
        ),
        topics=(
            topic_definition(
                effective_from=SESSIONS[0],
                available_at=datetime(2026, 1, 1, tzinfo=UTC),
            ),
        ),
        memberships=tuple(
            topic_membership(
                security_id=security_id,
                effective_from=SESSIONS[0],
                available_at=datetime(2026, 1, 1, tzinfo=UTC),
            )
            for security_id in members
        ),
    )
    return market, topic


def _evaluation(role: str, capability: str, status: str, row_status: str):
    return RoleEvaluation(
        topic_status=RoleTopicStatus(
            topic_id="topic-a",
            role=role,
            capability=capability,
            status=status,
            expected=1,
            valid=1,
            legal_excluded=0,
            missing=0,
        ),
        candidates=(
            RankedCandidate(
                topic_id="topic-a",
                security_id="STOCK",
                role=role,
                status=row_status,
                factors=(Decimal("1"),),
                tie_rank=1,
            ),
        ),
    )


def _by_metric(observations):
    return {observation.metric: observation for observation in observations}


def test_external_role_mapping_is_complete_and_case_sensitive():
    assert dict(ROLE_NAMES) == {
        "emotion": "emotion_leader",
        "trend": "trend_leader",
        "core": "zhongjun",
        "follower": "genfeng",
        "catchup": "buZhang",
        "relay": "jieli",
    }


@pytest.mark.parametrize(
    ("role", "capability", "status", "row_status"),
    (
        ("trend", "ready", "ready", "candidate"),
        ("emotion", "simplified", "simplified", "simplified_candidate"),
        ("follower", "watch_only", "watch_only", "watch"),
        ("relay", "pending_validation", "pending_validation", "pending_validation"),
    ),
)
def test_unapproved_or_nonready_rules_never_become_formal_candidates(
    role,
    capability,
    status,
    row_status,
):
    evaluation = _evaluation(role, capability, status, row_status)

    unapproved = adapt_role_evaluation(evaluation, rule_version="role-v1")
    approved = adapt_role_evaluation(
        evaluation,
        rule_version="role-v1",
        approved_rule_versions=frozenset({"role-v1"}),
    )

    assert unapproved.status == "insufficient"
    assert unapproved.rank is None
    assert unapproved.capability == capability
    assert unapproved.internal_status == row_status
    assert unapproved.rule_version == "role-v1"
    assert "rule_version_not_approved" in unapproved.reasons
    if capability == "ready":
        assert approved.status == "candidate"
        assert approved.rank == 1
    elif capability == "pending_validation":
        assert approved.status == "waiting_verify"
        assert approved.rank is None
    else:
        assert approved.status == "insufficient"
        assert approved.rank is None


def test_approved_relay_without_pending_row_remains_insufficient():
    evaluation = RoleEvaluation(
        topic_status=RoleTopicStatus(
            topic_id="topic-a",
            role="relay",
            capability="pending_validation",
            status="insufficient",
            expected=1,
            valid=0,
            legal_excluded=0,
            missing=1,
        )
    )

    adapted = adapt_role_evaluation(
        evaluation,
        rule_version="reset-role-v2",
        approved_rule_versions=frozenset({"reset-role-v2"}),
    )

    assert adapted.status == "insufficient"
    assert adapted.rank is None


def test_index_rs_is_unavailable_without_explicit_index():
    observations = _by_metric(
        research_role_factors(_market(include_benchmark=False), "STOCK", "CSI300")
    )

    for window in (20, 60, 120):
        observation = observations[f"relative_strength_{window}"]
        assert observation.value is None
        assert observation.coverage.valid == 0
        assert "benchmark_identity_unavailable" in observation.reasons


def test_all_factors_require_visible_effective_security_identity():
    market = _market()
    market = market.model_copy(
        update={
            "security_states": tuple(
                item for item in market.security_states if item.security_id != "STOCK"
            )
        }
    )

    observations = research_role_factors(market, "STOCK", "CSI300")

    assert all(observation.value is None for observation in observations)
    assert all(
        "security_identity_unavailable" in observation.reasons
        for observation in observations
    )


def test_duplicate_bound_bar_is_missing_for_its_exact_position():
    market = _market()
    duplicate = next(
        item
        for item in market.bars
        if item.security_id == "STOCK" and item.trade_date == T
    )
    market = market.model_copy(update={"bars": (*market.bars, duplicate)})

    observations = _by_metric(research_role_factors(market, "STOCK", "CSI300"))

    assert observations["relative_strength_20"].value is None
    assert observations["relative_strength_20"].coverage == FactorCoverage(
        expected=21,
        valid=20,
        excluded=0,
        missing=1,
    )
    assert observations["float_mv"].value is None


def test_exact_windows_do_not_move_forward_to_replace_missing_samples():
    missing_endpoint = SESSIONS[-21]
    subject_days = tuple(day for day in SESSIONS if day != missing_endpoint)

    observations = _by_metric(
        research_role_factors(
            _market(subject_days=subject_days),
            "STOCK",
            "CSI300",
        )
    )

    assert observations["relative_strength_20"].value is None
    assert observations["relative_strength_20"].coverage == FactorCoverage(
        expected=21,
        valid=20,
        excluded=0,
        missing=1,
    )
    assert observations["relative_strength_60"].value is None
    assert observations["relative_strength_120"].value is None
    assert observations["close_vs_ma20"].value is not None
    assert observations["ma20_slope_5"].value is None


def test_research_factors_emit_traceable_independent_observations():
    market, topic = _topic(_market())

    observations = research_role_factors(
        market,
        "STOCK",
        "CSI300",
        topic=topic,
        topic_id="topic-a",
    )
    metrics = _by_metric(observations)

    assert {observation.factor_id for observation in observations} == {
        "FAC-R01",
        "FAC-R02",
        "FAC-R03",
        "FAC-C01",
        "FAC-C02",
    }
    assert all(observation.unit and observation.window for observation in observations)
    assert all(
        observation.coverage.expected
        == (
            observation.coverage.valid
            + observation.coverage.excluded
            + observation.coverage.missing
        )
        for observation in observations
    )
    assert metrics["relative_strength_120"].value == Decimal("0")
    assert metrics["new_high_120"].value is True
    assert metrics["maximum_drawdown_60"].value == Decimal("0")
    assert metrics["average_amount_20"].value == Decimal("12050")
    assert metrics["topic_amount_share"].value == Decimal("0.5")
    assert metrics["float_mv"].value == Decimal("1000000")
    assert metrics["amount_change_1_vs_prior5"].metadata["baseline"] == "T-5..T-1"
    assert metrics["relative_strength_20"].metadata["knowledge_cutoff"] == (
        CUTOFF.isoformat()
    )
    assert metrics["topic_amount_share"].metadata["topic_source_version"] == "s1"
    assert metrics["up_day_average_amount"].value is not None
    assert metrics["down_day_average_amount"].value is None
    assert metrics["down_day_average_amount"].reasons == ("not_applicable",)


def test_missing_optional_capacity_fields_are_not_substituted():
    observations = _by_metric(
        research_role_factors(
            _market(subject_changes={"turnover_rate": None, "float_mv": None}),
            "STOCK",
            "CSI300",
        )
    )

    assert observations["turnover_stability_20"].value is None
    assert observations["float_mv"].value is None
    assert "turnover_rate_unavailable" in observations["turnover_stability_20"].reasons
    assert observations["float_mv"].reasons == ("float_mv_unavailable",)


def test_topic_share_requires_member_identity_as_well_as_amount():
    market, topic = _topic(_market())
    market = market.model_copy(
        update={
            "security_states": tuple(
                item for item in market.security_states if item.security_id != "PEER"
            )
        }
    )

    observations = _by_metric(
        research_role_factors(
            market,
            "STOCK",
            "CSI300",
            topic=topic,
            topic_id="topic-a",
        )
    )

    assert observations["topic_amount_share"].value is None
    assert observations["topic_amount_share"].coverage.missing == 1
    assert observations["topic_amount_share"].reasons == (
        "topic_member_identity_unavailable",
    )


def test_factor_coverage_is_a_strict_nonnegative_partition():
    with pytest.raises(ValidationError, match="partition"):
        FactorCoverage(expected=2, valid=1, excluded=0, missing=0)
    with pytest.raises(ValidationError):
        FactorCoverage(expected=1, valid=-1, excluded=1, missing=1)
