"""Local license and promoted selection documents never accept private tasks."""

import pytest


def test_operator_documents_reject_duplicates():
    from dragon_radar.public_daily_ai.cli import load_document

    with pytest.raises(ValueError):
        load_document('{"reference":"a","reference":"b"}')


def test_selection_cli_rejects_private_task_ids():
    from dragon_radar.public_daily_ai.cli import validate_selections

    with pytest.raises(ValueError):
        validate_selections([{"task_id": "private"}])


def test_cli_help_never_connects_to_database(monkeypatch):
    from dragon_radar.public_daily_ai.cli import main

    monkeypatch.delenv("DRAGON_DATABASE_URL", raising=False)
    with pytest.raises(SystemExit) as result:
        main(["--help"])
    assert result.value.code == 0
