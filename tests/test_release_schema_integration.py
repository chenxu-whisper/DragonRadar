"""Explicit additive release catalog contracts, independent of runtime metadata."""

import pytest
from sqlalchemy import text

from dragon_radar.storage.schema_contract import (
    capture_schema_contract,
    compare_schema_contract,
    load_packaged_schema_contract,
)


def test_packaged_daily_ai_catalog_has_artifact_erasure_and_immutable_licensing():
    contract = load_packaged_schema_contract()
    tables = {table["name"] for table in contract["tables"]}
    assert {
        "ai_output_entitlements",
        "ai_output_entitlement_revocations",
        "public_daily_ai_archives",
        "promoted_news_summaries",
        "ai_artifact_private_links",
        "ai_artifact_verifications",
        "ai_artifact_revocations",
    } <= tables
    triggers = {
        (trigger["table"], trigger["name"]): trigger for trigger in contract["triggers"]
    }
    for table in ("public_daily_ai_archives", "promoted_news_summaries"):
        assert triggers[table, table + "_erasure"]["function_name"] == (
            "guard_ai_artifact_erasure"
        )
        assert triggers[table, table + "_no_truncate"]["events"] == ["TRUNCATE"]
    assert triggers["ai_output_entitlements", "ai_output_entitlements_immutable"][
        "events"
    ] == ["UPDATE", "DELETE"]


def test_packaged_clock_catalog_retains_nullable_legacy_lineage():
    contract = load_packaged_schema_contract()
    tables = {table["name"]: table for table in contract["tables"]}
    column = next(
        column
        for column in tables["publication_results"]["columns"]
        if column["name"] == "clock_lineage"
    )
    assert column["type"] == "jsonb"
    assert column["nullable"] is True
    assert "input_clock_evidence" in tables
    assert {
        trigger["name"]
        for trigger in contract["triggers"]
        if trigger["table"] == "input_clock_evidence"
    } == {
        "input_clock_evidence_no_update",
        "input_clock_evidence_no_delete",
        "input_clock_evidence_no_truncate",
    }
    assert any(
        constraint["name"] == "input_clock_evidence_content"
        and constraint["kind"] == "unique"
        for constraint in contract["constraints"]
    )


def test_packaged_analysis_budget_catalog_is_bounded_and_immutable():
    contract = load_packaged_schema_contract()
    table = next(
        table for table in contract["tables"] if table["name"] == "analysis_jobs"
    )
    column = next(
        column for column in table["columns"] if column["name"] == "max_wall_clock_ms"
    )
    assert column["type"] == "integer"
    assert column["nullable"] is False
    check = next(
        constraint
        for constraint in contract["constraints"]
        if constraint["name"] == "analysis_jobs_wall_clock_budget"
    )
    assert "300000" in check["expression"]
    assert "max_wall_clock_ms" in check["expression"]


def test_packaged_research_catalog_has_versioned_cache_and_legacy_result_columns():
    contract = load_packaged_schema_contract()
    tables = {table["name"]: table for table in contract["tables"]}
    assert {"research_backtest_cache", "research_backtest_cache_v2"} <= tables.keys()
    columns = {
        column["name"]: column for column in tables["research_backtest_runs"]["columns"]
    }
    assert columns["result_schema_version"]["nullable"] is False
    assert columns["compute_contract_sha256"]["nullable"] is False
    cache_unique = next(
        constraint
        for constraint in contract["constraints"]
        if constraint["table"] == "research_backtest_cache_v2"
        and constraint["kind"] == "unique"
    )
    assert cache_unique["columns"] == [
        "config_hash",
        "result_schema_version",
        "compute_contract_sha256",
    ]
    assert {
        trigger["name"]
        for trigger in contract["triggers"]
        if trigger["table"] == "research_backtest_cache_v2"
    } == {
        "research_backtest_cache_v2_immutable",
        "research_backtest_cache_v2_no_truncate",
    }


def test_actual_additive_catalog_matches_packaged_capture(database):
    with database.engine.connect() as connection:
        actual = capture_schema_contract(connection)
    assert compare_schema_contract(load_packaged_schema_contract(), actual) == []
    assert database.ready()


def test_capture_preserves_old_new_trigger_when_condition(database):
    with database.engine.begin() as connection:
        connection.execute(
            text(
                "CREATE TRIGGER release_old_new_probe BEFORE UPDATE ON source_grants "
                "FOR EACH ROW WHEN (OLD.source_id IS DISTINCT FROM NEW.source_id) "
                "EXECUTE FUNCTION reject_immutable_mutation()"
            )
        )
        actual = capture_schema_contract(connection)
    trigger = next(
        trigger
        for trigger in actual["triggers"]
        if trigger["name"] == "release_old_new_probe"
    )
    assert "old.source_id" in trigger["when"].lower()
    assert "new.source_id" in trigger["when"].lower()
    assert "IS DISTINCT FROM" in trigger["when"]


@pytest.mark.parametrize(
    "ddl",
    [
        "DROP TRIGGER public_daily_ai_archives_erasure ON public_daily_ai_archives",
        "ALTER TABLE publication_results DROP COLUMN clock_lineage",
        "DROP TRIGGER input_clock_evidence_no_update ON input_clock_evidence",
        "ALTER TABLE analysis_jobs DROP CONSTRAINT analysis_jobs_wall_clock_budget",
        (
            "ALTER TABLE research_backtest_runs DROP COLUMN "
            "compute_contract_sha256 CASCADE"
        ),
        (
            "DROP TRIGGER research_backtest_cache_v2_no_truncate "
            "ON research_backtest_cache_v2"
        ),
    ],
)
def test_readiness_rejects_additive_schema_drift(database, ddl):
    assert database.ready()
    with database.engine.begin() as connection:
        connection.execute(text(ddl))
    assert not database.ready()
