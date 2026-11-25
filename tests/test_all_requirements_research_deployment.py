"""New owner admissions and worker compute select the same server contract."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from dragon_radar.owner.app import create_owner_app
from dragon_radar.research.cache_contract import SCHEMAS


@pytest.mark.parametrize("version", [None, "v1", "v2"])
def test_owner_deployment_selects_new_health_contract_with_explicit_legacy_option(
    monkeypatch,
    version,
):
    monkeypatch.setenv("DRAGON_TOPIC_CURSOR_SECRET", "synthetic-test-only-secret")
    monkeypatch.setenv("DRAGON_REALTIME_CURSOR_SECRET", "synthetic-test-only-secret")
    if version is None:
        monkeypatch.delenv("DRAGON_RESEARCH_RESULT_VERSION", raising=False)
    else:
        monkeypatch.setenv("DRAGON_RESEARCH_RESULT_VERSION", version)
    app = create_owner_app(
        database_url="postgresql+psycopg://dragon_test@127.0.0.1:63899/dragon_test",
    )
    assert (
        app.state.research_repository.result_schema_version == SCHEMAS[version or "v2"]
    )
    app.state.research_repository.database.close()


@pytest.mark.parametrize("version", [None, "v1", "v2"])
def test_worker_admission_and_loader_use_identical_deployment_version(
    monkeypatch,
    version,
):
    from dragon_radar.owner import cli
    from dragon_radar.research import production

    if version is None:
        monkeypatch.delenv("DRAGON_RESEARCH_RESULT_VERSION", raising=False)
    else:
        monkeypatch.setenv("DRAGON_RESEARCH_RESULT_VERSION", version)
    loader = Mock(spec=production.load_production_compute, return_value=lambda *_: {})
    monkeypatch.setattr(production, "load_production_compute", loader)
    for name in (
        "OwnerRepository",
        "PublicationRepository",
        "AnalysisJobRepository",
        "ResearchRunRepository",
        "AnalysisJobWorker",
        "ResearchBacktestWorker",
    ):
        monkeypatch.setattr(cli, name, Mock(spec=getattr(cli, name)))
    monkeypatch.setattr(cli.threading, "Thread", Mock(spec=cli.threading.Thread))
    database = object()
    cli.execute(
        SimpleNamespace(
            command="analysis-worker",
            worker_id="synthetic-worker",
            poll_seconds=1,
            lease_seconds=30,
            max_attempts=3,
            research_production_request="test-request.json",
        ),
        SimpleNamespace(database=database),
    )
    selected = version or "v2"
    assert cli.ResearchRunRepository.call_args.kwargs["result_version"] == selected
    assert loader.call_args.kwargs["result_version"] == selected


def test_unknown_deployment_contract_fails_before_database_connection(monkeypatch):
    monkeypatch.setenv("DRAGON_RESEARCH_RESULT_VERSION", "future")
    monkeypatch.setenv("DRAGON_TOPIC_CURSOR_SECRET", "synthetic-test-only-secret")
    monkeypatch.setenv("DRAGON_REALTIME_CURSOR_SECRET", "synthetic-test-only-secret")
    with pytest.raises(ValueError, match="research result version"):
        create_owner_app(
            database_url="postgresql+psycopg://dragon_test@127.0.0.1:63899/dragon_test",
        )
