"""Synthetic contracts for licensed local market ingestion and read models."""

import hashlib
import json
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, time, timedelta
from decimal import Decimal
from threading import Barrier
from uuid import UUID

import pytest
from factories import DAYS, NOW, bar, batch, state
from sqlalchemy import event, inspect, select, text


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


def test_older_backfill_does_not_replace_newer_current_security(
    database, repository
):
    from dragon_radar.ingestion.runner import MarketIngestionRunner
    from dragon_radar.readmodels.tables import security

    runner = MarketIngestionRunner(repository)
    newer = batch(
        trade_date=DAYS[-1],
        source_version="s2",
        pool_version="p2",
        security_states=(
            state(
                code="NEW",
                source_version="s2",
                pool_version="p2",
                effective_from=DAYS[2],
                available_at=NOW - timedelta(days=1),
            ),
        ),
        bars=(
            bar(
                trade_date=DAYS[-1],
                source_version="s2",
                available_at=NOW - timedelta(hours=1),
            ),
        ),
    )
    older = batch(
        trade_date=DAYS[1],
        security_states=(state(code="OLD", effective_to=DAYS[2]),),
        bars=(bar(trade_date=DAYS[1]),),
    )

    current = runner.import_batch(licensed_batch(newer), key="current-newer")
    runner.import_batch(licensed_batch(older), key="backfill-older")

    with database.engine.connect() as connection:
        row = connection.execute(
            select(
                security.c.code,
                security.c.source_version,
                security.c.source_archive_id,
            ).where(security.c.security_id == "A")
        ).one()
    assert row == ("NEW", "s2", current.batch_id)


def test_same_date_older_revision_does_not_replace_later_available_identity(
    database, repository
):
    from dragon_radar.ingestion.runner import MarketIngestionRunner
    from dragon_radar.readmodels.tables import security

    runner = MarketIngestionRunner(repository)

    def revision(version, code, available_at):
        return batch(
            source_version=version,
            pool_version=version,
            security_states=(
                state(
                    code=code,
                    source_version=version,
                    pool_version=version,
                    available_at=available_at,
                ),
            ),
            bars=(bar(source_version=version),),
        )

    later = runner.import_batch(
        licensed_batch(revision("s2", "LATER", NOW - timedelta(hours=2))),
        key="same-date-later",
    )
    runner.import_batch(
        licensed_batch(revision("s1", "EARLIER", NOW - timedelta(days=1))),
        key="same-date-earlier",
    )

    with database.engine.connect() as connection:
        row = connection.execute(
            select(
                security.c.code,
                security.c.available_at,
                security.c.source_archive_id,
            ).where(security.c.security_id == "A")
        ).one()
    assert row == ("LATER", NOW - timedelta(hours=2), later.batch_id)


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


def test_limit_up_source_revisions_are_preserved_with_archive_lineage(
    database, repository
):
    from dragon_radar.domain.models import LimitUpEvent
    from dragon_radar.ingestion.runner import MarketIngestionRunner
    from dragon_radar.readmodels.tables import limit_up_record

    runner = MarketIngestionRunner(repository)

    def revision(version, continuous_boards):
        available_at = NOW - timedelta(hours=continuous_boards)
        return batch(
            source_version=version,
            pool_version=version,
            security_states=(
                state(
                    code="000001",
                    source_version=version,
                    pool_version=version,
                    available_at=available_at,
                ),
            ),
            bars=(bar(source_version=version),),
            limit_up_events=(
                LimitUpEvent(
                    security_id="A",
                    trade_date=DAYS[-1],
                    available_at=available_at,
                    source_id="synthetic",
                    source_version=version,
                    is_limit_up=True,
                    continuous_boards=continuous_boards,
                ),
            ),
        )

    first = runner.import_batch(
        licensed_batch(revision("s1", 1)), key="limit-up-v1"
    )
    second = runner.import_batch(
        licensed_batch(revision("s2", 2)), key="limit-up-v2"
    )

    with database.engine.connect() as connection:
        rows = connection.execute(
            select(
                limit_up_record.c.source_version,
                limit_up_record.c.continuous_boards,
                limit_up_record.c.source_archive_id,
            ).order_by(limit_up_record.c.source_version)
        ).all()
    assert rows == [
        ("s1", 1, first.batch_id),
        ("s2", 2, second.batch_id),
    ]


@pytest.mark.parametrize(
    ("record", "field", "maximum"),
    [
        ("state", "security_id", 32),
        ("state", "code", 10),
        ("state", "name", 50),
        ("state", "security_type", 30),
        ("state", "exchange", 10),
        ("state", "board", 20),
        ("batch", "source_id", 50),
        ("batch", "source_version", 100),
        ("bar", "currency", 10),
        ("bar", "volume_unit", 20),
    ],
)
def test_overlength_normalized_identity_is_archived_and_quarantined(
    database,
    repository,
    record,
    field,
    maximum,
):
    from factories import grant

    from dragon_radar.ingestion.runner import MarketIngestionRunner

    value = "x" * (maximum + 1)
    state_changes = {}
    bar_changes = {}
    if record == "state":
        state_changes[field] = value
    elif record == "bar":
        bar_changes[field] = value

    security_id = value if field == "security_id" else "A"
    source_id = value if field == "source_id" else "synthetic"
    source_version = value if field == "source_version" else "s1"
    exchange = value if field == "exchange" else "TEST"
    if source_id != "synthetic":
        repository.register_source(grant(source_id=source_id))
    calendar = batch().calendar.model_copy(update={"exchange": exchange})
    state_values = {
        "security_id": security_id,
        "code": "000001",
        "source_id": source_id,
        "source_version": source_version,
        "exchange": exchange,
        **state_changes,
    }
    bar_values = {
        "security_id": security_id,
        "source_id": source_id,
        "source_version": source_version,
        **bar_changes,
    }
    payload = batch(
        source_id=source_id,
        source_version=source_version,
        grant=grant(source_id=source_id),
        calendar=calendar,
        expected_security_ids=(security_id,),
        security_states=(state(**state_values),),
        bars=(bar(**bar_values),),
    )

    result = MarketIngestionRunner(repository).import_batch(
        licensed_batch(payload),
        key=f"overlength-{record}-{field}",
    )

    assert result.status == "quarantined"
    with database.engine.connect() as connection:
        assert connection.scalar(text("SELECT count(*) FROM import_batches")) == 1
        quality_report = connection.execute(
            text("SELECT quality_report FROM import_batches")
        ).scalar_one()
        assert "normalized_bounds" in {
            issue["code"] for issue in quality_report["issues"]
        }
        for table in (
            "security",
            "security_state_history",
            "daily_kline",
            "index_daily_kline",
            "limit_up_record",
        ):
            assert connection.scalar(text(f"SELECT count(*) FROM {table}")) == 0


def test_unsupported_security_type_is_quarantined_without_materialization(
    database, repository
):
    from dragon_radar.ingestion.runner import MarketIngestionRunner

    result = MarketIngestionRunner(repository).import_batch(
        licensed_batch(
            batch(
                security_states=(state(code="000001", security_type="fund"),),
                bars=(bar(),),
            )
        ),
        key="unsupported-security-type",
    )

    assert result.status == "quarantined"
    with database.engine.connect() as connection:
        assert connection.scalar(text("SELECT count(*) FROM import_batches")) == 1
        assert connection.scalar(text("SELECT count(*) FROM security")) == 0
        assert connection.scalar(text("SELECT count(*) FROM daily_kline")) == 0


def test_historical_non_current_security_gets_stable_base_row(database, repository):
    from dragon_radar.ingestion.runner import MarketIngestionRunner
    from dragon_radar.readmodels.tables import security, security_state_history

    historical = state(
        security_id="historical-only",
        code="000002",
        name="Historical Only",
        effective_to=DAYS[1],
    )
    payload = batch(
        security_states=(state(code="000001"), historical),
    )

    result = MarketIngestionRunner(repository).import_batch(
        licensed_batch(payload),
        key="historical-security",
    )

    assert result.status == "succeeded"
    with database.engine.connect() as connection:
        base = connection.execute(
            select(
                security.c.code,
                security.c.name,
                security.c.security_type,
                security.c.list_date,
            ).where(security.c.security_id == "historical-only")
        ).one()
        history_count = connection.scalar(
            select(text("count(*)"))
            .select_from(security_state_history)
            .where(security_state_history.c.security_id == "historical-only")
        )
    assert base == (
        historical.code,
        historical.name,
        historical.security_type,
        historical.listing_date,
    )
    assert history_count > 0


@pytest.mark.parametrize(
    "missing_field",
    ["code", "security_type", "listing_date", "suspended", "is_st"],
)
def test_missing_historical_identity_is_archived_and_quarantined(
    database,
    repository,
    missing_field,
):
    from dragon_radar.ingestion.runner import MarketIngestionRunner
    from dragon_radar.storage.repository import QualityRejected

    identity = {
        "code": "000002",
        missing_field: None,
    }
    historical = state(
        security_id="historical-incomplete",
        effective_to=DAYS[1],
        **identity,
    )
    result = MarketIngestionRunner(repository).import_batch(
        licensed_batch(
            batch(security_states=(state(code="000001"), historical))
        ),
        key=f"historical-missing-{missing_field}",
    )

    assert result.status == "quarantined"
    with database.engine.connect() as connection:
        assert connection.scalar(text("SELECT count(*) FROM import_batches")) == 1
        assert connection.scalar(text("SELECT count(*) FROM security")) == 0
        assert (
            connection.scalar(text("SELECT count(*) FROM security_state_history")) == 0
        )
    with pytest.raises(QualityRejected):
        repository.publish(result.batch_id)


def test_page_gap_is_archived_quarantined_and_idempotent(database, repository):
    from dragon_radar.ingestion.runner import MarketIngestionRunner
    from dragon_radar.storage.repository import QualityRejected

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
    with pytest.raises(QualityRejected):
        repository.publish(first.batch_id)


def test_ingestion_key_binds_complete_envelope(repository):
    from dragon_radar.ingestion.runner import MarketIngestionRunner
    from dragon_radar.storage.repository import Conflict

    runner = MarketIngestionRunner(repository)
    envelope = licensed_batch()
    first = runner.import_batch(envelope, key="market-envelope")
    assert runner.import_batch(envelope, key="market-envelope") == first

    changed_page = envelope.page_manifest.pages[0].model_copy(
        update={"page_hash": hashlib.sha256(b"changed-page").hexdigest()}
    )
    changed = envelope.model_copy(
        update={
            "page_manifest": envelope.page_manifest.model_copy(
                update={
                    "pages": (changed_page, *envelope.page_manifest.pages[1:])
                }
            )
        }
    )
    with pytest.raises(Conflict):
        runner.import_batch(changed, key="market-envelope")


def test_ingestion_replay_rechecks_current_storage_authorization(repository):
    from factories import grant

    from dragon_radar.ingestion.runner import MarketIngestionRunner
    from dragon_radar.storage.repository import Forbidden

    runner = MarketIngestionRunner(repository)
    envelope = licensed_batch()
    runner.import_batch(envelope, key="market-replay-authorization")
    repository.register_source(grant(local_storage=False))

    with pytest.raises(Forbidden):
        runner.import_batch(envelope, key="market-replay-authorization")


def test_concurrent_same_envelope_import_has_one_workflow(database, repository):
    from dragon_radar.ingestion.runner import MarketIngestionRunner

    barrier = Barrier(2)

    def run(_index):
        barrier.wait(timeout=10)
        return MarketIngestionRunner(repository).import_batch(
            licensed_batch(),
            key="market-envelope-race",
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(run, range(2)))

    assert results == [results[0], results[0]]
    with database.engine.connect() as connection:
        assert connection.scalar(text("SELECT count(*) FROM import_batches")) == 1
        assert connection.scalar(text("SELECT count(*) FROM market_import_tasks")) == 1
        assert (
            connection.scalar(text("SELECT count(*) FROM market_import_attempts")) == 1
        )


def test_runner_uses_repository_import_lock_order(database, repository):
    from dragon_radar.ingestion.runner import MarketIngestionRunner

    locks = []

    def record_lock(conn, cursor, statement, parameters, context, executemany):
        if "pg_advisory_xact_lock" in statement:
            locks.append(parameters["key"])

    event.listen(database.engine, "after_cursor_execute", record_lock)
    try:
        MarketIngestionRunner(repository).import_batch(
            licensed_batch(),
            key="market-lock-order",
        )
    finally:
        event.remove(database.engine, "after_cursor_execute", record_lock)

    assert locks[:3] == [
        "market-ingestion-key:market-lock-order",
        "import-key:market-lock-order",
        "source:synthetic",
    ]


def test_attempt_insert_failure_rolls_back_archive_and_workflow(
    database, repository
):
    from dragon_radar.ingestion.runner import MarketIngestionRunner

    def fail_attempt(conn, cursor, statement, parameters, context, executemany):
        if statement.startswith("INSERT INTO market_import_attempts"):
            raise RuntimeError("injected attempt failure")

    event.listen(database.engine, "before_cursor_execute", fail_attempt)
    try:
        with pytest.raises(RuntimeError, match="injected attempt failure"):
            MarketIngestionRunner(repository).import_batch(
                licensed_batch(),
                key="market-attempt-failure",
            )
    finally:
        event.remove(database.engine, "before_cursor_execute", fail_attempt)

    with database.engine.connect() as connection:
        for table in (
            "import_batches",
            "market_import_tasks",
            "market_import_attempts",
            "security",
            "security_state_history",
            "daily_kline",
        ):
            assert connection.scalar(text(f"SELECT count(*) FROM {table}")) == 0
        assert (
            connection.scalar(
                text("SELECT count(*) FROM audit_events WHERE action = 'import'")
            )
            == 0
        )


def test_materialization_failure_rolls_back_archive_and_workflow(
    database, repository, monkeypatch
):
    from dragon_radar.ingestion.runner import MarketIngestionRunner

    runner = MarketIngestionRunner(repository)

    def fail_materialization(*_args):
        raise RuntimeError("injected materialization failure")

    monkeypatch.setattr(runner, "_materialize", fail_materialization)
    with pytest.raises(RuntimeError, match="injected materialization failure"):
        runner.import_batch(
            licensed_batch(),
            key="market-materialization-failure",
        )

    with database.engine.connect() as connection:
        for table in (
            "import_batches",
            "market_import_tasks",
            "market_import_attempts",
            "security",
            "security_state_history",
            "daily_kline",
        ):
            assert connection.scalar(text(f"SELECT count(*) FROM {table}")) == 0


def test_retry_materialization_failure_keeps_only_original_quarantine(
    database, repository, monkeypatch
):
    from dragon_radar.ingestion.runner import MarketIngestionRunner

    runner = MarketIngestionRunner(repository)
    original = runner.import_batch(
        licensed_batch(contiguous=False),
        key="market-retry-original",
    )

    def fail_materialization(*_args):
        raise RuntimeError("injected retry materialization failure")

    monkeypatch.setattr(runner, "_materialize", fail_materialization)
    with pytest.raises(RuntimeError, match="injected retry materialization failure"):
        runner.retry_attempt(
            original.attempt_id,
            licensed_batch(),
            key="market-retry-failure",
        )

    with database.engine.connect() as connection:
        assert connection.scalar(text("SELECT count(*) FROM import_batches")) == 1
        assert connection.scalar(text("SELECT count(*) FROM market_import_tasks")) == 1
        assert (
            connection.scalar(text("SELECT count(*) FROM market_import_attempts")) == 1
        )
        assert connection.scalar(text("SELECT count(*) FROM security")) == 0
        assert connection.scalar(text("SELECT count(*) FROM daily_kline")) == 0


def test_storage_only_license_archives_without_materializing(database, repository):
    from factories import grant

    from dragon_radar.ingestion.runner import MarketIngestionRunner

    storage_only = grant(local_display=False)
    repository.register_source(storage_only)
    payload = batch(
        grant=storage_only,
        security_states=(state(code="000001"),),
        bars=(bar(previous_close="9"),),
    )

    result = MarketIngestionRunner(repository).import_batch(
        licensed_batch(payload),
        key="market-storage-only",
    )

    assert result.status == "quarantined"
    with database.engine.connect() as connection:
        assert connection.scalar(text("SELECT count(*) FROM import_batches")) == 1
        assert connection.scalar(text("SELECT count(*) FROM market_import_tasks")) == 1
        assert (
            connection.scalar(text("SELECT count(*) FROM market_import_attempts")) == 1
        )
        assert connection.scalar(text("SELECT count(*) FROM security")) == 0


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

    with pytest.raises(ValueError, match="not retryable"):
        runner.retry_attempt(
            failed.attempt_id,
            licensed_batch(),
            key="market-retry-after-success",
        )

    authorization_failure = runner.import_batch(
        licensed_batch(contiguous=False),
        key="market-failed-before-revocation",
    )
    repository.revoke_source("synthetic")
    with pytest.raises(Forbidden):
        runner.retry_attempt(
            authorization_failure.attempt_id,
            licensed_batch(),
            key="market-retry-denied",
        )

    with database.engine.connect() as connection:
        assert (
            connection.scalar(
                text(
                    "SELECT count(*) FROM market_import_attempts "
                    "WHERE task_id = :task_id"
                ),
                {"task_id": failed.task_id},
            )
            == 2
        )


def test_retry_key_cannot_replay_result_from_another_task(repository):
    from dragon_radar.ingestion.runner import MarketIngestionRunner
    from dragon_radar.storage.repository import Conflict

    runner = MarketIngestionRunner(repository)
    first = runner.import_batch(
        licensed_batch(contiguous=False),
        key="market-first-task",
    )
    second = runner.import_batch(
        licensed_batch(contiguous=False),
        key="market-second-task",
    )
    runner.retry_attempt(
        first.attempt_id,
        licensed_batch(),
        key="market-shared-retry-key",
    )

    with pytest.raises(Conflict):
        runner.retry_attempt(
            second.attempt_id,
            licensed_batch(),
            key="market-shared-retry-key",
        )


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
    attempt_columns = {
        column["name"] for column in inspector.get_columns("market_import_attempts")
    }
    archive_columns = {
        column["name"] for column in inspector.get_columns("import_batches")
    }
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
    limit_up_unique = {
        tuple(item["column_names"])
        for item in inspector.get_unique_constraints("limit_up_record")
    }

    assert security_indexes["security_exchange_code"] == ("exchange", "code")
    assert "envelope_hash" in attempt_columns
    assert "payload_hash" not in attempt_columns
    assert "payload_hash" in archive_columns
    assert "idempotency_hash" in archive_columns
    assert ("security_id", "state_type", "valid_from") in history_unique
    assert ("security_id", "valid_from") not in security_unique
    assert (
        "security_id",
        "trade_date",
        "source",
        "source_version",
    ) in limit_up_unique
    with database.engine.connect() as connection:
        history_constraints = {
            row["conname"]
            for row in connection.execute(
            text(
                "SELECT conname FROM pg_constraint "
                "WHERE conrelid = 'security_state_history'::regclass"
            )
            ).mappings()
        }
    assert "security_state_history_no_overlap" in history_constraints


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
