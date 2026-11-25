"""Additive named-column compatibility retains all structural drift checks."""

from copy import deepcopy

import pytest
from sqlalchemy import text

from dragon_radar.storage.schema_contract import (
    capture_schema_contract,
    compare_schema_contract,
    load_packaged_schema_contract,
)


def reordered_contract():
    expected = load_packaged_schema_contract()
    actual = deepcopy(expected)
    for table in actual["tables"]:
        if table["name"] in {"analysis_jobs", "research_backtest_runs"}:
            table["columns"] = table["columns"][1:] + table["columns"][:1]
    return expected, actual


def test_appended_physical_column_position_is_compatible():
    expected, actual = reordered_contract()
    assert compare_schema_contract(expected, actual) == []


@pytest.mark.parametrize(
    "field,value",
    [
        ("name", "renamed_column"),
        ("type", "text"),
        ("nullable", True),
        ("identity", "always"),
        ("generated", "stored"),
    ],
)
def test_column_position_normalization_retains_every_column_property(field, value):
    expected, actual = reordered_contract()
    table = next(
        table for table in actual["tables"] if table["name"] == "analysis_jobs"
    )
    column = next(column for column in table["columns"] if column["name"] == "id")
    column[field] = value
    assert any(
        "tables mismatch" in error
        for error in compare_schema_contract(expected, actual)
    )


def test_column_position_normalization_retains_foreign_key_position():
    expected, actual = reordered_contract()
    constraint = next(
        constraint
        for constraint in actual["constraints"]
        if constraint["kind"] == "foreign_key" and len(constraint["columns"]) > 1
    )
    constraint["columns"].reverse()
    assert any(
        "constraints mismatch" in error
        for error in compare_schema_contract(expected, actual)
    )


def test_actual_add_column_matches_same_named_fresh_layout(database_url):
    from sqlalchemy import create_engine

    engine = create_engine(database_url)
    try:
        with engine.begin() as connection:
            connection.execute(text("CREATE TABLE position_probe (id bigint NOT NULL)"))
            connection.execute(
                text("ALTER TABLE position_probe ADD COLUMN budget integer")
            )
            appended = capture_schema_contract(connection)
            connection.execute(text("DROP TABLE position_probe"))
            connection.execute(
                text("CREATE TABLE position_probe (budget integer, id bigint NOT NULL)")
            )
            fresh = capture_schema_contract(connection)
        assert compare_schema_contract(fresh, appended) == []
    finally:
        engine.dispose()
