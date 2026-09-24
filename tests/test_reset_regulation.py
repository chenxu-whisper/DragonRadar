"""Reviewed regulation rules, calculations, events, and publication lineage."""

from concurrent.futures import ThreadPoolExecutor
from contextlib import nullcontext
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from threading import Barrier
from types import SimpleNamespace
from uuid import uuid4

import pytest
from factories import DAYS, NOW, bar, batch, grant, state
from pydantic import ValidationError

HTTPS_URL_CASES = (
    ("https://exchange.example.test/rule", True),
    ("HTTPS://exchange.example.test/rule", True),
    ("https://#x", False),
    ("https://?x", False),
    ("https://user:secret@example.test/rule", False),
    ("http://exchange.example.test/rule", False),
)


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
        "rule_id": "synthetic-upper",
        "rule_version": "test-v1",
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


def _register_event_source(repository, source_id):
    repository.register_source(
        grant(
            source_id=source_id,
            reference=f"{source_id}-synthetic-test-only",
        )
    )


def _insert_legacy_event(database, event):
    from sqlalchemy import insert

    from dragon_radar.regulation.tables import regulation_events

    with database.engine.begin() as connection:
        connection.execute(
            insert(regulation_events).values(
                **event.model_dump(mode="json"),
                created_at=NOW,
            )
        )


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
            _observation(
                rule_id=lower.rule_id,
                computed_value=Decimal(computed),
            ),
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


def test_rule_evaluation_rejects_an_observation_bound_to_another_rule():
    from dragon_radar.regulation.engine import evaluate_rule

    result = evaluate_rule(
        _rule(),
        _observation(rule_version="another-version"),
    )

    assert result.state == "pending"
    assert "rule_input_mismatch" in result.reasons


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


def test_formal_event_adds_only_source_provenance_to_prd_table_8_facts():
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
        "source_id",
        "source_version",
    }
    assert calculation.state == "triggered"
    assert not hasattr(calculation, "event")
    assert not hasattr(calculation, "event_type")


def test_legacy_event_model_allows_null_provenance_but_repository_rejects_new_write():
    from dragon_radar.regulation.repository import RegulationRepository

    event = _event()

    assert event.source_id is None
    assert event.source_version is None

    class Engine:
        def begin(self):
            raise AssertionError("invalid event must be rejected before database write")

    regulations = RegulationRepository(
        SimpleNamespace(engine=Engine()),
        SimpleNamespace(),
    )

    with pytest.raises(ValueError, match="New regulation events require source"):
        regulations.append_event(event, key="missing-event-provenance")


def test_sourced_event_append_authorizes_new_write_and_replays_valid_command(
    database,
    repository,
):
    from dragon_radar.regulation.repository import RegulationRepository
    from dragon_radar.storage.repository import Forbidden

    regulations = RegulationRepository(database, repository, clock=lambda: NOW)
    regulations.register_rule(_rule(), key="sourced-event-rule")
    event = _event(source_id="synthetic", source_version="fixture-v1")

    recorded = regulations.append_event(event, key="sourced-event")
    repository.revoke_source("synthetic")
    replayed = regulations.append_event(event, key="sourced-event")

    assert recorded == event
    assert replayed == event
    with pytest.raises(Forbidden, match="event source"):
        regulations.append_event(
            event.model_copy(
                update={
                    "event_id": "event-revoked",
                    "announcement_id": "notice-revoked",
                }
            ),
            key="revoked-event-source",
        )
    with pytest.raises(Forbidden, match="Source is not locally authorized"):
        regulations.append_event(
            event.model_copy(
                update={
                    "event_id": "event-unknown",
                    "announcement_id": "notice-unknown",
                    "source_id": "unknown-event-source",
                }
            ),
            key="unknown-event-source",
        )


@pytest.mark.parametrize(("url", "accepted"), HTTPS_URL_CASES)
def test_regulation_urls_follow_the_https_authority_contract(url, accepted):
    expectation = nullcontext() if accepted else pytest.raises(ValidationError)

    with expectation:
        _rule(official_source_url=url)


@pytest.mark.parametrize(("url", "accepted"), HTTPS_URL_CASES)
def test_postgres_direct_insert_enforces_the_https_authority_contract(
    database, url, accepted
):
    from sqlalchemy import insert
    from sqlalchemy.exc import DBAPIError

    from dragon_radar.regulation.tables import regulation_rules

    values = _rule().model_dump(mode="json")
    values["id"] = uuid4()
    values["official_source_url"] = url
    values["created_at"] = NOW
    expectation = nullcontext() if accepted else pytest.raises(DBAPIError)

    with expectation, database.engine.begin() as connection:
        connection.execute(insert(regulation_rules).values(**values))


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


def test_cli_accepts_controlled_regulation_event_source_attestation():
    from dragon_radar.cli import _parser

    args = _parser().parse_args(
        [
            "regulation-materialize",
            "observations.json",
            "--market-snapshot",
            str(uuid4()),
            "--key",
            "synthetic-materialization",
            "--event-source-completeness",
            "complete",
            "--event-source-id",
            "synthetic-event-feed",
            "--event-source-version",
            "fixture-v1",
        ]
    )

    assert args.event_source_completeness == "complete"
    assert args.event_source_id == "synthetic-event-feed"
    assert args.event_source_version == "fixture-v1"


def test_observation_identity_requires_rule_id_and_version():
    from dragon_radar.regulation.models import RegulationObservation

    values = _observation().model_dump()
    values.pop("rule_version")

    with pytest.raises(ValidationError):
        RegulationObservation(**values)


def test_duplicate_or_conflicting_security_rule_inputs_are_rejected_before_storage():
    from dragon_radar.regulation.repository import RegulationRepository

    repository = RegulationRepository(object(), object())
    duplicate = _observation()
    conflict = _observation(computed_value=Decimal("0.69"))

    with pytest.raises(ValueError, match="unique security and rule identities"):
        repository.materialize(
            market_snapshot_id=uuid4(),
            observations=(duplicate, duplicate),
            key="duplicate-rule-input",
        )
    with pytest.raises(ValueError, match="unique security and rule identities"):
        repository.materialize(
            market_snapshot_id=uuid4(),
            observations=(duplicate, conflict),
            key="conflicting-rule-input",
        )


def test_regulation_snapshot_event_source_evidence_is_explicit_and_consistent():
    from dragon_radar.regulation.models import RegulationSnapshot

    values = {
        "id": uuid4(),
        "market_snapshot_id": uuid4(),
        "trade_date": DAYS[-1],
        "as_of": NOW,
        "created_at": NOW,
    }

    unknown = RegulationSnapshot(**values)
    complete = RegulationSnapshot(
        **values,
        event_source_completeness="complete",
        event_source_id="synthetic-event-feed",
        event_source_version="fixture-v1",
    )

    assert unknown.event_source_completeness == "unknown"
    assert unknown.event_source_id is None
    assert unknown.event_source_version is None
    assert complete.event_source_completeness == "complete"
    with pytest.raises(ValidationError):
        RegulationSnapshot(
            **values,
            event_source_completeness="complete",
        )
    with pytest.raises(ValidationError):
        RegulationSnapshot(
            **values,
            event_source_id="synthetic-event-feed",
        )


def test_materialized_production_gate_includes_method_and_snapshot_binding():
    from dragon_radar.regulation.models import RegulationSnapshot
    from dragon_radar.regulation.repository import RegulationRepository

    snapshot = RegulationSnapshot(
        id=uuid4(),
        market_snapshot_id=uuid4(),
        trade_date=DAYS[-1],
        as_of=NOW,
        created_at=NOW,
    )
    context = {
        "snapshot": snapshot,
        "calculations": (),
        "events": (),
        "inputs": (),
        "expected_security_ids": ("A",),
        "rule_bindings": (
            {"active": True},
            {"active": False},
        ),
        "rules": (
            _rule(
                rule_id="unsupported-rule",
                calculation_method="not-supported",
            ),
            _rule(rule_id="inactive-binding-rule"),
        ),
    }

    payload = RegulationRepository.materialize_context(context).payload
    unsupported, inactive = payload["rules"]

    assert unsupported["production_enabled"] is False
    assert unsupported["production_gate_reasons"] == ("unsupported_calculation_method",)
    assert inactive["production_enabled"] is False
    assert inactive["production_gate_reasons"] == ("snapshot_binding_inactive",)


def test_regulation_sql_guards_cannot_treat_null_eligibility_as_allowed():
    migration = Path(
        "src/dragon_radar/migrations/versions/0009_regulation.py"
    ).read_text(encoding="utf-8")

    assert "RETURN COALESCE(" in migration
    assert "regulation_rule_is_production_eligible(NEW) IS NOT TRUE" in migration
    assert "regulation_rule_is_production_eligible(rule_row) IS NOT TRUE" in migration
    assert "rule_row.calculation_method IN ('relative_return')" in migration


def test_regulation_record_clocks_are_read_only_after_advisory_locks(monkeypatch):
    from dragon_radar.regulation.repository import RegulationRepository

    class Engine:
        def begin(self):
            return nullcontext(object())

    class Market:
        lock_count = 0

        def _lock(self, _connection, _key):
            self.lock_count += 1

    market = Market()

    def clock():
        assert market.lock_count == 2
        return NOW

    regulations = RegulationRepository(
        SimpleNamespace(engine=Engine()),
        market,
        clock=clock,
    )

    def stop_after_lock(_connection, _key, _payload_hash):
        raise RuntimeError("stop after lock")

    monkeypatch.setattr(regulations, "_existing_command", stop_after_lock)
    operations = (
        lambda: regulations.register_rule(_rule(), key="clock-rule"),
        lambda: regulations.withdraw_rule(
            "synthetic-upper",
            "test-v1",
            reason="clock_withdrawal",
            key="clock-status",
        ),
        lambda: regulations.append_event(
            _event(source_id="synthetic", source_version="fixture-v1"),
            key="clock-event",
        ),
        lambda: regulations.materialize(
            market_snapshot_id=uuid4(),
            observations=(_observation(),),
            key="clock-snapshot",
        ),
    )

    for operation in operations:
        market.lock_count = 0
        with pytest.raises(RuntimeError, match="stop after lock"):
            operation()


def test_regulation_snapshot_schema_has_status_sequence_watermark():
    from sqlalchemy import BigInteger

    from dragon_radar.regulation.tables import regulation_snapshots

    watermark = regulation_snapshots.c.status_sequence_watermark

    assert isinstance(watermark.type, BigInteger)
    assert watermark.nullable is False


def test_repository_withdrawal_stops_new_calculation_but_keeps_old_snapshot(
    database, repository
):
    from dragon_radar.regulation.repository import RegulationRepository

    market = repository.publish(
        repository.import_batch(batch(), key="regulation-market").id
    )
    regulations = RegulationRepository(database, repository, clock=lambda: NOW)
    regulations.register_rule(_rule(), key="regulation-rule")
    regulations.append_event(
        _event(source_id="synthetic", source_version="fixture-v1"),
        key="regulation-event",
    )
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


def test_materialization_fills_missing_expected_security_for_each_applicable_rule(
    database, repository
):
    from dragon_radar.regulation.repository import RegulationRepository

    market_batch = batch(
        expected_security_ids=("A", "B"),
        security_states=(state("A"), state("B")),
        bars=tuple(
            bar(security_id=security_id, trade_date=day)
            for security_id in ("A", "B")
            for day in DAYS
        ),
    )
    market = repository.publish(
        repository.import_batch(
            market_batch,
            key="regulation-completeness-market",
        ).id
    )
    regulations = RegulationRepository(database, repository, clock=lambda: NOW)
    regulations.register_rule(_rule(), key="regulation-completeness-rule")

    snapshot = regulations.materialize(
        market_snapshot_id=market.id,
        observations=(_observation(),),
        key="regulation-completeness-snapshot",
    )
    context = regulations.snapshot_context(snapshot.id)
    payload = regulations.materialize_context(context)
    calculations = {
        calculation.security_id: calculation for calculation in context["calculations"]
    }

    assert calculations["B"].state == "pending"
    assert calculations["B"].reasons == ("input_missing",)
    assert payload.coverage == {
        "expected": 2,
        "valid": 1,
        "legal_excluded": 0,
        "missing": 1,
    }
    assert payload.payload["event_source_completeness"] == "unknown"


def test_materialization_persists_complete_event_source_evidence_and_hashes_it(
    database,
    repository,
):
    from dragon_radar.regulation.repository import RegulationRepository
    from dragon_radar.storage.repository import Conflict

    market = repository.publish(
        repository.import_batch(
            batch(),
            key="event-source-evidence-market",
        ).id
    )
    _register_event_source(repository, "synthetic-event-feed")
    regulations = RegulationRepository(database, repository, clock=lambda: NOW)
    regulations.register_rule(_rule(), key="event-source-evidence-rule")
    snapshot = regulations.materialize(
        market_snapshot_id=market.id,
        observations=(_observation(),),
        event_source_completeness="complete",
        event_source_id="synthetic-event-feed",
        event_source_version="fixture-v1",
        key="event-source-evidence-snapshot",
    )

    context = regulations.snapshot_context(snapshot.id)
    payload = regulations.materialize_context(context).payload

    assert snapshot.event_source_completeness == "complete"
    assert snapshot.event_source_id == "synthetic-event-feed"
    assert snapshot.event_source_version == "fixture-v1"
    assert context["root"]["event_source_completeness"] == "complete"
    assert context["root"]["event_source_id"] == "synthetic-event-feed"
    assert context["root"]["event_source_version"] == "fixture-v1"
    assert payload["event_source_completeness"] == "complete"
    assert payload["event_source_id"] == "synthetic-event-feed"
    assert payload["event_source_version"] == "fixture-v1"

    with pytest.raises(Conflict):
        regulations.materialize(
            market_snapshot_id=market.id,
            observations=(_observation(),),
            event_source_completeness="complete",
            event_source_id="synthetic-event-feed",
            event_source_version="fixture-v2",
            key="event-source-evidence-snapshot",
        )


def test_complete_attestation_binds_only_exact_source_and_version_events(
    database,
    repository,
):
    from dragon_radar.regulation.repository import RegulationRepository

    _register_event_source(repository, "synthetic-event-a")
    _register_event_source(repository, "synthetic-event-b")
    market = repository.publish(
        repository.import_batch(batch(), key="event-provenance-market").id
    )
    regulations = RegulationRepository(database, repository, clock=lambda: NOW)
    regulations.register_rule(_rule(), key="event-provenance-rule")
    for event in (
        _event(
            event_id="event-source-a-v1",
            announcement_id="notice-source-a-v1",
            source_id="synthetic-event-a",
            source_version="v1",
        ),
        _event(
            event_id="event-source-a-v2",
            announcement_id="notice-source-a-v2",
            source_id="synthetic-event-a",
            source_version="v2",
        ),
        _event(
            event_id="event-source-b-v1",
            announcement_id="notice-source-b-v1",
            source_id="synthetic-event-b",
            source_version="v1",
        ),
    ):
        regulations.append_event(event, key=f"append-{event.event_id}")
    _insert_legacy_event(
        database,
        _event(
            event_id="event-legacy",
            announcement_id="notice-legacy",
        ),
    )

    snapshot = regulations.materialize(
        market_snapshot_id=market.id,
        observations=(_observation(),),
        event_source_completeness="complete",
        event_source_id="synthetic-event-a",
        event_source_version="v1",
        key="event-provenance-complete",
    )

    assert [
        event.event_id for event in regulations.snapshot_context(snapshot.id)["events"]
    ] == ["event-source-a-v1"]


def test_complete_empty_source_dependency_blocks_publication_and_read_after_revocation(
    database,
    repository,
):
    from sqlalchemy import select

    from dragon_radar.publication.models import PublicationCommand
    from dragon_radar.publication.repository import PublicationRepository
    from dragon_radar.publication.retention import RetentionService
    from dragon_radar.publication.service import PublicationService
    from dragon_radar.publication.tables import source_dependencies
    from dragon_radar.regulation.repository import RegulationRepository
    from dragon_radar.storage.repository import Forbidden

    event_source_id = "synthetic-empty-events"
    _register_event_source(repository, event_source_id)
    market = repository.publish(
        repository.import_batch(batch(), key="empty-event-source-market").id
    )
    regulations = RegulationRepository(database, repository, clock=lambda: NOW)
    regulations.register_rule(_rule(), key="empty-event-source-rule")
    snapshot = regulations.materialize(
        market_snapshot_id=market.id,
        observations=(_observation(),),
        event_source_completeness="complete",
        event_source_id=event_source_id,
        event_source_version="v1",
        key="empty-event-source-snapshot",
    )
    command = PublicationCommand(
        trade_date=DAYS[-1],
        market_snapshot_id=market.id,
        regulation_input_id=snapshot.id,
        disabled_domains=("topic", "news", "role"),
    )
    service = PublicationService(database, repository, clock=lambda: NOW)

    repository.revoke_source(event_source_id)
    with pytest.raises(Forbidden):
        service.publish(command, key="empty-event-source-denied-publication")

    _register_event_source(repository, event_source_id)
    publication = service.publish(
        command,
        key="empty-event-source-publication",
    )
    with database.engine.connect() as connection:
        event_dependency = (
            connection.execute(
                select(source_dependencies).where(
                    source_dependencies.c.publication_id == publication.id,
                    source_dependencies.c.derived_type == "regulation",
                    source_dependencies.c.archive_type == "regulation_event_snapshot",
                )
            )
            .mappings()
            .one()
        )
    assert event_dependency["source_id"] == event_source_id
    assert event_dependency["archive_id"] == snapshot.id
    assert (
        PublicationRepository(database, repository, clock=lambda: NOW).get_snapshot(
            publication.id
        )
        is not None
    )

    RetentionService(database, repository, clock=lambda: NOW).withdraw_display(
        event_source_id
    )
    with pytest.raises(Forbidden):
        PublicationRepository(database, repository, clock=lambda: NOW).get_snapshot(
            publication.id
        )


def test_unknown_event_completeness_retains_authorized_and_legacy_disclosures(
    database,
    repository,
):
    from dragon_radar.regulation.repository import RegulationRepository

    _register_event_source(repository, "synthetic-unknown-a")
    _register_event_source(repository, "synthetic-unknown-b")
    market = repository.publish(
        repository.import_batch(batch(), key="unknown-event-provenance-market").id
    )
    regulations = RegulationRepository(database, repository, clock=lambda: NOW)
    regulations.register_rule(_rule(), key="unknown-event-provenance-rule")
    events = (
        _event(
            event_id="unknown-source-a",
            announcement_id="notice-unknown-a",
            source_id="synthetic-unknown-a",
            source_version="v1",
        ),
        _event(
            event_id="unknown-source-b",
            announcement_id="notice-unknown-b",
            source_id="synthetic-unknown-b",
            source_version="v4",
        ),
    )
    for event in events:
        regulations.append_event(event, key=f"append-{event.event_id}")
    _insert_legacy_event(
        database,
        _event(
            event_id="unknown-legacy",
            announcement_id="notice-unknown-legacy",
        ),
    )

    snapshot = regulations.materialize(
        market_snapshot_id=market.id,
        observations=(_observation(),),
        key="unknown-event-provenance-snapshot",
    )
    context = regulations.snapshot_context(snapshot.id)

    assert context["snapshot"].event_source_completeness == "unknown"
    assert {event.event_id for event in context["events"]} == {
        "unknown-source-a",
        "unknown-source-b",
        "unknown-legacy",
    }
    assert {(event.source_id, event.source_version) for event in context["events"]} == {
        ("synthetic-unknown-a", "v1"),
        ("synthetic-unknown-b", "v4"),
        (None, None),
    }


def test_each_rule_version_consumes_only_its_own_observation(database, repository):
    from dragon_radar.regulation.repository import RegulationRepository

    market = repository.publish(
        repository.import_batch(
            batch(),
            key="regulation-independent-values-market",
        ).id
    )
    regulations = RegulationRepository(database, repository, clock=lambda: NOW)
    regulations.register_rule(_rule(), key="regulation-independent-values-v1")
    regulations.register_rule(
        _rule(rule_version="test-v2", window_days=20),
        key="regulation-independent-values-v2",
    )

    snapshot = regulations.materialize(
        market_snapshot_id=market.id,
        observations=(
            _observation(computed_value=Decimal("0.68")),
            _observation(
                rule_version="test-v2",
                computed_value=Decimal("0.90"),
            ),
        ),
        key="regulation-independent-values-snapshot",
    )
    calculations = {
        item.rule_version: item
        for item in regulations.snapshot_context(snapshot.id)["calculations"]
    }

    assert calculations["test-v1"].computed_value == Decimal("0.68")
    assert calculations["test-v1"].state == "approaching_alert"
    assert calculations["test-v2"].computed_value == Decimal("0.90")
    assert calculations["test-v2"].state == "triggered"


def test_materialize_sql_count_does_not_scale_with_rules_times_securities(
    database,
    repository,
):
    from sqlalchemy import event as sqlalchemy_event

    from dragon_radar.regulation.repository import RegulationRepository

    regulations = RegulationRepository(database, repository, clock=lambda: NOW)
    regulations.register_rule(
        _rule(rule_id="scale-rule-0"),
        key="regulation-scale-rule-0",
    )
    small_market = repository.publish(
        repository.import_batch(
            batch(),
            key="regulation-scale-small-market",
        ).id
    )

    statements = []

    def record_statement(
        connection,
        cursor,
        statement,
        parameters,
        context,
        executemany,
    ):
        statements.append((statement, executemany))

    sqlalchemy_event.listen(
        database.engine,
        "before_cursor_execute",
        record_statement,
    )
    try:
        regulations.materialize(
            market_snapshot_id=small_market.id,
            observations=(_observation(rule_id="scale-rule-0"),),
            key="regulation-scale-small-snapshot",
        )
    finally:
        sqlalchemy_event.remove(
            database.engine,
            "before_cursor_execute",
            record_statement,
        )
    small_count = len(statements)
    assert small_count == 16

    for index in range(1, 6):
        regulations.register_rule(
            _rule(rule_id=f"scale-rule-{index}"),
            key=f"regulation-scale-rule-{index}",
        )
    security_ids = tuple(f"S{index}" for index in range(6))
    large_market = repository.publish(
        repository.import_batch(
            batch(
                expected_security_ids=security_ids,
                security_states=tuple(
                    state(security_id) for security_id in security_ids
                ),
                bars=tuple(
                    bar(security_id=security_id, trade_date=day)
                    for security_id in security_ids
                    for day in DAYS
                ),
            ),
            key="regulation-scale-large-market",
        ).id
    )
    observations = tuple(
        _observation(
            rule_id=f"scale-rule-{rule_index}",
            security_id=security_id,
        )
        for rule_index in range(6)
        for security_id in security_ids
    )

    statements = []
    sqlalchemy_event.listen(
        database.engine,
        "before_cursor_execute",
        record_statement,
    )
    try:
        regulations.materialize(
            market_snapshot_id=large_market.id,
            observations=observations,
            key="regulation-scale-large-snapshot",
        )
    finally:
        sqlalchemy_event.remove(
            database.engine,
            "before_cursor_execute",
            record_statement,
        )

    assert len(statements) == small_count
    for table_name in (
        "regulation_snapshot_rules",
        "regulation_snapshot_inputs",
        "regulation_calculations",
    ):
        inserts = [
            executemany
            for statement, executemany in statements
            if statement.startswith(f"INSERT INTO {table_name} ")
        ]
        assert inserts == [True]


def test_status_sequence_watermark_wins_over_inverted_recorded_times(
    database, repository
):
    from dragon_radar.regulation.repository import RegulationRepository

    market = repository.publish(
        repository.import_batch(
            batch(),
            key="regulation-status-watermark-market",
        ).id
    )
    times = iter(
        (
            NOW - timedelta(hours=1),
            NOW + timedelta(hours=1),
            NOW + timedelta(hours=2),
        )
    )
    regulations = RegulationRepository(database, repository, clock=lambda: next(times))
    regulations.register_rule(
        _rule(active=False),
        key="regulation-status-watermark-rule",
    )
    regulations.activate_rule(
        "synthetic-upper",
        "test-v1",
        key="regulation-status-watermark-activation",
    )

    snapshot = regulations.materialize(
        market_snapshot_id=market.id,
        observations=(_observation(),),
        key="regulation-status-watermark-snapshot",
    )
    context = regulations.snapshot_context(snapshot.id)

    assert context["root"]["status_sequence_watermark"] == 1
    assert context["rule_bindings"][0]["active"] is True
    assert context["calculations"][0].state == "approaching_alert"


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
    _register_event_source(repository, "synthetic-event-feed")
    regulations.append_event(
        _event(
            source_id="synthetic-event-feed",
            source_version="fixture-v1",
        ),
        key="regulation-publication-event",
    )
    regulation_snapshot = regulations.materialize(
        market_snapshot_id=market.id,
        observations=(_observation(),),
        event_source_completeness="complete",
        event_source_id="synthetic-event-feed",
        event_source_version="fixture-v1",
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
    assert result.payload["event_source_completeness"] == "complete"
    assert result.payload["event_source_id"] == "synthetic-event-feed"
    assert result.payload["event_source_version"] == "fixture-v1"
    assert result.payload["completeness"]["expected"] == 1
    assert result.payload["calculations"][0]["state"] == "approaching_alert"
    assert result.payload["events"] == (
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
            "source_id": "synthetic-event-feed",
            "source_version": "fixture-v1",
        },
    )


def test_regulated_publication_list_prefetches_contexts_with_fixed_query_budget(
    database,
    repository,
):
    from sqlalchemy import event as sqlalchemy_event

    from dragon_radar.publication.models import PublicationCommand
    from dragon_radar.publication.repository import PublicationRepository
    from dragon_radar.publication.service import PublicationService
    from dragon_radar.regulation.repository import RegulationRepository

    regulations = RegulationRepository(database, repository, clock=lambda: NOW)
    _register_event_source(repository, "synthetic-list-event-source")
    regulations.register_rule(_rule(), key="regulation-list-rule")
    regulations.append_event(
        _event(
            source_id="synthetic-list-event-source",
            source_version="v1",
        ),
        key="regulation-list-event",
    )
    service = PublicationService(database, repository, clock=lambda: NOW)
    selection = PublicationRepository(database, repository, clock=lambda: NOW)

    def publish(index):
        market = repository.publish(
            repository.import_batch(
                batch(),
                key=f"regulation-list-market-{index}",
            ).id
        )
        regulation = regulations.materialize(
            market_snapshot_id=market.id,
            observations=(_observation(),),
            event_source_completeness="complete",
            event_source_id="synthetic-list-event-source",
            event_source_version="v1",
            key=f"regulation-list-snapshot-{index}",
        )
        return service.publish(
            PublicationCommand(
                trade_date=DAYS[-1],
                market_snapshot_id=market.id,
                regulation_input_id=regulation.id,
                disabled_domains=("topic", "news", "role"),
            ),
            key=f"regulation-list-publication-{index}",
        )

    def measured_list(limit):
        statements = []

        def record_statement(
            connection,
            cursor,
            statement,
            parameters,
            context,
            executemany,
        ):
            statements.append(statement)

        sqlalchemy_event.listen(
            database.engine,
            "before_cursor_execute",
            record_statement,
        )
        try:
            listed = selection.list_snapshots(limit=limit)
        finally:
            sqlalchemy_event.remove(
                database.engine,
                "before_cursor_execute",
                record_statement,
            )
        return listed, len(statements)

    publish(0)
    one, one_count = measured_list(1)
    expected_ids = {one[0].id}
    for index in range(1, 4):
        expected_ids.add(publish(index).id)
    many, many_count = measured_list(4)

    assert {item.id for item in many} == expected_ids
    for item in many:
        result = item.result("regulation")
        assert result.payload["snapshot_id"] == str(result.input_id)
        assert result.payload["market_snapshot_id"] == str(
            item.publication.market_snapshot_id
        )
        assert result.payload["calculations"][0]["state"] == "approaching_alert"
    assert one_count == many_count
    assert many_count == 23


def test_market_only_publication_list_keeps_thirteen_query_budget(
    database,
    repository,
):
    from sqlalchemy import event as sqlalchemy_event

    from dragon_radar.publication.models import PublicationCommand
    from dragon_radar.publication.repository import PublicationRepository
    from dragon_radar.publication.service import PublicationService

    market = repository.publish(
        repository.import_batch(
            batch(),
            key="market-only-query-budget-batch",
        ).id
    )
    PublicationService(database, repository, clock=lambda: NOW).publish(
        PublicationCommand(
            trade_date=DAYS[-1],
            market_snapshot_id=market.id,
            disabled_domains=("topic", "news", "regulation", "role"),
        ),
        key="market-only-query-budget-publication",
    )
    statements = []

    def record_statement(
        connection,
        cursor,
        statement,
        parameters,
        context,
        executemany,
    ):
        statements.append(statement)

    sqlalchemy_event.listen(database.engine, "before_cursor_execute", record_statement)
    try:
        PublicationRepository(
            database,
            repository,
            clock=lambda: NOW,
        ).list_snapshots(limit=1)
    finally:
        sqlalchemy_event.remove(
            database.engine,
            "before_cursor_execute",
            record_statement,
        )

    assert len(statements) == 13


def test_prefetched_regulation_publications_still_recheck_payload_and_lineage(
    database,
    repository,
    monkeypatch,
):
    from sqlalchemy import insert, select

    from dragon_radar.publication.models import PublicationCommand
    from dragon_radar.publication.repository import PublicationRepository
    from dragon_radar.publication.service import PublicationService
    from dragon_radar.publication.tables import source_dependencies
    from dragon_radar.regulation.repository import RegulationRepository
    from dragon_radar.storage.repository import Forbidden

    market = repository.publish(
        repository.import_batch(
            batch(),
            key="regulation-prefetch-review-market",
        ).id
    )
    regulations = RegulationRepository(database, repository, clock=lambda: NOW)
    regulations.register_rule(_rule(), key="regulation-prefetch-review-rule")
    regulation = regulations.materialize(
        market_snapshot_id=market.id,
        observations=(_observation(),),
        key="regulation-prefetch-review-snapshot",
    )
    publication = PublicationService(
        database,
        repository,
        clock=lambda: NOW,
    ).publish(
        PublicationCommand(
            trade_date=DAYS[-1],
            market_snapshot_id=market.id,
            regulation_input_id=regulation.id,
            disabled_domains=("topic", "news", "role"),
        ),
        key="regulation-prefetch-review-publication",
    )
    selection = PublicationRepository(database, repository, clock=lambda: NOW)
    materialize_context = RegulationRepository.materialize_context

    def forged_materialization(context):
        expected = materialize_context(context)
        return expected.model_copy(
            update={
                "payload": {
                    **expected.payload,
                    "snapshot_id": str(uuid4()),
                }
            }
        )

    monkeypatch.setattr(
        RegulationRepository,
        "materialize_context",
        staticmethod(forged_materialization),
    )
    with pytest.raises(Forbidden, match="regulation payload"):
        selection.get_snapshot(publication.id)
    monkeypatch.setattr(
        RegulationRepository,
        "materialize_context",
        staticmethod(materialize_context),
    )
    other_market = repository.publish(
        repository.import_batch(
            batch(),
            key="regulation-prefetch-review-other-market",
        ).id
    )

    with database.engine.begin() as connection:
        dependency = (
            connection.execute(
                select(source_dependencies).where(
                    source_dependencies.c.publication_id == publication.id,
                    source_dependencies.c.derived_type == "regulation",
                )
            )
            .mappings()
            .one()
        )
        duplicate = dict(dependency)
        duplicate.pop("dependency_sequence")
        duplicate["id"] = uuid4()
        duplicate["archive_id"] = other_market.batch_id
        connection.execute(insert(source_dependencies).values(**duplicate))

    with pytest.raises(Forbidden, match="regulation lineage"):
        selection.get_snapshot(publication.id)


def _stored_rule_values(**changes):
    values = _rule(
        active=False,
        review_switch="pending",
        review_record=None,
        test_cases=(),
        regression_passed=False,
        official_source_url=None,
    ).model_dump(mode="json")
    values.update(
        id=uuid4(),
        created_at=NOW,
        **changes,
    )
    return values


def _materialized_regulation_fixture(database, repository, *, suffix):
    from dragon_radar.regulation.repository import RegulationRepository

    market = repository.publish(
        repository.import_batch(
            batch(),
            key=f"regulation-integrity-market-{suffix}",
        ).id
    )
    regulations = RegulationRepository(database, repository, clock=lambda: NOW)
    regulations.register_rule(
        _rule(),
        key=f"regulation-integrity-rule-{suffix}",
    )
    regulations.append_event(
        _event(
            event_id=f"event-{suffix}",
            announcement_id=f"notice-{suffix}",
            source_id="synthetic",
            source_version="fixture-v1",
        ),
        key=f"regulation-integrity-event-{suffix}",
    )
    snapshot = regulations.materialize(
        market_snapshot_id=market.id,
        observations=(_observation(),),
        key=f"regulation-integrity-snapshot-{suffix}",
    )
    return market, regulations, snapshot


def _insert_snapshot_rule(
    connection,
    snapshot,
    *,
    suffix,
    bind=True,
):
    from sqlalchemy import func, insert, select

    from dragon_radar.regulation.tables import (
        regulation_rules,
        regulation_snapshot_rules,
    )

    values = _rule(rule_id=f"integrity-{suffix}").model_dump(mode="json")
    values.update(id=uuid4(), created_at=NOW)
    connection.execute(insert(regulation_rules).values(**values))
    if bind:
        position = connection.scalar(
            select(
                func.coalesce(func.max(regulation_snapshot_rules.c.position), -1)
            ).where(regulation_snapshot_rules.c.snapshot_id == snapshot.id)
        )
        connection.execute(
            insert(regulation_snapshot_rules).values(
                snapshot_id=snapshot.id,
                rule_record_id=values["id"],
                position=position + 1,
                active=True,
            )
        )
    return values["id"]


def _stored_input_values(snapshot, rule_record_id, **changes):
    return {
        "snapshot_id": snapshot.id,
        "rule_record_id": rule_record_id,
        "security_id": "A",
        "trade_date": snapshot.trade_date,
        "exchange": "TEST",
        "board": "synthetic_test",
        "security_status": "regular",
        "computed_value": Decimal("0.68"),
        "input_state": "complete",
        **changes,
    }


def test_postgres_rejects_valid_looking_input_and_calculation_with_forged_state(
    database,
    repository,
):
    from sqlalchemy import insert
    from sqlalchemy.exc import DBAPIError

    from dragon_radar.regulation.tables import (
        regulation_calculations,
        regulation_snapshot_inputs,
    )

    _market, _regulations, snapshot = _materialized_regulation_fixture(
        database,
        repository,
        suffix="forged-state",
    )
    with pytest.raises(DBAPIError), database.engine.begin() as connection:
        rule_record_id = _insert_snapshot_rule(
            connection,
            snapshot,
            suffix="forged-state",
        )
        connection.execute(
            insert(regulation_snapshot_inputs).values(
                **_stored_input_values(
                    snapshot,
                    rule_record_id,
                    exchange="OTHER",
                )
            )
        )
        connection.execute(
            insert(regulation_calculations).values(
                id=uuid4(),
                snapshot_id=snapshot.id,
                rule_record_id=rule_record_id,
                security_id="A",
                trade_date=snapshot.trade_date,
                computed_value=Decimal("0.68"),
                input_state="complete",
                state="pending",
                reasons=["rule_not_applicable"],
            )
        )


@pytest.mark.parametrize(
    "changes",
    [
        {"rule_record_id": None},
        {"security_id": "NOT-IN-EXPECTED-POOL"},
        {"exchange": "OTHER"},
        {"board": "other_board"},
        {"security_status": "suspended"},
    ],
    ids=(
        "rule-not-bound-to-snapshot",
        "security-outside-expected-pool",
        "exchange-mismatch",
        "board-mismatch",
        "security-status-mismatch",
    ),
)
def test_postgres_rejects_regulation_input_outside_immutable_snapshot_context(
    database,
    repository,
    changes,
):
    from sqlalchemy import insert
    from sqlalchemy.exc import DBAPIError

    from dragon_radar.regulation.tables import regulation_snapshot_inputs

    _market, _regulations, snapshot = _materialized_regulation_fixture(
        database,
        repository,
        suffix=uuid4().hex[:12],
    )
    with pytest.raises(DBAPIError), database.engine.begin() as connection:
        rule_record_id = _insert_snapshot_rule(
            connection,
            snapshot,
            suffix=f"input-{uuid4().hex[:8]}",
            bind="rule_record_id" not in changes,
        )
        values = _stored_input_values(snapshot, rule_record_id)
        values.update(changes)
        if values["rule_record_id"] is None:
            values["rule_record_id"] = rule_record_id
        connection.execute(insert(regulation_snapshot_inputs).values(**values))


@pytest.mark.parametrize(
    "security_states",
    [
        (),
        (
            state(),
            state(name="Conflicting Synthetic A", source_version="s2"),
        ),
    ],
    ids=("missing-state", "conflicting-states"),
)
@pytest.mark.parametrize("input_state", ["missing", "conflict"])
def test_postgres_ambiguous_market_state_allows_only_fail_closed_input(
    database,
    repository,
    security_states,
    input_state,
):
    from sqlalchemy import insert, select
    from sqlalchemy.exc import DBAPIError

    from dragon_radar.regulation.repository import RegulationRepository
    from dragon_radar.regulation.tables import (
        regulation_rules,
        regulation_snapshot_inputs,
        regulation_snapshot_rules,
        regulation_snapshots,
    )
    from dragon_radar.storage.models import snapshots

    archive = repository.import_batch(
        batch(security_states=security_states),
        key=f"ambiguous-state-archive-{len(security_states)}-{input_state}",
    )
    regulations = RegulationRepository(database, repository, clock=lambda: NOW)
    regulations.register_rule(
        _rule(),
        key=f"ambiguous-state-rule-{len(security_states)}-{input_state}",
    )
    market_snapshot_id = uuid4()
    snapshot_id = uuid4()
    with database.engine.begin() as connection:
        connection.execute(
            insert(snapshots).values(
                id=market_snapshot_id,
                batch_id=archive.id,
                source_id="synthetic",
                trade_date=DAYS[-1],
                revision=1,
                supersedes=None,
                manifest={},
                published_at=NOW,
            )
        )
        rule_record_id = connection.scalar(
            select(regulation_rules.c.id).where(
                regulation_rules.c.rule_id == "synthetic-upper"
            )
        )
        connection.execute(
            insert(regulation_snapshots).values(
                id=snapshot_id,
                idempotency_key=f"ambiguous-state-{len(security_states)}-{input_state}",
                command_hash="a" * 64,
                market_snapshot_id=market_snapshot_id,
                trade_date=DAYS[-1],
                as_of=NOW,
                status_sequence_watermark=0,
                created_at=NOW,
            )
        )
        connection.execute(
            insert(regulation_snapshot_rules).values(
                snapshot_id=snapshot_id,
                rule_record_id=rule_record_id,
                position=0,
                active=True,
            )
        )
        connection.execute(
            insert(regulation_snapshot_inputs).values(
                snapshot_id=snapshot_id,
                rule_record_id=rule_record_id,
                security_id="A",
                trade_date=DAYS[-1],
                exchange="unknown",
                board="unknown",
                security_status="unknown",
                computed_value=None,
                input_state=input_state,
            )
        )

    with pytest.raises(DBAPIError), database.engine.begin() as connection:
        second_rule_record_id = _insert_snapshot_rule(
            connection,
            SimpleNamespace(id=snapshot_id),
            suffix=f"ambiguous-{len(security_states)}",
        )
        connection.execute(
            insert(regulation_snapshot_inputs).values(
                snapshot_id=snapshot_id,
                rule_record_id=second_rule_record_id,
                security_id="A",
                trade_date=DAYS[-1],
                exchange="TEST",
                board="synthetic_test",
                security_status="regular",
                computed_value=Decimal("0.68"),
                input_state="complete",
            )
        )


@pytest.mark.parametrize(
    ("rule_changes", "field"),
    [
        ({"threshold": Decimal("Infinity")}, "threshold-positive-infinity"),
        (
            {
                "direction": "lower",
                "threshold": Decimal("-Infinity"),
                "warning_threshold": Decimal("0.35"),
            },
            "threshold-negative-infinity",
        ),
        ({"warning_threshold": Decimal("-Infinity")}, "warning-negative-infinity"),
        (
            {
                "direction": "lower",
                "threshold": Decimal("0.30"),
                "warning_threshold": Decimal("Infinity"),
            },
            "warning-positive-infinity",
        ),
    ],
)
def test_postgres_rejects_nonfinite_regulation_rule_thresholds(
    database,
    rule_changes,
    field,
):
    from sqlalchemy import insert
    from sqlalchemy.exc import DBAPIError

    from dragon_radar.regulation.tables import regulation_rules

    values = _stored_rule_values(
        rule_id=f"nonfinite-{field}"[:30],
        **rule_changes,
    )
    with pytest.raises(DBAPIError), database.engine.begin() as connection:
        connection.execute(insert(regulation_rules).values(**values))


@pytest.mark.parametrize("value", ["NaN", "Infinity", "-Infinity"])
def test_postgres_rejects_nonfinite_regulation_snapshot_inputs(
    database,
    repository,
    value,
):
    from sqlalchemy import insert
    from sqlalchemy.exc import DBAPIError

    from dragon_radar.regulation.tables import regulation_snapshot_inputs

    _market, _regulations, snapshot = _materialized_regulation_fixture(
        database,
        repository,
        suffix=uuid4().hex[:12],
    )
    with pytest.raises(DBAPIError), database.engine.begin() as connection:
        rule_record_id = _insert_snapshot_rule(
            connection,
            snapshot,
            suffix=f"input-{value}",
        )
        connection.execute(
            insert(regulation_snapshot_inputs).values(
                **_stored_input_values(
                    snapshot,
                    rule_record_id,
                    computed_value=Decimal(value),
                )
            )
        )


@pytest.mark.parametrize(
    ("value", "state"),
    [
        ("NaN", "idle"),
        ("Infinity", "triggered"),
        ("-Infinity", "idle"),
    ],
)
def test_postgres_rejects_nonfinite_regulation_calculations(
    database,
    repository,
    value,
    state,
):
    from sqlalchemy import insert, text
    from sqlalchemy.exc import DBAPIError

    from dragon_radar.regulation.tables import (
        regulation_calculations,
        regulation_snapshot_inputs,
    )

    _market, _regulations, snapshot = _materialized_regulation_fixture(
        database,
        repository,
        suffix=uuid4().hex[:12],
    )
    with pytest.raises(DBAPIError), database.engine.begin() as connection:
        rule_record_id = _insert_snapshot_rule(
            connection,
            snapshot,
            suffix=f"calc-{uuid4().hex[:8]}",
        )
        connection.execute(
            text(
                "ALTER TABLE regulation_snapshot_inputs "
                "DISABLE TRIGGER regulation_snapshot_inputs_guard"
            )
        )
        connection.execute(
            insert(regulation_snapshot_inputs).values(
                **_stored_input_values(
                    snapshot,
                    rule_record_id,
                    computed_value=Decimal(value),
                )
            )
        )
        connection.execute(
            text(
                "ALTER TABLE regulation_snapshot_inputs "
                "ENABLE TRIGGER regulation_snapshot_inputs_guard"
            )
        )
        connection.execute(
            insert(regulation_calculations).values(
                id=uuid4(),
                snapshot_id=snapshot.id,
                rule_record_id=rule_record_id,
                security_id="A",
                trade_date=snapshot.trade_date,
                computed_value=Decimal(value),
                input_state="complete",
                state=state,
                reasons=[],
            )
        )


@pytest.mark.parametrize("token", ["NaN", "Infinity", "-Infinity"])
def test_postgres_jsonb_rejects_nonfinite_json_number_tokens(database, token):
    from sqlalchemy import text
    from sqlalchemy.exc import DBAPIError

    with pytest.raises(DBAPIError), database.engine.begin() as connection:
        connection.execute(text(f"""SELECT '{{"value": {token}}}'::jsonb"""))


@pytest.mark.parametrize("publication_status", ["published", "revoked"])
@pytest.mark.parametrize(
    "child_table",
    [
        "regulation_snapshot_rules",
        "regulation_snapshot_inputs",
        "regulation_calculations",
        "regulation_snapshot_events",
    ],
)
def test_published_regulation_snapshot_rejects_new_children(
    database,
    repository,
    publication_status,
    child_table,
):
    from sqlalchemy import insert, update
    from sqlalchemy.exc import DBAPIError

    from dragon_radar.publication.models import PublicationCommand
    from dragon_radar.publication.service import PublicationService
    from dragon_radar.publication.tables import publications
    from dragon_radar.regulation.tables import (
        regulation_calculations,
        regulation_events,
        regulation_snapshot_events,
        regulation_snapshot_inputs,
        regulation_snapshot_rules,
    )

    market, _regulations, snapshot = _materialized_regulation_fixture(
        database,
        repository,
        suffix=uuid4().hex[:12],
    )
    event_id = f"sealed-{uuid4().hex[:20]}"
    with database.engine.begin() as connection:
        calculation_rule_id = _insert_snapshot_rule(
            connection,
            snapshot,
            suffix=f"calc-{uuid4().hex[:8]}",
        )
        connection.execute(
            insert(regulation_snapshot_inputs).values(
                **_stored_input_values(snapshot, calculation_rule_id)
            )
        )
        input_rule_id = _insert_snapshot_rule(
            connection,
            snapshot,
            suffix=f"input-{uuid4().hex[:8]}",
        )
        unbound_rule_id = _insert_snapshot_rule(
            connection,
            snapshot,
            suffix=f"rule-{uuid4().hex[:8]}",
            bind=False,
        )
        event_values = _event(
            event_id=event_id,
            announcement_id=f"notice-{uuid4().hex[:8]}",
        ).model_dump(mode="json")
        event_values["created_at"] = NOW
        connection.execute(insert(regulation_events).values(**event_values))

    publication = PublicationService(
        database,
        repository,
        clock=lambda: NOW,
    ).publish(
        PublicationCommand(
            trade_date=DAYS[-1],
            market_snapshot_id=market.id,
            regulation_input_id=snapshot.id,
            disabled_domains=("topic", "news", "role"),
        ),
        key=f"sealed-{publication_status}-{child_table}",
    )
    if publication_status == "revoked":
        with database.engine.begin() as connection:
            connection.execute(
                update(publications)
                .where(publications.c.id == publication.id)
                .values(status="revoked")
            )

    statements = {
        "regulation_snapshot_rules": insert(regulation_snapshot_rules).values(
            snapshot_id=snapshot.id,
            rule_record_id=unbound_rule_id,
            position=99,
            active=True,
        ),
        "regulation_snapshot_inputs": insert(regulation_snapshot_inputs).values(
            **_stored_input_values(snapshot, input_rule_id)
        ),
        "regulation_calculations": insert(regulation_calculations).values(
            id=uuid4(),
            snapshot_id=snapshot.id,
            rule_record_id=calculation_rule_id,
            security_id="A",
            trade_date=snapshot.trade_date,
            computed_value=Decimal("0.68"),
            input_state="complete",
            state="approaching_alert",
            reasons=[],
        ),
        "regulation_snapshot_events": insert(regulation_snapshot_events).values(
            snapshot_id=snapshot.id,
            event_id=event_id,
            position=99,
        ),
    }
    with pytest.raises(DBAPIError), database.engine.begin() as connection:
        connection.execute(statements[child_table])


def test_publication_and_child_append_serialize_on_regulation_snapshot(
    database,
    repository,
):
    from sqlalchemy import event as sqlalchemy_event
    from sqlalchemy import insert
    from sqlalchemy.exc import DBAPIError

    from dragon_radar.publication.models import PublicationCommand
    from dragon_radar.publication.repository import PublicationRepository
    from dragon_radar.publication.service import PublicationService
    from dragon_radar.regulation.repository import RegulationRepository
    from dragon_radar.regulation.tables import (
        regulation_events,
        regulation_snapshot_events,
    )

    market, _regulations, snapshot = _materialized_regulation_fixture(
        database,
        repository,
        suffix="publication-child-race",
    )
    event_id = f"race-{uuid4().hex[:20]}"
    event_values = _event(
        event_id=event_id,
        announcement_id=f"notice-{uuid4().hex[:8]}",
    ).model_dump(mode="json")
    event_values["created_at"] = NOW
    with database.engine.begin() as connection:
        connection.execute(insert(regulation_events).values(**event_values))

    insert_started = Barrier(2)
    child_inserted = Barrier(2)
    coordination = {"publication_locks_snapshot": False}

    def coordinate_publication(
        connection,
        cursor,
        statement,
        parameters,
        context,
        executemany,
    ):
        normalized = statement.upper()
        if (
            "FROM REGULATION_SNAPSHOTS" in normalized
            and "WHERE REGULATION_SNAPSHOTS.ID IN" in normalized
        ):
            coordination["publication_locks_snapshot"] = "FOR UPDATE" in normalized
        if (
            "FROM REGULATION_SNAPSHOT_EVENTS JOIN REGULATION_EVENTS" in normalized
            and "ORDER BY REGULATION_SNAPSHOT_EVENTS.SNAPSHOT_ID" in normalized
        ):
            insert_started.wait(timeout=10)
            if not coordination["publication_locks_snapshot"]:
                child_inserted.wait(timeout=10)

    def coordinate_child_insert(
        connection,
        cursor,
        statement,
        parameters,
        context,
        executemany,
    ):
        if "INSERT INTO REGULATION_SNAPSHOT_EVENTS" not in statement.upper():
            return
        insert_started.wait(timeout=10)

    def confirm_child_insert(
        connection,
        cursor,
        statement,
        parameters,
        context,
        executemany,
    ):
        if (
            "INSERT INTO REGULATION_SNAPSHOT_EVENTS" in statement.upper()
            and not coordination["publication_locks_snapshot"]
        ):
            child_inserted.wait(timeout=10)

    def publish():
        return PublicationService(
            database,
            repository,
            clock=lambda: NOW,
        ).publish(
            PublicationCommand(
                trade_date=DAYS[-1],
                market_snapshot_id=market.id,
                regulation_input_id=snapshot.id,
                disabled_domains=("topic", "news", "role"),
            ),
            key="regulation-publication-child-race",
        )

    def append_child():
        try:
            with database.engine.begin() as connection:
                connection.execute(
                    insert(regulation_snapshot_events).values(
                        snapshot_id=snapshot.id,
                        event_id=event_id,
                        position=1,
                    )
                )
        except DBAPIError:
            return "rejected"
        return "inserted"

    sqlalchemy_event.listen(
        database.engine,
        "before_cursor_execute",
        coordinate_publication,
    )
    sqlalchemy_event.listen(
        database.engine,
        "before_cursor_execute",
        coordinate_child_insert,
    )
    sqlalchemy_event.listen(
        database.engine,
        "after_cursor_execute",
        confirm_child_insert,
    )
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            publication_future = pool.submit(publish)
            child_future = pool.submit(append_child)
            publication = publication_future.result(timeout=20)
            child_result = child_future.result(timeout=20)
    finally:
        sqlalchemy_event.remove(
            database.engine,
            "before_cursor_execute",
            coordinate_publication,
        )
        sqlalchemy_event.remove(
            database.engine,
            "before_cursor_execute",
            coordinate_child_insert,
        )
        sqlalchemy_event.remove(
            database.engine,
            "after_cursor_execute",
            confirm_child_insert,
        )

    stored = next(
        item
        for item in PublicationRepository(database, repository).list_results(
            publication.id
        )
        if item.domain == "regulation"
    )
    current = RegulationRepository.materialize_context(
        RegulationRepository(
            database,
            repository,
            clock=lambda: NOW,
        ).snapshot_context(snapshot.id)
    )

    assert child_result in {"inserted", "rejected"}
    assert stored.payload == current.payload
    assert stored.coverage == current.coverage
    assert stored.quality_flags == current.quality_flags


def test_regulation_publish_reuses_outer_transaction_for_snapshot_context(
    database,
    repository,
    monkeypatch,
):
    from dragon_radar.publication.models import PublicationCommand
    from dragon_radar.publication.service import PublicationService

    market, _regulations, snapshot = _materialized_regulation_fixture(
        database,
        repository,
        suffix="single-connection",
    )
    service = PublicationService(database, repository, clock=lambda: NOW)

    def reject_second_transaction(_snapshot_id):
        raise AssertionError("publish requested a second database connection")

    monkeypatch.setattr(
        service.regulations, "snapshot_context", reject_second_transaction
    )

    publication = service.publish(
        PublicationCommand(
            trade_date=DAYS[-1],
            market_snapshot_id=market.id,
            regulation_input_id=snapshot.id,
            disabled_domains=("topic", "news", "role"),
        ),
        key="regulation-single-connection",
    )

    assert publication.regulation_input_id == snapshot.id


def test_materialize_context_consumes_regulation_inputs_once():
    from dragon_radar.regulation.engine import evaluate_rule
    from dragon_radar.regulation.models import RegulationSnapshot
    from dragon_radar.regulation.repository import RegulationRepository

    class OnePass:
        def __init__(self, values):
            self.values = values
            self.iterations = 0

        def __iter__(self):
            self.iterations += 1
            if self.iterations > 1:
                raise AssertionError("regulation inputs were scanned more than once")
            return iter(self.values)

    observations = (
        _observation(security_id="A"),
        _observation(security_id="B"),
    )
    inputs = OnePass(observations)
    context = {
        "snapshot": RegulationSnapshot(
            id=uuid4(),
            market_snapshot_id=uuid4(),
            trade_date=DAYS[-1],
            as_of=NOW,
            created_at=NOW,
        ),
        "calculations": tuple(
            evaluate_rule(_rule(), observation) for observation in observations
        ),
        "events": (),
        "inputs": inputs,
        "expected_security_ids": ("A", "B"),
        "rule_bindings": ({"active": True},),
        "rules": (_rule(),),
    }

    result = RegulationRepository.materialize_context(context)

    assert result.coverage == {
        "expected": 2,
        "valid": 2,
        "legal_excluded": 0,
        "missing": 0,
    }
    assert inputs.iterations == 1


@pytest.mark.parametrize(
    (
        "security_id",
        "input_value",
        "input_state",
        "stored_value",
        "stored_input_state",
        "stored_state",
        "stored_reasons",
    ),
    [
        (
            "forged-trigger",
            Decimal("0.60"),
            "complete",
            Decimal("0.60"),
            "complete",
            "triggered",
            [],
        ),
        ("forged-idle", None, "missing", None, "missing", "idle", []),
        (
            "forged-reasons",
            None,
            "missing",
            None,
            "missing",
            "pending",
            ["wrong_reason"],
        ),
        (
            "forged-input-state",
            Decimal("0.68"),
            "complete",
            Decimal("0.68"),
            "conflict",
            "pending",
            ["input_conflict"],
        ),
        (
            "forged-value",
            Decimal("0.68"),
            "complete",
            Decimal("0.69"),
            "complete",
            "approaching_alert",
            [],
        ),
    ],
)
def test_postgres_recomputes_and_rejects_forged_regulation_calculations(
    database,
    repository,
    security_id,
    input_value,
    input_state,
    stored_value,
    stored_input_state,
    stored_state,
    stored_reasons,
):
    from sqlalchemy import insert, select
    from sqlalchemy.exc import DBAPIError

    from dragon_radar.regulation.tables import (
        regulation_calculations,
        regulation_snapshot_inputs,
        regulation_snapshot_rules,
    )

    _market, _regulations, snapshot = _materialized_regulation_fixture(
        database,
        repository,
        suffix=security_id,
    )
    with pytest.raises(DBAPIError), database.engine.begin() as connection:
        rule_record_id = connection.scalar(
            select(regulation_snapshot_rules.c.rule_record_id).where(
                regulation_snapshot_rules.c.snapshot_id == snapshot.id
            )
        )
        connection.execute(
            insert(regulation_snapshot_inputs).values(
                snapshot_id=snapshot.id,
                rule_record_id=rule_record_id,
                security_id=security_id,
                trade_date=snapshot.trade_date,
                exchange="TEST",
                board="synthetic_test",
                security_status="regular",
                computed_value=input_value,
                input_state=input_state,
            )
        )
        connection.execute(
            insert(regulation_calculations).values(
                id=uuid4(),
                snapshot_id=snapshot.id,
                rule_record_id=rule_record_id,
                security_id=security_id,
                trade_date=snapshot.trade_date,
                computed_value=stored_value,
                input_state=stored_input_state,
                state=stored_state,
                reasons=stored_reasons,
            )
        )


@pytest.mark.parametrize(
    ("field", "invalid_value"),
    [
        ("trade_date", DAYS[-2]),
        ("as_of", NOW + timedelta(seconds=1)),
        ("status_sequence_watermark", 2),
    ],
)
def test_postgres_rejects_regulation_snapshot_root_not_matching_market_cut(
    database,
    repository,
    field,
    invalid_value,
):
    from sqlalchemy import insert
    from sqlalchemy.exc import DBAPIError

    from dragon_radar.regulation.tables import regulation_snapshots

    market = repository.publish(
        repository.import_batch(
            batch(),
            key=f"regulation-root-market-{field}",
        ).id
    )
    values = {
        "id": uuid4(),
        "idempotency_key": f"regulation-root-{field}",
        "command_hash": "a" * 64,
        "market_snapshot_id": market.id,
        "trade_date": DAYS[-1],
        "as_of": NOW,
        "status_sequence_watermark": 0,
        "created_at": NOW,
    }
    values[field] = invalid_value

    with pytest.raises(DBAPIError), database.engine.begin() as connection:
        connection.execute(insert(regulation_snapshots).values(**values))


def test_postgres_allows_structurally_valid_inactive_pending_rule_without_source(
    database,
):
    from sqlalchemy import insert

    from dragon_radar.regulation.tables import regulation_rules

    with database.engine.begin() as connection:
        connection.execute(insert(regulation_rules).values(**_stored_rule_values()))


@pytest.mark.parametrize(
    ("field", "invalid_value"),
    [
        ("effective_to", DAYS[0]),
        ("warning_threshold", Decimal("0.70")),
        ("benchmark_index", ""),
        ("normal_anomaly_count_mode", " "),
        ("official_source_url", ""),
        ("official_source_url", "http://exchange.example.test/rule"),
        ("official_source_url", "https://user@example.test/rule"),
        ("review_record", []),
        ("review_record", {"prepared_by": "author"}),
        ("test_cases", {}),
        ("test_cases", [{"name": "case-without-result"}]),
    ],
)
def test_postgres_rejects_structurally_invalid_inactive_rules(
    database,
    field,
    invalid_value,
):
    from sqlalchemy import insert
    from sqlalchemy.exc import DBAPIError

    from dragon_radar.regulation.tables import regulation_rules

    values = _stored_rule_values(**{field: invalid_value})
    with pytest.raises(DBAPIError), database.engine.begin() as connection:
        connection.execute(insert(regulation_rules).values(**values))


@pytest.mark.parametrize(
    ("field", "invalid_value"),
    [
        ("effective_to", DAYS[-1]),
        ("announcement_url", ""),
        ("announcement_url", "http://exchange.example.test/notice"),
        ("announcement_url", "https://user@example.test/notice"),
    ],
)
def test_postgres_rejects_structurally_invalid_events(
    database,
    field,
    invalid_value,
):
    from sqlalchemy import insert
    from sqlalchemy.exc import DBAPIError

    from dragon_radar.regulation.repository import RegulationRepository
    from dragon_radar.regulation.tables import regulation_events

    regulations = RegulationRepository(database, clock=lambda: NOW)
    regulations.register_rule(
        _rule(active=False),
        key=f"regulation-event-parent-{field}",
    )
    values = _event().model_dump(mode="json")
    values.update(created_at=NOW, **{field: invalid_value})
    with pytest.raises(DBAPIError), database.engine.begin() as connection:
        connection.execute(insert(regulation_events).values(**values))


REGULATION_TABLES = (
    "regulation_rules",
    "regulation_rule_statuses",
    "regulation_events",
    "regulation_snapshots",
    "regulation_snapshot_rules",
    "regulation_snapshot_inputs",
    "regulation_calculations",
    "regulation_snapshot_events",
    "regulation_commands",
)


@pytest.mark.parametrize("table_name", REGULATION_TABLES)
def test_every_regulation_table_rejects_update_and_delete(
    database,
    repository,
    table_name,
):
    from sqlalchemy import text
    from sqlalchemy.exc import DBAPIError

    _materialized_regulation_fixture(database, repository, suffix=table_name)

    for statement in (
        f"UPDATE {table_name} SET ",
        f"DELETE FROM {table_name}",
    ):
        sql = (
            statement + "idempotency_key = idempotency_key"
            if table_name in {"regulation_snapshots", "regulation_commands"}
            and statement.startswith("UPDATE")
            else statement + "event_id = event_id"
            if table_name in {"regulation_events", "regulation_snapshot_events"}
            and statement.startswith("UPDATE")
            else statement + "rule_record_id = rule_record_id"
            if table_name
            in {
                "regulation_rule_statuses",
                "regulation_snapshot_rules",
                "regulation_snapshot_inputs",
                "regulation_calculations",
            }
            and statement.startswith("UPDATE")
            else statement + "rule_id = rule_id"
            if table_name == "regulation_rules" and statement.startswith("UPDATE")
            else statement
        )
        with pytest.raises(DBAPIError), database.engine.begin() as connection:
            connection.execute(text(sql))


@pytest.mark.parametrize(
    "table_name",
    ("regulation_rules", "regulation_snapshots", "regulation_commands"),
)
def test_representative_regulation_table_families_reject_truncate(
    database,
    table_name,
):
    from sqlalchemy import text
    from sqlalchemy.exc import DBAPIError

    with pytest.raises(DBAPIError), database.engine.begin() as connection:
        connection.execute(text(f"TRUNCATE TABLE {table_name}"))


def test_regulation_schema_revision_is_0009b():
    from dragon_radar.storage.database import SCHEMA_REVISION

    assert SCHEMA_REVISION == "0009b_reg_event_provenance"


def test_regulation_migration_head_is_0009b_and_reserves_0010_for_ai():
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    config = Config()
    config.set_main_option("script_location", "dragon_radar:migrations")

    script = ScriptDirectory.from_config(config)
    assert script.get_heads() == ["0009b_reg_event_provenance"]
    assert script.get_revision("0009b_reg_event_provenance").down_revision == (
        "0009a_reg_event_source"
    )


def test_regulation_event_source_migration_upgrades_existing_0009_schema(database_url):
    from alembic import command as alembic_command
    from alembic.config import Config
    from sqlalchemy import inspect, text

    from dragon_radar.storage.database import SCHEMA_REVISION, Database

    database = Database(database_url)
    config = Config()
    config.set_main_option("script_location", "dragon_radar:migrations")
    try:
        with database.engine.begin() as connection:
            config.attributes["connection"] = connection
            alembic_command.upgrade(config, "0009_regulation")
            connection.execute(
                text(
                    "ALTER TABLE regulation_snapshots "
                    "DROP CONSTRAINT IF EXISTS "
                    "regulation_snapshots_event_source_evidence, "
                    "DROP COLUMN IF EXISTS event_source_version, "
                    "DROP COLUMN IF EXISTS event_source_id, "
                    "DROP COLUMN IF EXISTS event_source_completeness"
                )
            )
            connection.execute(
                text(
                    "DROP INDEX IF EXISTS regulation_events_source; "
                    "ALTER TABLE regulation_events "
                    "DROP CONSTRAINT IF EXISTS "
                    "regulation_events_source_provenance, "
                    "DROP CONSTRAINT IF EXISTS "
                    "regulation_events_source_id_fkey, "
                    "DROP COLUMN IF EXISTS source_version, "
                    "DROP COLUMN IF EXISTS source_id"
                )
            )
            assert {
                column["name"]
                for column in inspect(connection).get_columns("regulation_snapshots")
            }.isdisjoint(
                {
                    "event_source_completeness",
                    "event_source_id",
                    "event_source_version",
                }
            )
            assert {
                column["name"]
                for column in inspect(connection).get_columns("regulation_events")
            }.isdisjoint({"source_id", "source_version"})
        database.migrate()
        with database.engine.connect() as connection:
            columns = {
                column["name"]: column
                for column in inspect(connection).get_columns("regulation_snapshots")
            }
            assert columns["event_source_completeness"]["nullable"] is False
            assert columns["event_source_completeness"]["default"] is not None
            assert columns["event_source_id"]["nullable"] is True
            assert columns["event_source_version"]["nullable"] is True
            assert (
                connection.scalar(text("SELECT version_num FROM alembic_version"))
                == SCHEMA_REVISION
            )
    finally:
        database.close()


@pytest.mark.parametrize(
    ("completeness", "source_id", "source_version"),
    (
        ("unknown", "synthetic-event-feed", None),
        ("unknown", None, "fixture-v1"),
        ("complete", None, None),
        ("complete", "", "fixture-v1"),
        ("complete", "synthetic-event-feed", ""),
    ),
)
def test_database_rejects_inconsistent_event_source_evidence(
    database,
    repository,
    completeness,
    source_id,
    source_version,
):
    from sqlalchemy import text
    from sqlalchemy.exc import DBAPIError

    _market, regulations, snapshot = _materialized_regulation_fixture(
        database,
        repository,
        suffix=f"src-{uuid4().hex[:8]}",
    )
    with (
        pytest.raises(
            DBAPIError,
            match="regulation_snapshots_event_source_evidence",
        ),
        database.engine.begin() as connection,
    ):
        connection.execute(
            text("""
                INSERT INTO regulation_snapshots (
                    id, idempotency_key, command_hash, market_snapshot_id,
                    trade_date, as_of, status_sequence_watermark, created_at,
                    event_source_completeness, event_source_id,
                    event_source_version
                )
                SELECT :id, :key, :command_hash, market_snapshot_id,
                       trade_date, as_of, status_sequence_watermark, created_at,
                       :completeness, :source_id, :source_version
                  FROM regulation_snapshots
                 WHERE id = :snapshot_id
            """),
            {
                "id": uuid4(),
                "key": f"invalid-event-source-{uuid4()}",
                "command_hash": "0" * 64,
                "completeness": completeness,
                "source_id": source_id,
                "source_version": source_version,
                "snapshot_id": snapshot.id,
            },
        )


def test_regulation_tables_are_append_only_and_migration_is_ready(database):
    from sqlalchemy import insert, inspect, text, update
    from sqlalchemy.exc import DBAPIError

    from dragon_radar.regulation.repository import RegulationRepository
    from dragon_radar.regulation.tables import regulation_rules
    from dragon_radar.storage.database import SCHEMA_REVISION

    regulations = RegulationRepository(database, clock=lambda: NOW)
    regulations.register_rule(_rule(), key="regulation-immutable-rule")

    assert SCHEMA_REVISION == "0009b_reg_event_provenance"
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
    snapshot_columns = {
        column["name"]: column
        for column in inspector.get_columns("regulation_snapshots")
    }
    assert snapshot_columns["event_source_completeness"]["nullable"] is False
    event_columns = {
        column["name"]: column for column in inspector.get_columns("regulation_events")
    }
    assert event_columns["source_id"]["nullable"] is True
    assert event_columns["source_version"]["nullable"] is True
    assert any(
        fk["referred_table"] == "source_grants"
        and fk["constrained_columns"] == ["source_id"]
        for fk in inspector.get_foreign_keys("regulation_events")
    )
    assert any(
        fk["referred_table"] == "regulation_snapshots"
        and fk["constrained_columns"] == ["regulation_input_id"]
        for fk in inspector.get_foreign_keys("publications")
    )
    regulation_binding_index = next(
        index
        for index in inspector.get_indexes("publications")
        if index["name"] == "publications_regulation_input"
    )
    assert regulation_binding_index["column_names"] == ["regulation_input_id"]
    assert (
        regulation_binding_index["dialect_options"]["postgresql_where"]
        == "(regulation_input_id IS NOT NULL)"
    )
    with pytest.raises(DBAPIError), database.engine.begin() as connection:
        connection.execute(
            update(regulation_rules)
            .where(regulation_rules.c.rule_id == "synthetic-upper")
            .values(window_days=99)
        )
    incomplete = _rule(official_source_url=None, active=False).model_dump(mode="json")
    incomplete["id"] = uuid4()
    incomplete["active"] = True
    incomplete["created_at"] = NOW
    with pytest.raises(DBAPIError), database.engine.begin() as connection:
        connection.execute(insert(regulation_rules).values(**incomplete))
    with database.engine.connect() as connection:
        assert (
            connection.scalar(text("SELECT version_num FROM alembic_version"))
            == "0009b_reg_event_provenance"
        )
