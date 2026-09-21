"""Synthetic contracts for licensed local market ingestion and read models."""

import hashlib
import json
import os
import subprocess
import sys
from datetime import UTC, datetime, time
from decimal import Decimal
from uuid import UUID

from factories import NOW, bar, batch, state
from sqlalchemy import inspect, select, text


def issue_codes(payload):
    from dragon_radar.domain.quality import validate_batch

    return {issue.code for issue in validate_batch(payload, now=NOW).issues}


def test_stable_security_id_is_distinct_from_current_display_code():
    original = state(security_id="stable-security-1", code="000001")
    renamed = state(
        security_id="stable-security-1",
        code="001001",
        effective_from=original.effective_from,
    )

    assert original.security_id == renamed.security_id
    assert original.code != renamed.code


def test_stock_and_index_identity_are_explicit():
    stock = state(security_type="common_stock", code="000001")
    index = state(
        security_id="index-1",
        security_type="index",
        code="000300",
        name="Synthetic Index",
    )

    assert stock.security_type == "common_stock"
    assert index.security_type == "index"


def test_optional_market_and_seal_fields_remain_none():
    from dragon_radar.domain.models import LimitUpEvent

    daily = bar(
        turnover_rate=None,
        float_market_cap=None,
        total_market_cap=None,
    )
    event = LimitUpEvent(
        security_id="A",
        trade_date=daily.trade_date,
        available_at=daily.available_at,
        source_id=daily.source_id,
        source_version=daily.source_version,
        is_limit_up=True,
        touched_limit=None,
        first_seal_time=None,
        last_seal_time=None,
        break_count=None,
        seal_amount=None,
        continuous_boards=1,
    )

    assert daily.turnover_rate is None
    assert daily.float_market_cap is None
    assert daily.total_market_cap is None
    assert event.first_seal_time is None
    assert event.last_seal_time is None


def test_prd_market_size_names_are_exact_decimal_fields():
    daily = bar(float_mv="123.45", total_mv=456)

    assert daily.float_mv == Decimal("123.45")
    assert daily.total_mv == Decimal("456")
    assert daily.float_market_cap == daily.float_mv
    assert daily.total_market_cap == daily.total_mv


def test_negative_market_values_are_quarantined():
    payload = batch(
        bars=(
            bar(
                turnover_rate=Decimal("-0.01"),
                float_market_cap=Decimal("-1"),
                total_market_cap=Decimal("-2"),
            ),
        )
    )

    assert {
        "negative_turnover_rate",
        "negative_float_market_cap",
        "negative_total_market_cap",
    } <= issue_codes(payload)


def test_wrong_units_are_quarantined_instead_of_rejected_at_parse_time():
    payload = batch(
        bars=(
            bar(
                price_unit="cent/share",
                volume_unit="contract",
                amount_unit="thousand_currency",
                turnover_rate=Decimal("0.1"),
                turnover_rate_unit="percent",
                float_market_cap=Decimal("100"),
                market_cap_unit="thousand_currency",
            ),
        )
    )

    assert "unit_contract" in issue_codes(payload)


def test_missing_page_continuity_is_quarantined():
    assert "incomplete_pages" in issue_codes(batch(complete_pages=False))


def test_negative_limit_up_counts_and_amount_are_quarantined():
    from dragon_radar.domain.models import LimitUpEvent

    event = LimitUpEvent(
        security_id="A",
        trade_date=bar().trade_date,
        available_at=bar().available_at,
        source_id="synthetic",
        source_version="s1",
        is_limit_up=True,
        touched_limit=True,
        first_seal_time=time(9, 35),
        last_seal_time=time(14, 55),
        break_count=-1,
        seal_amount=Decimal("-1"),
        continuous_boards=-1,
    )
    payload = batch(limit_up_events=(event,))

    assert {
        "negative_break_count",
        "negative_seal_amount",
        "negative_continuous_boards",
    } <= issue_codes(payload)


def test_wrong_limit_up_amount_unit_is_quarantined():
    from dragon_radar.domain.models import LimitUpEvent

    event = LimitUpEvent(
        security_id="A",
        trade_date=bar().trade_date,
        available_at=bar().available_at,
        source_id="synthetic",
        source_version="s1",
        is_limit_up=True,
        seal_amount="100",
        seal_amount_unit="thousand_currency",
    )

    assert "unit_contract" in issue_codes(batch(limit_up_events=(event,)))


def licensed_batch(payload=None, *, contiguous=True):
    from dragon_radar.ingestion.contracts import (
        LicensedMarketBatch,
        MarketManifestPage,
        MarketPageManifest,
    )

    payload = payload or batch(
        security_states=(state(code="000001"),),
        bars=(
            bar(
                previous_close="9",
                turnover_rate=None,
                float_market_cap=None,
                total_market_cap=None,
            ),
        ),
    )
    pages = (
        MarketManifestPage(
            page_id="page-1",
            request_cursor=None,
            next_cursor="cursor-2",
            page_hash=hashlib.sha256(b"page-1").hexdigest(),
            received_row_count=1,
        ),
        MarketManifestPage(
            page_id="page-2",
            request_cursor="cursor-2" if contiguous else "cursor-gap",
            next_cursor=None,
            page_hash=hashlib.sha256(b"page-2").hexdigest(),
            received_row_count=1,
        ),
    )
    return LicensedMarketBatch(
        batch=payload,
        page_manifest=MarketPageManifest(
            request_from=payload.trade_date,
            request_to=payload.trade_date,
            pages=pages,
            declared_total=2,
            terminal_cursor=None,
        ),
    )


def test_local_json_adapter_accepts_only_explicit_licensed_envelope(tmp_path):
    from dragon_radar.ingestion.local_json import load_licensed_market_json

    payload = licensed_batch()
    path = tmp_path / "licensed-market.json"
    path.write_text(payload.model_dump_json(), encoding="utf-8")
    assert load_licensed_market_json(path) == payload

    legacy_path = tmp_path / "legacy-market.json"
    legacy_path.write_text(batch().model_dump_json(), encoding="utf-8")
    try:
        load_licensed_market_json(legacy_path)
    except ValueError as exc:
        assert str(exc) == "Invalid licensed market JSON"
    else:
        raise AssertionError("legacy JSON must not silently become licensed ingestion")


def test_runner_materializes_stock_index_and_lineage(database, repository):
    from dragon_radar.ingestion.runner import MarketIngestionRunner
    from dragon_radar.readmodels.tables import (
        daily_kline,
        index_daily_kline,
        security,
    )

    index_state = state(
        security_id="stable-index",
        code="000300",
        name="Synthetic Index",
        security_type="index",
    )
    stock_state = state(security_id="stable-stock", code="000001")
    payload = batch(
        expected_security_ids=("stable-stock", "stable-index"),
        security_states=(stock_state, index_state),
        bars=(
            bar(security_id="stable-stock", previous_close="9"),
            bar(security_id="stable-index", previous_close="9"),
        ),
    )
    result = MarketIngestionRunner(repository).import_batch(
        licensed_batch(payload), key="market-runner"
    )

    assert result.status == "succeeded"
    assert isinstance(result.task_id, UUID)
    assert result.batch_id is not None
    with database.engine.connect() as connection:
        rows = connection.execute(
            select(
                security.c.security_id,
                security.c.code,
                security.c.source_archive_id,
            ).order_by(security.c.security_id)
        ).all()
        assert rows == [
            ("stable-index", "000300", result.batch_id),
            ("stable-stock", "000001", result.batch_id),
        ]
        assert connection.scalar(select(text("count(*)")).select_from(daily_kline)) == 1
        assert (
            connection.scalar(select(text("count(*)")).select_from(index_daily_kline))
            == 1
        )


def test_runner_preserves_nullable_market_fields(database, repository):
    from dragon_radar.ingestion.runner import MarketIngestionRunner
    from dragon_radar.readmodels.tables import daily_kline

    result = MarketIngestionRunner(repository).import_batch(
        licensed_batch(), key="nullable-market"
    )
    with database.engine.connect() as connection:
        row = connection.execute(
            select(
                daily_kline.c.turnover_rate,
                daily_kline.c.float_mv,
                daily_kline.c.total_mv,
                daily_kline.c.source_archive_id,
            )
        ).one()
    assert row == (None, None, None, result.batch_id)


def test_page_gap_is_archived_quarantined_and_idempotent(database, repository):
    from dragon_radar.ingestion.runner import MarketIngestionRunner

    runner = MarketIngestionRunner(repository)
    first = runner.import_batch(licensed_batch(contiguous=False), key="market-gap")
    replay = runner.import_batch(licensed_batch(contiguous=False), key="market-gap")

    assert first == replay
    assert first.status == "quarantined"
    assert first.batch_id is not None
    attempt = runner.get_attempt(first.attempt_id)
    assert attempt.failed_cursor == "cursor-gap"
    assert attempt.missing_items == ("expected_cursor:cursor-2",)
    assert attempt.request_from == attempt.retry_from
    assert attempt.request_to == attempt.retry_to
    with database.engine.connect() as connection:
        assert connection.scalar(text("SELECT count(*) FROM import_batches")) == 1
        assert connection.scalar(text("SELECT count(*) FROM market_import_tasks")) == 1
        assert (
            connection.scalar(text("SELECT count(*) FROM market_import_attempts")) == 1
        )
        assert connection.scalar(text("SELECT count(*) FROM daily_kline")) == 0


def test_batch_completeness_failure_records_missing_item(repository):
    from dragon_radar.ingestion.runner import MarketIngestionRunner

    payload = batch(
        complete_pages=False,
        security_states=(state(code="000001"),),
        bars=(bar(previous_close="9"),),
    )
    result = MarketIngestionRunner(repository).import_batch(
        licensed_batch(payload), key="market-incomplete-pages"
    )

    attempt = MarketIngestionRunner(repository).get_attempt(result.attempt_id)
    assert "batch_complete_pages:false" in attempt.missing_items


def test_retry_is_append_only_and_rechecks_original_authorization(
    database, repository
):
    from dragon_radar.ingestion.runner import MarketIngestionRunner
    from dragon_radar.storage.repository import Forbidden

    runner = MarketIngestionRunner(repository)
    failed = runner.import_batch(licensed_batch(contiguous=False), key="market-failed")
    succeeded = runner.retry_attempt(
        failed.attempt_id,
        licensed_batch(),
        key="market-retry",
    )
    assert succeeded.status == "succeeded"
    assert succeeded.task_id == failed.task_id
    assert [attempt.status for attempt in runner.list_attempts()] == [
        "succeeded",
        "quarantined",
    ]

    repository.revoke_source("synthetic")
    try:
        runner.retry_attempt(
            failed.attempt_id,
            licensed_batch(),
            key="market-retry-denied",
        )
    except Forbidden:
        pass
    else:
        raise AssertionError("retry must recheck authorization")


def test_task_and_attempt_reads_recheck_embedded_authorization(repository):
    from factories import grant

    from dragon_radar.ingestion.runner import MarketIngestionRunner

    runner = MarketIngestionRunner(repository)
    runner.import_batch(licensed_batch(), key="market-expiring")
    repository.register_source(
        grant(expires_at=datetime(2030, 1, 1, tzinfo=UTC))
    )
    repository.clock = lambda: datetime(2028, 1, 1, tzinfo=UTC)

    assert runner.list_tasks() == ()
    assert runner.list_attempts() == ()


def test_market_schema_constraints_match_frozen_contract(database):
    inspector = inspect(database.engine)
    security_indexes = {
        item["name"]: tuple(item["column_names"])
        for item in inspector.get_indexes("security")
    }
    history_unique = {
        tuple(item["column_names"])
        for item in inspector.get_unique_constraints("security_state_history")
    }
    security_unique = {
        tuple(item["column_names"])
        for item in inspector.get_unique_constraints("security")
    }

    assert security_indexes["security_exchange_code"] == ("exchange", "code")
    assert ("security_id", "state_type", "valid_from") in history_unique
    assert ("security_id", "valid_from") not in security_unique
    assert "security_state_history_no_overlap" in {
        row["conname"]
        for row in database.engine.connect()
        .execute(
            text(
                "SELECT conname FROM pg_constraint "
                "WHERE conrelid = 'security_state_history'::regclass"
            )
        )
        .mappings()
    }


def test_local_display_does_not_grant_export_ai_or_sharing():
    rights = batch().grant
    assert rights.local_display
    assert not rights.export
    assert not rights.ai
    assert not rights.sharing


def test_licensed_envelope_round_trip_keeps_decimals_as_strings():
    payload = licensed_batch(
        batch(
            security_states=(state(code="000001"),),
            bars=(bar(previous_close="9", float_market_cap="123.45"),),
        )
    )
    encoded = payload.model_dump_json()
    decoded = json.loads(encoded)
    assert decoded["batch"]["bars"][0]["float_mv"] == "123.45"


def run_cli(args, database_url):
    env = dict(os.environ)
    env["DRAGON_DATABASE_URL"] = database_url
    return subprocess.run(
        [sys.executable, "-m", "dragon_radar", *args],
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )


def test_market_cli_exposes_minimal_task_and_attempt_workflow(
    database, repository, tmp_path
):
    database_url = database.engine.url.render_as_string(hide_password=False)
    path = tmp_path / "licensed-market.json"
    path.write_text(licensed_batch().model_dump_json(), encoding="utf-8")

    imported = run_cli(
        ["market-import", str(path), "--key", "market-cli"], database_url
    )
    assert imported.returncode == 0, imported.stderr
    body = json.loads(imported.stdout)
    assert set(body) == {"attempt_id", "batch_id", "status", "task_id"}
    assert body["status"] == "succeeded"

    tasks = json.loads(run_cli(["market-tasks"], database_url).stdout)["tasks"]
    assert len(tasks) == 1
    assert set(tasks[0]) == {
        "request_from",
        "request_to",
        "source_id",
        "status",
        "task_id",
    }
    shown_task = run_cli(["market-task-show", body["task_id"]], database_url)
    assert shown_task.returncode == 0, shown_task.stderr
    assert json.loads(shown_task.stdout) == tasks[0]

    attempts = json.loads(
        run_cli(["market-attempts", "--status", "succeeded"], database_url).stdout
    )["attempts"]
    assert len(attempts) == 1
    shown_attempt = run_cli(
        ["market-attempt-show", body["attempt_id"]], database_url
    )
    assert shown_attempt.returncode == 0, shown_attempt.stderr
    assert json.loads(shown_attempt.stdout) == attempts[0]
    combined = (
        imported.stdout
        + imported.stderr
        + shown_task.stdout
        + shown_task.stderr
        + shown_attempt.stdout
        + shown_attempt.stderr
    )
    for sensitive in (
        "synthetic-test-only",
        "canonical_json",
        "payload_hash",
        "quality_report",
        "embedded_grant",
        "page_manifest",
        "details",
    ):
        assert sensitive not in combined
