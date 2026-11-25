"""Version namespace and immutable metadata; legacy is not silently upgraded."""

import importlib

from test_b7_result_contracts import actual_result
from test_reset_backtest import backtest_schema  # noqa: F401


def test_contract_key_is_server_derived_and_versioned():
    from dragon_radar.research.cache_contract import cache_key, compute_contract

    cfg, _ = actual_result()
    assert cache_key(cfg, "v1", compute_contract("v1")) != cache_key(
        cfg, "v2", compute_contract("v2")
    )
    assert len(compute_contract("v2")) == 64


def test_additive_migration_and_metadata():
    migration = importlib.import_module(
        "dragon_radar.migrations.versions.0018_research_contract"
    )
    from dragon_radar.research.tables import (
        research_backtest_cache_v2,
        research_backtest_runs,
    )

    assert migration.down_revision == "0017_analysis_limits"
    assert {"result_schema_version", "compute_contract_sha256"} <= set(
        research_backtest_runs.c.keys()
    )
    assert {"config_hash", "result_schema_version", "compute_contract_sha256"} <= set(
        research_backtest_cache_v2.c.keys()
    )
    assert {
        "research_backtest_runs_result_schema",
        "research_backtest_runs_compute_contract",
    } <= {c.name for c in research_backtest_runs.constraints}


def test_v2_cache_freezes_first_clock_but_rejects_different_facts():
    import copy

    import pytest
    from test_b7_materialization import materialized

    from dragon_radar.research.repository import validated_cache_result

    cfg, result = materialized()
    candidate = copy.deepcopy(result)
    candidate["lineage"]["computed_at"] = "2026-11-24T01:00:00Z"
    cached = {
        "result": result,
        "input_manifest": {
            "config": cfg.model_dump(mode="json"),
            "history": copy.deepcopy(result["lineage"]["frames"]),
        },
    }
    reused = validated_cache_result(cached, cfg, candidate=candidate)
    assert reused["lineage"]["computed_at"] == "2026-11-24T00:00:00Z"
    candidate["lineage"]["frames"][0]["frame_sha256"] = "f" * 64
    with pytest.raises(ValueError, match="conflicting"):
        validated_cache_result(cached, cfg, candidate=candidate)


def test_v2_cache_lookup_uses_all_three_server_keys():
    from sqlalchemy.dialects import postgresql

    from dragon_radar.research.cache_contract import SCHEMAS, compute_contract
    from dragon_radar.research.repository import ResearchRunRepository
    from dragon_radar.research.tables import research_backtest_cache_v2

    cfg, _ = actual_result()
    statement = ResearchRunRepository._cache_where(
        research_backtest_cache_v2,
        cfg.config_hash,
        SCHEMAS["v2"],
        compute_contract("v2"),
    ).compile(dialect=postgresql.dialect())
    assert set(statement.params.values()) == {
        cfg.config_hash,
        SCHEMAS["v2"],
        compute_contract("v2"),
    }


def test_fresh_and_existing_schema_upgrade(database_url):
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import create_engine, inspect, text

    engine = create_engine(database_url)
    config = Config()
    config.set_main_option("script_location", "dragon_radar:migrations")
    try:
        with engine.begin() as connection:
            config.attributes["connection"] = connection
            command.upgrade(config, "0017_analysis_limits")
            # Simulate an existing installation whose old migration did not
            # import today's research metadata.
            connection.execute(
                text(
                    "ALTER TABLE research_backtest_runs "
                    "DROP COLUMN result_schema_version"
                )
            )
            connection.execute(
                text(
                    "ALTER TABLE research_backtest_runs "
                    "DROP COLUMN compute_contract_sha256"
                )
            )
            command.upgrade(config, "0018_research_contract")
            assert (
                connection.scalar(text("SELECT version_num FROM alembic_version"))
                == "0018_research_contract"
            )
            assert "research_backtest_cache_v2" in inspect(connection).get_table_names()
            triggers = (
                connection.execute(
                    text(
                        "SELECT tgname FROM pg_trigger "
                        "WHERE tgrelid = 'research_backtest_cache_v2'::regclass"
                    )
                )
                .scalars()
                .all()
            )
            assert {
                "research_backtest_cache_v2_immutable",
                "research_backtest_cache_v2_no_truncate",
            } <= set(triggers)
    finally:
        engine.dispose()


def test_fresh_head_preserves_live_metadata(database):
    from sqlalchemy import inspect, text

    with database.engine.connect() as connection:
        assert (
            connection.scalar(text("SELECT version_num FROM alembic_version"))
            == "0018_research_contract"
        )
        assert {"result_schema_version", "compute_contract_sha256"} <= {
            c["name"] for c in inspect(connection).get_columns("research_backtest_runs")
        }


def test_database_contract_metadata_and_cache_are_immutable(
    backtest_schema,  # noqa: F811 - pytest injects the imported fixture
):
    from datetime import UTC, datetime
    from uuid import uuid4

    import pytest
    from sqlalchemy import insert, text
    from sqlalchemy.exc import DBAPIError
    from test_b7_materialization import materialized
    from test_reset_backtest import _insert_run

    from dragon_radar.research.cache_contract import SCHEMAS, compute_contract
    from dragon_radar.research.tables import research_backtest_cache_v2

    database, owner, publication = backtest_schema
    cfg, result = materialized()
    with database.engine.begin() as connection:
        run = _insert_run(connection, owner, publication)
        connection.execute(
            insert(research_backtest_cache_v2).values(
                id=uuid4(),
                config_hash=cfg.config_hash,
                result_schema_version=SCHEMAS["v2"],
                compute_contract_sha256=compute_contract("v2"),
                input_manifest={"config": cfg.model_dump(mode="json")},
                result=result,
                created_at=datetime(2026, 11, 24, tzinfo=UTC),
            )
        )
    for statement in (
        "UPDATE research_backtest_runs SET result_schema_version = "
        "'dragon-radar.research-backtest-result/v2' WHERE id = :id",
        "UPDATE research_backtest_runs SET compute_contract_sha256 = :digest "
        "WHERE id = :id",
        "UPDATE research_backtest_cache_v2 SET result = '{}'::jsonb",
        "DELETE FROM research_backtest_cache_v2",
        "TRUNCATE research_backtest_cache_v2",
    ):
        with pytest.raises(DBAPIError):
            with database.engine.begin() as connection:
                connection.execute(
                    text(statement), {"id": run["run_id"], "digest": "a" * 64}
                )
    with database.engine.connect() as connection:
        assert (
            connection.scalar(
                text(
                    "SELECT result->'lineage'->>'computed_at' "
                    "FROM research_backtest_cache_v2"
                )
            )
            == result["lineage"]["computed_at"]
        )
