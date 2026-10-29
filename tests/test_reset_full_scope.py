"""Task 29 full-scope final acceptance contracts.

This suite does not replace the per-domain suites. It links the 57 PRD
requirements and engineering extensions EX01-EX16 to their actually delivered
evidence, pins the cross-domain snapshot-binding rules, and records the Task 27
scope boundary (B01/B02, EX14/EX15) as an explicit blocked contract instead of
silently skipping it.
"""

from __future__ import annotations

import runpy
from datetime import date, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from factories import batch, state
from httpx import ASGITransport, AsyncClient

ROOT = Path(__file__).parents[1]
CONTRACT_CHECKER = runpy.run_path(str(ROOT / "scripts" / "check_prd_contracts.py"))
EXPECTED_REQS = set(CONTRACT_CHECKER["EXPECTED_REQS"])
FINAL_ACCEPTANCE_DOC = ROOT / "docs" / "acceptance" / "reset-final.md"

# ---------------------------------------------------------------------------
# Evidence registry
# ---------------------------------------------------------------------------

T = "tests/"
W = "web/tests/"

REQ_REGISTRY: dict[str, dict[str, object]] = {
    "G01": {
        "tasks": "02、03、08",
        "evidence": (
            f"{T}test_reset_publication.py",
            f"{T}test_reset_market.py",
            f"{W}e2e/reset-market.spec.ts",
            "web/src/pages/Overview.tsx",
        ),
    },
    "G02": {
        "tasks": "09、14",
        "evidence": (
            f"{T}test_reset_market.py",
            f"{T}test_reset_news.py",
            f"{W}e2e/reset-navigation.spec.ts",
        ),
    },
    "G03": {
        "tasks": "03、05、12",
        "evidence": (
            f"{W}unit/spa-navigation.test.ts",
            f"{W}e2e/reset-navigation.spec.ts",
            f"{W}e2e/reset-preferences.spec.ts",
        ),
    },
    "G04": {
        "tasks": "20",
        "evidence": (
            f"{W}unit/reset-export.test.ts",
            f"{W}e2e/reset-preferences.spec.ts",
        ),
    },
    "M01": {
        "tasks": "07、09",
        "evidence": (
            f"{T}test_reset_ingestion.py",
            f"{T}test_reset_market.py",
            f"{W}e2e/reset-market.spec.ts",
            "web/src/pages/Overview.tsx",
        ),
    },
    "M02": {
        "tasks": "09",
        "evidence": (
            f"{T}test_reset_market.py",
            f"{W}e2e/reset-market.spec.ts",
        ),
    },
    "N01": {
        "tasks": "14",
        "evidence": (
            f"{T}test_reset_news.py",
            f"{W}e2e/reset-news.spec.ts",
            "web/src/pages/News.tsx",
        ),
    },
    "N02": {
        "tasks": "14",
        "evidence": (
            f"{T}test_reset_news.py",
            f"{W}e2e/reset-news.spec.ts",
        ),
    },
    "N03": {
        "tasks": "14、18",
        "evidence": (
            f"{T}test_reset_news.py",
            f"{T}test_reset_ai_tasks.py",
            f"{W}unit/reset-news-api.test.ts",
        ),
    },
    "L01": {
        "tasks": "10、11、12",
        "evidence": (
            f"{T}test_reset_role_api.py",
            f"{T}test_reset_role_publication.py",
            f"{W}e2e/reset-leaders.spec.ts",
            "web/src/pages/Leaders.tsx",
        ),
    },
    "L02": {
        "tasks": "07、11、12",
        "evidence": (
            f"{T}test_reset_role_publication.py",
            f"{T}test_reset_role_api.py",
            f"{W}e2e/reset-leaders.spec.ts",
        ),
    },
    "L03": {
        "tasks": "11、12",
        "evidence": (
            f"{T}test_reset_role_contracts.py",
            f"{T}test_reset_role_publication.py",
            f"{W}e2e/reset-leaders.spec.ts",
        ),
    },
    "P01": {
        "tasks": "13",
        "evidence": (
            f"{T}test_reset_analysis.py",
            f"{W}e2e/reset-analysis.spec.ts",
            "web/src/pages/Analysis.tsx",
        ),
    },
    "P02": {
        "tasks": "13",
        "evidence": (
            f"{T}test_reset_analysis.py",
            f"{W}e2e/reset-analysis.spec.ts",
        ),
    },
    "S01": {
        "tasks": "12、13、14、16",
        "evidence": (
            f"{T}test_reset_analysis.py",
            f"{T}test_reset_market.py",
            f"{W}e2e/reset-navigation.spec.ts",
            "web/src/pages/SecurityDetail.tsx",
        ),
    },
    "U01": {
        "tasks": "04、05、06",
        "evidence": (
            f"{W}unit/reset-local.test.ts",
            f"{W}unit/reset-backup.test.ts",
            f"{W}e2e/reset-preferences.spec.ts",
        ),
    },
    "U02": {
        "tasks": "04、05、06",
        "evidence": (
            f"{W}unit/reset-note-provenance.test.ts",
            f"{W}unit/reset-local.test.ts",
            f"{W}e2e/reset-review.spec.ts",
        ),
    },
    "U03": {
        "tasks": "05、17、18",
        "evidence": (
            f"{T}test_reset_ai_tasks.py",
            f"{W}unit/review-draft.test.ts",
            f"{W}e2e/reset-review.spec.ts",
        ),
    },
    "U04": {
        "tasks": "05、06、11",
        "evidence": (
            f"{W}unit/reset-note-provenance.test.ts",
            f"{W}e2e/reset-review.spec.ts",
        ),
    },
    "R01": {
        "tasks": "15",
        "evidence": (
            f"{T}test_reset_regulation.py",
            f"{T}test_reset_regulation_api.py",
            f"{W}e2e/reset-regulation.spec.ts",
            "web/src/pages/Regulation.tsx",
        ),
    },
    "R02": {
        "tasks": "15、16",
        "evidence": (
            f"{T}test_reset_regulation.py",
            f"{W}e2e/reset-regulation.spec.ts",
        ),
    },
    "R03": {
        "tasks": "15、16",
        "evidence": (
            f"{T}test_reset_regulation.py",
            f"{W}e2e/reset-regulation.spec.ts",
        ),
    },
    "R04": {
        "tasks": "15",
        "evidence": (
            f"{T}test_reset_regulation.py",
            f"{T}test_reset_retention.py",
        ),
    },
    "Q01": {
        "tasks": "07、08、09",
        "evidence": (
            f"{T}test_reset_publication.py",
            f"{T}test_reset_market.py",
            f"{T}test_reset_contracts.py",
        ),
    },
    "Q02": {
        "tasks": "07、08、14、15",
        "evidence": (
            f"{T}test_reset_ingestion.py",
            f"{T}test_reset_contracts.py",
        ),
    },
    "Q03": {
        "tasks": "07、20",
        "evidence": (f"{T}test_reset_ingestion.py",),
    },
    "Q04": {
        "tasks": "03、05、08、09、16",
        "evidence": (
            f"{T}test_reset_ingestion.py",
            f"{T}test_reset_publication.py",
        ),
    },
    "D01": {
        "tasks": "07、19",
        "evidence": (
            f"{T}test_owner_api.py",
            f"{T}test_reset_public_gateway.py",
        ),
    },
    "D02": {
        "tasks": "07",
        "evidence": (f"{T}test_reset_ingestion.py",),
    },
    "D03": {
        "tasks": "08",
        "evidence": (
            f"{T}test_reset_publication.py",
            f"{W}e2e/reset-market.spec.ts",
        ),
    },
    "D04": {
        "tasks": "06、07、08、20",
        "evidence": (
            f"{T}test_reset_retention.py",
            f"{W}e2e/reset-recovery.spec.ts",
        ),
    },
    "A01": {
        "tasks": "17、18",
        "evidence": (
            f"{T}test_reset_ai_tasks.py",
            f"{W}unit/reset-ai.test.ts",
        ),
    },
    "A02": {
        "tasks": "14、17、18",
        "evidence": (
            f"{T}test_reset_ai_tasks.py",
            f"{T}test_reset_news.py",
            f"{W}unit/reset-ai.test.ts",
        ),
    },
    "A03": {
        "tasks": "12、17、18",
        "evidence": (
            f"{T}test_reset_ai_tasks.py",
            f"{W}unit/reset-ai.test.ts",
        ),
    },
    "A04": {
        "tasks": "05、17、18",
        "evidence": (
            f"{T}test_reset_ai_tasks.py",
            f"{W}unit/review-draft.test.ts",
            f"{W}e2e/reset-review.spec.ts",
        ),
    },
    "A05": {
        "tasks": "18、20",
        "evidence": (
            f"{T}test_reset_ai_verification.py",
            f"{T}test_reset_ai_tasks.py",
            f"{W}e2e/reset-ai.spec.ts",
        ),
    },
    "E01": {
        "tasks": "21、29",
        "evidence": (
            f"{T}test_reset_realtime.py",
            f"{W}e2e/reset-realtime.spec.ts",
            "docs/acceptance/reset-realtime.md",
        ),
    },
    "E02": {
        "tasks": "22、29",
        "evidence": (
            f"{T}test_reset_lhb.py",
            f"{W}e2e/reset-lhb.spec.ts",
            "docs/acceptance/reset-lhb.md",
        ),
    },
    "E03": {
        "tasks": "23、29",
        "evidence": (
            f"{T}test_reset_wordcloud.py",
            f"{W}e2e/reset-wordcloud.spec.ts",
        ),
    },
    "E04": {
        "tasks": "24、29",
        "evidence": (
            f"{T}test_reset_rotation.py",
            f"{W}e2e/reset-rotation.spec.ts",
            "docs/acceptance/reset-leaders.md",
        ),
    },
    "E05": {
        "tasks": "26、29",
        "evidence": (
            f"{T}test_reset_query.py",
            f"{W}e2e/reset-query.spec.ts",
            "web/src/features/query/QueryPanel.tsx",
            "docs/acceptance/reset-query.md",
        ),
    },
    "B01": {
        "tasks": "27、29",
        "status": "blocked",
        "blocked_reason": (
            "Task 27 not implemented: EX14 PIT backtest / factor health check / "
            "walk-forward"
        ),
        "evidence": ("docs/acceptance/reset-final.md",),
    },
    "B02": {
        "tasks": "27、29",
        "status": "blocked",
        "blocked_reason": (
            "Task 27 not implemented: EX15 local simulation ledger / AI uplift "
            "comparison"
        ),
        "evidence": ("docs/acceptance/reset-final.md",),
    },
    "X01": {
        "tasks": "19",
        "evidence": (
            f"{T}test_owner_api.py",
            f"{T}test_reset_public_gateway.py",
        ),
    },
    "X02": {
        "tasks": "17、18、19",
        "evidence": (
            f"{T}test_reset_ai_verification.py",
            f"{T}test_reset_query.py",
            f"{W}unit/reset-ai.test.ts",
        ),
    },
    "X03": {
        "tasks": "07、08、19、20",
        "evidence": (
            f"{T}test_reset_retention.py",
            f"{W}e2e/reset-recovery.spec.ts",
        ),
    },
    "V01": {
        "tasks": "03、20",
        "evidence": (
            f"{W}unit/spa-navigation.test.ts",
            f"{W}e2e/reset-navigation.spec.ts",
        ),
    },
    "V02": {
        "tasks": "03、12、16",
        "evidence": (
            f"{W}unit/role-labels.test.ts",
            f"{W}e2e/reset-leaders.spec.ts",
        ),
    },
    "V03": {
        "tasks": "09、12、13",
        "evidence": (
            f"{W}e2e/reset-analysis.spec.ts",
            f"{W}e2e/reset-leaders.spec.ts",
        ),
    },
    "V04": {
        "tasks": "03、05、12、13",
        "evidence": (
            f"{W}e2e/reset-navigation.spec.ts",
            "web/src/pages/SecurityDetail.tsx",
        ),
    },
    "V05": {
        "tasks": "13",
        "evidence": (
            f"{T}test_reset_analysis.py",
            f"{W}e2e/reset-analysis.spec.ts",
        ),
    },
    "V06": {
        "tasks": "03、20",
        "evidence": (
            f"{W}e2e/reset-realtime.spec.ts",
            f"{W}e2e/reset-preferences.spec.ts",
        ),
    },
    "V07": {
        "tasks": "03、17",
        "evidence": (f"{W}e2e/reset-realtime.spec.ts",),
    },
    "V08": {
        "tasks": "05、12、14、18",
        "evidence": (
            f"{T}test_reset_news.py",
            f"{W}e2e/reset-news.spec.ts",
        ),
    },
    "V09": {
        "tasks": "25、29",
        "evidence": (
            f"{W}unit/reset-preferences.test.ts",
            f"{W}e2e/reset-preferences.spec.ts",
        ),
    },
    "V10": {
        "tasks": "25、29",
        "evidence": (
            f"{W}unit/reset-preferences.test.ts",
            f"{W}e2e/reset-preferences.spec.ts",
        ),
    },
    "V11": {
        "tasks": "25、29",
        "evidence": (
            f"{W}unit/reset-preferences.test.ts",
            f"{W}e2e/reset-preferences.spec.ts",
        ),
    },
}

# task -> (status, app surface, route, evidence)
EX_REGISTRY: dict[str, tuple[str, str, str | None, tuple[str, ...]]] = {
    "EX01": (
        "13",
        "owner",
        "/api/analysis/jobs",
        (f"{T}test_analysis_jobs.py", f"{T}test_owner_api.py"),
    ),
    "EX02": (
        "13",
        "owner",
        "/api/analysis/jobs/{job_id}",
        (f"{T}test_analysis_jobs.py",),
    ),
    "EX03": (
        "14",
        "owner",
        "/api/news/{article_id}/associations/review",
        (f"{T}test_reset_news.py",),
    ),
    "EX04": (
        "21",
        "workbench",
        "/api/realtime/quotes",
        (f"{T}test_reset_realtime.py", f"{W}e2e/reset-realtime.spec.ts"),
    ),
    "EX05": (
        "21",
        "workbench",
        "/api/realtime/minutes",
        (f"{T}test_reset_realtime.py", f"{W}e2e/reset-realtime.spec.ts"),
    ),
    "EX06": (
        "21",
        "workbench",
        "/api/realtime/anomalies",
        (f"{T}test_reset_realtime.py", f"{W}e2e/reset-realtime.spec.ts"),
    ),
    "EX07": (
        "21",
        "workbench",
        "/api/market/sector-flow/intraday",
        (f"{T}test_reset_realtime.py", f"{W}e2e/reset-realtime.spec.ts"),
    ),
    "EX08": (
        "22",
        "workbench",
        "/api/lhb",
        (f"{T}test_reset_lhb.py", f"{W}e2e/reset-lhb.spec.ts"),
    ),
    "EX09": (
        "22",
        "workbench",
        "/api/lhb/seats/{seat_id}",
        (f"{T}test_reset_lhb.py", f"{W}e2e/reset-lhb.spec.ts"),
    ),
    "EX10": (
        "23",
        "workbench",
        "/api/news/wordcloud",
        (f"{T}test_reset_wordcloud.py", f"{W}e2e/reset-wordcloud.spec.ts"),
    ),
    "EX11": (
        "24",
        "workbench",
        "/api/topics/rotation",
        (f"{T}test_reset_rotation.py", f"{W}e2e/reset-rotation.spec.ts"),
    ),
    "EX12": (
        "26",
        "owner",
        "/api/query/parse",
        (
            f"{T}test_reset_query.py",
            f"{W}e2e/reset-query.spec.ts",
            "web/src/features/query/QueryPanel.tsx",
        ),
    ),
    "EX13": (
        "26",
        "owner",
        "/api/query/execute",
        (
            f"{T}test_reset_query.py",
            f"{W}e2e/reset-query.spec.ts",
        ),
    ),
    "EX14": (
        "27",
        "none",
        None,
        ("docs/acceptance/reset-final.md",),
    ),
    "EX15": (
        "27",
        "none",
        None,
        ("docs/acceptance/reset-final.md",),
    ),
    "EX16": (
        "28",
        "workbench",
        "/api/market/sentiment/research",
        (
            f"{T}test_reset_sentiment_research.py",
            f"{W}e2e/reset-sentiment-research.spec.ts",
            "web/src/features/research/SentimentPanel.tsx",
            "docs/acceptance/reset-sentiment-research.md",
        ),
    ),
}

BLOCKED_REQS = {"B01", "B02"}
BLOCKED_EX = {"EX14", "EX15"}
PRIVATE_PREFIXES = ("/api/owner", "/api/query", "/api/ai", "/api/research")

STATE_ANCHORS = (
    "t29-state:development-complete",
    "t29-state:synthetic-regression",
    "t29-state:real-input:blocked",
    "t29-state:official-rules:blocked",
    "t29-state:real-model-evaluation:blocked",
    "t29-state:public-sharing-permit:blocked",
    "t29-state:shadow-run-20-trading-days:blocked",
)


def _evidence_paths() -> set[str]:
    paths: set[str] = set()
    for entry in REQ_REGISTRY.values():
        paths.update(entry["evidence"])
    for _task, _surface, _route, evidence in EX_REGISTRY.values():
        paths.update(evidence)
    return paths


def _route_index(app) -> dict[str, set[str]]:
    index: dict[str, set[str]] = {}
    for route in app.routes:
        nested = getattr(getattr(route, "original_router", None), "routes", None)
        candidates = nested if nested is not None else [route]
        for candidate in candidates:
            if hasattr(candidate, "methods") and hasattr(candidate, "path"):
                index.setdefault(candidate.path, set()).update(candidate.methods)
    return index


# ---------------------------------------------------------------------------
# 57 REQ traceability
# ---------------------------------------------------------------------------


def test_req_registry_covers_exactly_the_57_prd_requirements():
    assert set(REQ_REGISTRY) == EXPECTED_REQS
    assert len(REQ_REGISTRY) == 57
    for req_id, entry in REQ_REGISTRY.items():
        assert isinstance(entry["tasks"], str) and entry["tasks"]
        evidence = entry["evidence"]
        assert isinstance(evidence, tuple) and len(evidence) >= 1
        assert len(set(evidence)) == len(evidence), f"{req_id} has duplicate evidence"


def test_delivered_requirements_carry_existing_automated_evidence():
    for req_id, entry in REQ_REGISTRY.items():
        if req_id in BLOCKED_REQS:
            continue
        evidence = entry["evidence"]
        automated = [
            path for path in evidence if path.startswith(("tests/", "web/tests/"))
        ]
        assert automated, f"{req_id} has no automated test evidence"
        for path in evidence:
            assert (ROOT / path).is_file(), f"{req_id} evidence missing: {path}"


def test_blocked_requirements_are_exactly_task27_and_carry_no_fake_evidence():
    blocked = {
        req_id
        for req_id, entry in REQ_REGISTRY.items()
        if entry.get("status") == "blocked"
    }
    assert blocked == BLOCKED_REQS
    for req_id in BLOCKED_REQS:
        entry = REQ_REGISTRY[req_id]
        assert "Task 27" in entry["blocked_reason"]
        assert entry["evidence"] == ("docs/acceptance/reset-final.md",)
        assert (ROOT / "docs/acceptance/reset-final.md").is_file(), (
            f"{req_id} blocked scope must be recorded in reset-final.md"
        )


# ---------------------------------------------------------------------------
# EX01-16 traceability and route surface
# ---------------------------------------------------------------------------


def test_ex_registry_covers_all_sixteen_extensions_with_task27_blocked():
    assert set(EX_REGISTRY) == {f"EX{number:02d}" for number in range(1, 17)}
    blocked = {
        ex_id
        for ex_id, (_task, surface, route, _evidence) in EX_REGISTRY.items()
        if surface == "none"
    }
    assert blocked == BLOCKED_EX
    for ex_id in BLOCKED_EX:
        task, surface, route, evidence = EX_REGISTRY[ex_id]
        assert task == "27"
        assert route is None
        assert evidence == ("docs/acceptance/reset-final.md",)


def test_all_ex_evidence_paths_exist():
    for ex_id, (_task, _surface, _route, evidence) in EX_REGISTRY.items():
        for path in evidence:
            assert (ROOT / path).is_file(), f"{ex_id} evidence missing: {path}"


def test_workbench_serves_delivered_extensions_and_no_private_namespace():
    from dragon_radar.http.app import create_workbench_app

    routes = _route_index(create_workbench_app())

    for ex_id, (_task, surface, route, _evidence) in EX_REGISTRY.items():
        if surface != "workbench":
            continue
        assert route in routes, f"{ex_id} missing from workbench: {route}"
        assert "GET" in routes[route], f"{ex_id} must be a GET read route"

    # EX01-03/12-13 are owner-only; EX14-15 must not exist at all.
    for private_route in (
        "/api/analysis/jobs",
        "/api/query/parse",
        "/api/query/execute",
        "/api/news/{article_id}/associations/review",
        "/api/ai/summarize",
        "/api/research/backtests",
        "/api/research/backtests/{run_id}",
    ):
        assert private_route not in routes, f"{private_route} leaked into workbench"

    # The local workbench HTTP surface is read-only by construction.
    assert all(
        methods <= {"GET", "HEAD"}
        for path, methods in routes.items()
        if path.startswith("/api/")
    )


def test_owner_app_serves_ex01_03_12_13_but_not_task27_research_routes():
    from dragon_radar.owner.app import create_owner_app
    from dragon_radar.owner.repository import DEFAULT_ORIGIN

    routes = _route_index(create_owner_app(origin=DEFAULT_ORIGIN))

    for ex_id in ("EX01", "EX02", "EX03", "EX12", "EX13"):
        _task, _surface, route, _evidence = EX_REGISTRY[ex_id]
        assert route in routes, f"{ex_id} missing from owner app: {route}"

    assert "/api/research/backtests" not in routes
    assert "/api/research/backtests/{run_id}" not in routes


def test_public_app_has_no_post_and_no_private_namespace():
    from dragon_radar.http.public_app import create_public_app

    routes = _route_index(create_public_app())

    for path, methods in routes.items():
        if path.startswith("/api/"):
            assert "POST" not in methods, f"public app must stay read-only: {path}"
            assert not path.startswith(PRIVATE_PREFIXES), (
                f"private namespace leaked to public app: {path}"
            )


# ---------------------------------------------------------------------------
# Migration and Task 27 artifact boundary
# ---------------------------------------------------------------------------


def test_migration_head_is_0013_query_with_no_task27_revision():
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    from dragon_radar.storage.database import SCHEMA_REVISION

    assert SCHEMA_REVISION == "0013_query"
    config = Config()
    config.set_main_option("script_location", "dragon_radar:migrations")
    script = ScriptDirectory.from_config(config)
    assert script.get_heads() == ["0013_query"]
    revisions = {migration.revision for migration in script.walk_revisions()}
    assert "0014_research" not in revisions


def test_task27_backtest_and_simulation_artifacts_remain_absent():
    absent = (
        "src/dragon_radar/http/routes/research.py",
        "src/dragon_radar/migrations/versions/0014_research.py",
        "web/src/repositories/local/simulation.ts",
        "web/src/features/research/BacktestPanel.tsx",
        "web/src/features/research/SimulationPanel.tsx",
        "tests/test_reset_backtest.py",
        "web/tests/unit/reset-simulation.test.ts",
        "web/tests/e2e/reset-research.spec.ts",
    )
    for relative in absent:
        assert not (ROOT / relative).exists(), f"Task 27 artifact appeared: {relative}"

    # The existing research feature directory holds only Task 28 sentiment.
    research_feature = ROOT / "web/src/features/research"
    assert {path.name for path in research_feature.glob("*.tsx")} == {
        "SentimentPanel.tsx"
    }


def test_task27_research_validation_is_a_closed_pure_subdomain():
    package = ROOT / "src/dragon_radar/research"
    assert package.is_dir(), "Slice A adds the research validation package"
    assert {path.name for path in package.glob("*.py")} == {
        "__init__.py",
        "models.py",
        "samples.py",
        "statistics.py",
        "factor_health.py",
        "forward_eval.py",
        "report.py",
    }
    for forbidden in (
        "repository.py",
        "backtest.py",
        "execution.py",
        "walk_forward.py",
        "ablation.py",
        "routes.py",
        "cli.py",
        "worker.py",
        "models_db.py",
    ):
        assert not (package / forbidden).exists(), f"forbidden module: {forbidden}"


# ---------------------------------------------------------------------------
# Seven independent acceptance states ledger (docs/acceptance/reset-final.md)
# ---------------------------------------------------------------------------


def test_reset_final_doc_records_seven_independent_states_without_production_claim():
    assert FINAL_ACCEPTANCE_DOC.is_file(), (
        "Task 29 requires docs/acceptance/reset-final.md"
    )
    text = FINAL_ACCEPTANCE_DOC.read_text(encoding="utf-8")

    for anchor in STATE_ANCHORS:
        assert f"<!-- {anchor} -->" in text, f"missing state anchor {anchor}"
    for token in ("B01", "B02", "EX14", "EX15", "Task 27"):
        assert token in text
    assert "开发完成" in text and "生产就绪" in text
    assert "不等于生产就绪" in text
    # The development gate must never be presented as production acceptance.
    forbidden = ("生产全验收通过", "生产全验收PASS", "生产就绪：通过")
    assert not any(claim in text for claim in forbidden)


# ---------------------------------------------------------------------------
# Cross-domain publication binding (PostgreSQL integration)
# ---------------------------------------------------------------------------


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
def market_only_publication(database, repository):
    from dragon_radar.publication.models import PublicationCommand
    from dragon_radar.publication.repository import PublicationRepository
    from dragon_radar.publication.service import PublicationService

    payload = batch(
        security_states=(state(code="000001", name="Synthetic Alpha"),),
    )
    market = repository.publish(repository.import_batch(payload, key="t29-market").id)
    record = PublicationService(database, repository).publish(
        PublicationCommand(
            trade_date=payload.trade_date,
            market_snapshot_id=market.id,
            disabled_domains=("topic", "news", "regulation", "role"),
        ),
        key="t29-publication",
    )
    return payload, record, PublicationRepository(database, repository)


@pytest.mark.anyio
async def test_pinned_publication_serves_every_market_view_without_recomputation(
    market_only_publication, monkeypatch
):
    from dragon_radar.http.app import create_workbench_app

    payload, record, publication_repository = market_only_publication
    monkeypatch.setattr(
        "dragon_radar.readmodels.market.materialize_market",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("published read model was recomputed on read")
        ),
    )

    app = create_workbench_app(repository=publication_repository)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        for path in (
            "/api/market/overview",
            "/api/market/sector-flow?top_n=10",
            "/api/market/sentiment?days=20",
            "/api/market/sentiment/research?days=20",
            "/api/market/heatmap?days=60",
        ):
            separator = "&" if "?" in path else "?"
            response = await client.get(f"{path}{separator}snapshot_id={record.id}")
            assert response.status_code == 200, path
            assert response.json()["snapshot_id"] == str(record.id), path

        listed = await client.get(
            "/api/snapshots/list", params={"date": payload.trade_date.isoformat()}
        )
        assert listed.status_code == 200
        summaries = listed.json()["data"]["snapshots"]
        assert [item["snapshot_id"] for item in summaries] == [str(record.id)]

        summary = await client.get(f"/api/snapshots/{record.id}")
        assert summary.status_code == 200
        assert summary.json()["snapshot_id"] == str(record.id)

        # Unknown publications return an empty envelope and must never be
        # substituted by the current/pinned data.
        missing = await client.get(
            "/api/market/overview", params={"snapshot_id": uuid4()}
        )
        assert missing.status_code == 200
        missing_body = missing.json()
        assert missing_body["snapshot_id"] is None
        assert "no_data" in missing_body["quality_flags"]

        # The workbench enforces read-only even when a body is supplied.
        blocked = await client.post("/api/market/overview")
        assert blocked.status_code == 403


def test_publication_service_rejects_mismatched_market_snapshot_date(
    database, repository
):
    from dragon_radar.publication.models import PublicationCommand
    from dragon_radar.publication.service import PublicationService

    payload = batch(
        security_states=(state(code="000001", name="Synthetic Alpha"),),
    )
    market = repository.publish(
        repository.import_batch(payload, key="t29-mismatch-market").id
    )
    with pytest.raises(ValueError, match="date mismatch"):
        PublicationService(database, repository).publish(
            PublicationCommand(
                trade_date=payload.trade_date + timedelta(days=1),
                market_snapshot_id=market.id,
                disabled_domains=("topic", "news", "regulation", "role"),
            ),
            key="t29-mismatch",
        )


def test_publication_command_forbids_topic_reference_while_topic_domain_disabled():
    from dragon_radar.publication.models import PublicationCommand

    with pytest.raises(ValueError):
        PublicationCommand(
            trade_date=date(2026, 9, 8),
            market_snapshot_id=uuid4(),
            topic_snapshot_id=uuid4(),
            disabled_domains=("topic", "news", "regulation", "role"),
        )
