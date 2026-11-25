"""Database-free regressions for the finite parent review findings F1-F5."""

import importlib
import inspect
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from factories import NOW, grant
from fastapi import FastAPI
from sqlalchemy.dialects import postgresql
from starlette.requests import Request

from dragon_radar.http.access import PublicPublicationRepository
from dragon_radar.public_daily_ai.models import digest
from dragon_radar.public_daily_ai.repository import PublicDailyAIRepository
from dragon_radar.publication.retention import RetentionService
from dragon_radar.storage.repository import Forbidden


class Connection:
    def __init__(self, value=None):
        self.value = value
        self.statements = []

    def in_transaction(self):
        return True

    def scalar(self, statement):
        self.statements.append(statement)
        return self.value


def test_f1_real_public_facade_authorizes_without_market_or_task_access():
    sources = []
    current = grant(ai=True, sharing=True)
    market = SimpleNamespace(
        _source=lambda connection, source_id: sources.append(source_id) or current,
    )
    facade = PublicPublicationRepository(
        SimpleNamespace(),
        market,
        clock=lambda: NOW,
    )
    assert not hasattr(facade, "market")
    assert not hasattr(facade, "get_task")
    registry = PublicDailyAIRepository(SimpleNamespace(), facade, clock=lambda: NOW)
    registry.entitlements = SimpleNamespace(authorize=lambda *a, **k: None)
    payload = {"text": "Synthetic"}
    row = {
        "id": uuid4(),
        "article_revision_id": uuid4(),
        "rights_manifest": {
            "sources": [
                {"source_id": "synthetic", "grant": current.model_dump(mode="json")}
            ],
            "output": {"reference": "output-test"},
            "provider": "fake",
            "model_version": "fake-v1",
        },
        "safe_payload": payload,
        "content_hash": digest(payload),
    }
    registry.authorize(Connection(), row)
    assert sources == ["synthetic"]
    row["rights_manifest"]["sources"][0]["grant"]["ai"] = False
    with pytest.raises(Forbidden, match="Source rights"):
        registry.authorize(Connection(), row)


def test_f2_display_block_keeps_column_null_and_captures_dependency_cutoff():
    connection = Connection(37)
    database = SimpleNamespace(
        engine=SimpleNamespace(
            begin=lambda: nullcontext(connection),
        )
    )
    retention = RetentionService(database, SimpleNamespace(), clock=lambda: NOW)
    retention._revoke_grant = lambda *a, **k: None
    captured = {}

    def append(_connection, **values):
        captured.update(values)
        return {
            "id": uuid4(),
            "sequence": 1,
            "policy_reference": None,
            "cutoff_dependency_sequence": None,
            "occurred_at": NOW,
            **values,
        }

    retention._append = append
    receipt = retention.withdraw_display("synthetic")
    assert receipt.cutoff_dependency_sequence is None
    assert captured["details"].get("artifact_cutoff_dependency_sequence") == 37
    assert "cutoff_dependency_sequence" not in captured


def test_f2_historical_display_denial_uses_exact_artifact_and_sequence():
    module = importlib.import_module("dragon_radar.publication.repository")
    helper = getattr(module, "artifact_was_display_withdrawn", None)
    assert callable(helper), "Display cutoff authorization is missing"
    connection = Connection()
    assert helper(connection, "synthetic", "public_daily_ai", uuid4()) is False
    sql = str(
        connection.statements[-1].compile(
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    )
    assert "artifact_cutoff_dependency_sequence" in sql
    assert "source_dependencies.archive_type" in sql
    assert "source_dependencies.archive_id" in sql
    assert "source_dependencies.dependency_sequence <=" in sql
    assert "withdrawal_dependencies" not in sql


def test_f3_erased_consumer_identity_requires_prior_sealed_exact_binding(monkeypatch):
    migration = importlib.import_module(
        "dragon_radar.migrations.versions.0015_daily_ai",
    )
    statements = []
    monkeypatch.setattr(migration.op, "execute", statements.append)
    migration._install_withdrawal_binding_guard()
    sql = " ".join(statements[0].split())
    assert "prior_binding.source_dependency_id = d.id" in sql
    assert "prior_withdrawal.source_id = d.source_id" in sql
    assert "prior_withdrawal.scope = 'storage'" in sql
    assert "prior_withdrawal.action IN ( 'deleted', 'deidentified')" in sql
    assert "d.dependency_sequence <= prior_withdrawal.cutoff_dependency_sequence" in sql
    assert "prior_seal.withdrawal_id = prior_withdrawal.id" in sql
    assert "prior_withdrawal.sequence < w.sequence" in sql
    assert "n.article_revision_id = a.article_revision_id" in sql
    assert "d.derived_id = d.publication_id" in sql


def test_f4_resolve_summary_origin_union_before_any_source_lock():
    from dragon_radar.news.summary_publication import PromotedSummaryMaterializer
    from dragon_radar.publication.service import PublicationService

    helper = getattr(PromotedSummaryMaterializer, "source_ids", None)
    assert callable(helper), "Origin source pre-resolution is missing"
    choice = {
        "summary_id": uuid4(),
        "summary_revision": 1,
        "article_id": uuid4(),
        "article_revision_id": uuid4(),
        "article_revision": 1,
    }
    registry = SimpleNamespace(
        summary_source_ids=lambda connection, selections: {"A", "Z"},
    )
    materializer = PromotedSummaryMaterializer(None, registry, [choice])
    assert materializer.source_ids(Connection()) == {"A", "Z"}
    source = inspect.getsource(PublicationService.publish)
    assert source.index("summary_materializer.source_ids(connection)") < source.index(
        "grants = self.roles._lock_sources(connection, source_ids)"
    )
    assert "self.snapshot(" in inspect.getsource(PublicDailyAIRepository.summary)


def test_f5_summary_closed_body_translates_to_shared_revoke():
    from dragon_radar.ai.publication import AIPublicationService
    from dragon_radar.http.routes.ai_publication import install_ai_publication_routes

    app = FastAPI()
    install_ai_publication_routes(app)
    calls = []
    service = object.__new__(AIPublicationService)
    service.revoke = lambda *args, **kwargs: (
        calls.append((args, kwargs))
        or {
            "status": "unavailable",
        }
    )
    app.state.ai_publication_service = service
    routes = [
        nested
        for route in app.routes
        for nested in getattr(
            getattr(route, "original_router", None), "routes", [route]
        )
    ]
    route = next(
        r
        for r in routes
        if r.path.endswith(
            "published-summary/{summary_id}/revoke",
        )
    )
    body = {
        "expected_summary_revision": 2,
        "idempotency_key": "withdraw",
        "reason_code": "withdrawn_by_owner",
    }
    request = Request(
        {
            "type": "http",
            "app": app,
            "query_string": b"",
            "state": {"owner_json": body},
        }
    )
    owner = SimpleNamespace(token="owner", csrf="csrf")
    response = route.endpoint(uuid4(), uuid4(), request, owner)
    assert response.status_code == 200
    assert calls[0][0][-1].expected_archive_version == 2
    assert calls[0][1] == {"kind": "summary"}
    body["expected_archive_version"] = 2
    from dragon_radar.owner.security import BoundaryError, invalid_request

    with pytest.raises(BoundaryError) as error:
        route.endpoint(uuid4(), uuid4(), request, owner)
    assert error.value.status_code == invalid_request().status_code


def test_f5_ui_uses_kind_specific_expected_revision():
    source = (
        Path(__file__).parents[1] / "web/src/features/ai/publication-client.ts"
    ).read_text()
    assert "expected_summary_revision: receipt.version" in source
    assert "expected_archive_version: receipt.version" in source
