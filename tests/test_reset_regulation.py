"""Reviewed regulation rules, calculations, events, and publication lineage."""

from datetime import timedelta
from decimal import Decimal

import pytest
from factories import DAYS, NOW, batch
from pydantic import ValidationError


def _review_record():
    from dragon_radar.regulation.models import RegulationReviewRecord

    return RegulationReviewRecord(
        prepared_by="rule-author",
        reviewed_by="independent-reviewer",
        published_by="release-owner",
        reviewed_at=NOW - timedelta(hours=2),
        published_at=NOW - timedelta(hours=1),
    )


def _case(
    *,
    name="upper-approaching",
    computed_value="0.68",
    expected_state="approaching_alert",
):
    from dragon_radar.regulation.models import RegulationTestCase

    return RegulationTestCase(
        name=name,
        computed_value=Decimal(computed_value),
        expected_state=expected_state,
    )


def _rule(**changes):
    from dragon_radar.regulation.models import RegulationRule

    values = {
        "rule_id": "synthetic-upper",
        "rule_version": "test-v1",
        "applicable_exchange": "TEST",
        "applicable_board": "synthetic_test",
        "security_status": "regular",
        "effective_from": DAYS[0],
        "effective_to": None,
        "window_days": 10,
        "direction": "upper",
        "threshold": Decimal("0.70"),
        "warning_threshold": Decimal("0.65"),
        "benchmark_index": "TEST-BENCHMARK",
        "normal_anomaly_count_mode": "rolling_window",
        "recompute_condition": "window_reset",
        "calculation_method": "relative_return",
        "official_source_url": "https://exchange.example.test/rules/test-v1",
        "review_switch": "approved",
        "review_record": _review_record(),
        "test_cases": (_case(),),
        "regression_passed": True,
        "active": True,
    }
    return RegulationRule(**(values | changes))


def _observation(**changes):
    from dragon_radar.regulation.models import RegulationObservation

    values = {
        "security_id": "A",
        "trade_date": DAYS[-1],
        "exchange": "TEST",
        "board": "synthetic_test",
        "security_status": "regular",
        "computed_value": Decimal("0.68"),
        "input_state": "complete",
    }
    return RegulationObservation(**(values | changes))


def _event(**changes):
    from dragon_radar.regulation.models import RegulationEvent

    values = {
        "event_id": "event-001",
        "rule_id": "synthetic-upper",
        "rule_version": "test-v1",
        "security_id": "A",
        "event_type": "monitoring",
        "announcement_id": "notice-001",
        "announcement_url": "https://exchange.example.test/notices/001",
        "effective_from": DAYS[-1],
        "effective_to": None,
    }
    return RegulationEvent(**(values | changes))


def test_unreviewed_rule_stays_pending_even_near_warning_threshold():
    from dragon_radar.regulation.engine import evaluate_rule

    result = evaluate_rule(
        _rule(
            review_switch="pending",
            review_record=None,
            regression_passed=False,
            active=False,
        ),
        _observation(computed_value=Decimal("0.6999")),
    )

    assert result.state == "pending"
    assert "rule_not_approved" in result.reasons
    assert result.computed_value == Decimal("0.6999")


@pytest.mark.parametrize(
    ("computed", "state"),
    [
        ("0.6499", "idle"),
        ("0.65", "approaching_alert"),
        ("0.6999", "approaching_alert"),
        ("0.70", "triggered"),
    ],
)
def test_upper_warning_and_legal_threshold_are_independent(computed, state):
    from dragon_radar.regulation.engine import evaluate_rule

    assert (
        evaluate_rule(
            _rule(),
            _observation(computed_value=Decimal(computed)),
        ).state
        == state
    )


@pytest.mark.parametrize(
    ("computed", "state"),
    [
        ("0.3501", "idle"),
        ("0.35", "approaching_alert"),
        ("0.3001", "approaching_alert"),
        ("0.30", "triggered"),
    ],
)
def test_lower_warning_and_legal_threshold_are_independent(computed, state):
    from dragon_radar.regulation.engine import evaluate_rule

    lower = _rule(
        rule_id="synthetic-lower",
        direction="lower",
        threshold=Decimal("0.30"),
        warning_threshold=Decimal("0.35"),
        test_cases=(
            _case(
                name="lower-approaching",
                computed_value="0.32",
                expected_state="approaching_alert",
            ),
        ),
    )

    assert (
        evaluate_rule(
            lower,
            _observation(computed_value=Decimal(computed)),
        ).state
        == state
    )


def test_directional_warning_order_is_validated():
    with pytest.raises(ValidationError):
        _rule(warning_threshold=Decimal("0.70"))
    with pytest.raises(ValidationError):
        _rule(
            direction="lower",
            threshold=Decimal("0.30"),
            warning_threshold=Decimal("0.30"),
        )


@pytest.mark.parametrize(
    ("changes", "reason"),
    [
        ({"input_state": "missing", "computed_value": None}, "input_missing"),
        ({"input_state": "conflict", "computed_value": None}, "input_conflict"),
        ({"exchange": "OTHER"}, "rule_not_applicable"),
    ],
)
def test_missing_conflicting_or_inapplicable_input_never_becomes_idle(changes, reason):
    from dragon_radar.regulation.engine import evaluate_rule

    result = evaluate_rule(_rule(), _observation(**changes))

    assert result.state == "pending"
    assert reason in result.reasons


def test_unsupported_inactive_or_incompletely_released_rule_stays_pending():
    from dragon_radar.regulation.engine import evaluate_rule

    cases = (
        (
            _rule(calculation_method="not-supported"),
            "unsupported_calculation_method",
        ),
        (_rule(active=False), "rule_inactive"),
        (_rule(official_source_url=None), "official_source_missing"),
        (_rule(review_record=None), "review_record_missing"),
        (_rule(regression_passed=False), "regression_not_passed"),
    )

    for rule, reason in cases:
        result = evaluate_rule(rule, _observation())
        assert result.state == "pending"
        assert reason in result.reasons


def test_review_record_requires_independent_review_and_release_order():
    from dragon_radar.regulation.models import RegulationReviewRecord

    with pytest.raises(ValidationError):
        RegulationReviewRecord(
            prepared_by="same-person",
            reviewed_by="same-person",
            published_by="release-owner",
            reviewed_at=NOW,
            published_at=NOW,
        )
    with pytest.raises(ValidationError):
        RegulationReviewRecord(
            prepared_by="author",
            reviewed_by="reviewer",
            published_by="release-owner",
            reviewed_at=NOW,
            published_at=NOW - timedelta(seconds=1),
        )


def test_regression_gate_is_explicit_not_inferred_from_nonempty_cases():
    from dragon_radar.regulation.models import production_gate_reasons

    rule = _rule(regression_passed=False, test_cases=(_case(),))

    assert "regression_not_passed" in production_gate_reasons(rule)
    assert "regression_cases_missing" in production_gate_reasons(_rule(test_cases=()))


def test_formal_event_has_only_prd_table_8_facts_and_is_not_calculation_output():
    from dragon_radar.regulation.engine import evaluate_rule

    event = _event()
    calculation = evaluate_rule(
        _rule(),
        _observation(computed_value=Decimal("0.90")),
    )

    assert set(event.model_dump()) == {
        "event_id",
        "rule_id",
        "rule_version",
        "security_id",
        "event_type",
        "announcement_id",
        "announcement_url",
        "effective_from",
        "effective_to",
    }
    assert calculation.state == "triggered"
    assert not hasattr(calculation, "event")
    assert not hasattr(calculation, "event_type")


def test_event_and_rule_urls_must_be_absolute_https():
    with pytest.raises(ValidationError):
        _rule(official_source_url="http://exchange.example.test/rule")
    with pytest.raises(ValidationError):
        _event(announcement_url="https://user:secret@example.test/notice")


def test_frozen_regression_examples_pass_before_activation():
    from dragon_radar.regulation.engine import run_regression_cases

    assert run_regression_cases(_rule()) == ()


def test_event_effective_interval_is_half_open():
    from dragon_radar.regulation.models import event_is_effective

    event = _event(effective_to=DAYS[-1] + timedelta(days=1))

    assert event_is_effective(event, DAYS[-1])
    assert not event_is_effective(event, DAYS[-1] + timedelta(days=1))


def test_cli_exposes_only_controlled_regulation_write_commands_without_database():
    from dragon_radar.cli import _parser

    help_text = _parser().format_help()

    assert "regulation-rule-register" in help_text
    assert "regulation-rule-activate" in help_text
    assert "regulation-rule-withdraw" in help_text
    assert "regulation-event-add" in help_text
    assert "regulation-materialize" in help_text


def test_repository_withdrawal_stops_new_calculation_but_keeps_old_snapshot(
    database, repository
):
    from dragon_radar.regulation.repository import RegulationRepository

    market = repository.publish(
        repository.import_batch(batch(), key="regulation-market").id
    )
    regulations = RegulationRepository(database, repository, clock=lambda: NOW)
    regulations.register_rule(_rule(), key="regulation-rule")
    regulations.append_event(_event(), key="regulation-event")
    first = regulations.materialize(
        market_snapshot_id=market.id,
        observations=(_observation(),),
        key="regulation-before-withdrawal",
    )

    regulations.withdraw_rule(
        "synthetic-upper",
        "test-v1",
        reason="official_rule_withdrawn",
        key="regulation-withdrawal",
    )
    second = regulations.materialize(
        market_snapshot_id=market.id,
        observations=(_observation(),),
        key="regulation-after-withdrawal",
    )

    assert len(regulations.snapshot_context(first.id)["calculations"]) == 1
    assert regulations.snapshot_context(second.id)["calculations"] == ()
    assert len(regulations.snapshot_context(second.id)["inputs"]) == 1
    assert len(regulations.snapshot_context(first.id)["events"]) == 1
    assert len(regulations.snapshot_context(second.id)["events"]) == 1


def test_pending_rule_is_materialized_but_cannot_be_activated(database, repository):
    from dragon_radar.regulation.repository import RegulationRepository

    market = repository.publish(
        repository.import_batch(batch(), key="regulation-pending-market").id
    )
    regulations = RegulationRepository(database, repository, clock=lambda: NOW)
    pending = _rule(
        review_switch="pending",
        review_record=None,
        regression_passed=False,
        active=False,
    )
    regulations.register_rule(pending, key="regulation-pending-rule")

    snapshot = regulations.materialize(
        market_snapshot_id=market.id,
        observations=(_observation(),),
        key="regulation-pending-snapshot",
    )

    result = regulations.snapshot_context(snapshot.id)["calculations"][0]
    assert result.state == "pending"
    with pytest.raises(ValueError, match="not production eligible"):
        regulations.activate_rule(
            pending.rule_id,
            pending.rule_version,
            key="regulation-invalid-activation",
        )


def test_publication_automatically_materializes_stable_regulation_payload(
    database, repository
):
    from dragon_radar.publication.models import PublicationCommand
    from dragon_radar.publication.repository import PublicationRepository
    from dragon_radar.publication.service import PublicationService
    from dragon_radar.regulation.repository import RegulationRepository

    market = repository.publish(
        repository.import_batch(batch(), key="regulation-publication-market").id
    )
    regulations = RegulationRepository(database, repository, clock=lambda: NOW)
    regulations.register_rule(_rule(), key="regulation-publication-rule")
    regulations.append_event(_event(), key="regulation-publication-event")
    regulation_snapshot = regulations.materialize(
        market_snapshot_id=market.id,
        observations=(_observation(),),
        key="regulation-publication-input",
    )

    publication = PublicationService(
        database,
        repository,
        clock=lambda: NOW,
    ).publish(
        PublicationCommand(
            trade_date=DAYS[-1],
            market_snapshot_id=market.id,
            regulation_input_id=regulation_snapshot.id,
            disabled_domains=("topic", "news", "role"),
        ),
        key="regulation-publication",
    )
    result = next(
        item
        for item in PublicationRepository(database, repository).list_results(
            publication.id
        )
        if item.domain == "regulation"
    )

    assert result.status == "ready"
    assert result.input_id == regulation_snapshot.id
    assert result.payload["schema"] == "dragon-radar.regulation"
    assert result.payload["completeness"]["expected"] == 1
    assert result.payload["calculations"][0]["state"] == "approaching_alert"
    assert result.payload["events"] == [
        {
            "event_id": "event-001",
            "rule_id": "synthetic-upper",
            "rule_version": "test-v1",
            "security_id": "A",
            "event_type": "monitoring",
            "announcement_id": "notice-001",
            "announcement_url": "https://exchange.example.test/notices/001",
            "effective_from": DAYS[-1].isoformat(),
            "effective_to": None,
        }
    ]


def test_regulation_tables_are_append_only_and_migration_is_ready(database):
    from sqlalchemy import inspect, text, update
    from sqlalchemy.exc import DBAPIError

    from dragon_radar.regulation.repository import RegulationRepository
    from dragon_radar.regulation.tables import regulation_rules
    from dragon_radar.storage.database import SCHEMA_REVISION

    regulations = RegulationRepository(database, clock=lambda: NOW)
    regulations.register_rule(_rule(), key="regulation-immutable-rule")

    assert SCHEMA_REVISION == "0009_regulation"
    assert database.ready()
    inspector = inspect(database.engine)
    assert {
        "regulation_rules",
        "regulation_rule_statuses",
        "regulation_events",
        "regulation_snapshots",
        "regulation_snapshot_rules",
        "regulation_snapshot_inputs",
        "regulation_calculations",
        "regulation_snapshot_events",
        "regulation_commands",
    } <= set(inspector.get_table_names())
    assert any(
        fk["referred_table"] == "regulation_snapshots"
        and fk["constrained_columns"] == ["regulation_input_id"]
        for fk in inspector.get_foreign_keys("publications")
    )
    with pytest.raises(DBAPIError), database.engine.begin() as connection:
        connection.execute(
            update(regulation_rules)
            .where(regulation_rules.c.rule_id == "synthetic-upper")
            .values(window_days=99)
        )
    with database.engine.connect() as connection:
        assert (
            connection.scalar(text("SELECT version_num FROM alembic_version"))
            == "0009_regulation"
        )
