"""Local CLI integration tests; credentials are only environment configuration."""

import json
import os
import subprocess
import sys

from factories import batch, grant
from sqlalchemy import text


def run_cli(args, database_url=None):
    env = dict(os.environ)
    env.pop("DRAGON_DATABASE_URL", None)
    if database_url:
        env["DRAGON_DATABASE_URL"] = database_url
    return subprocess.run(
        [sys.executable, "-m", "dragon_radar", *args],
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )


def test_help_no_database_and_missing_environment():
    help_result = run_cli(["--help"])
    assert help_result.returncode == 0
    assert "source-register" in help_result.stdout
    missing = run_cli(["migrate"])
    assert missing.returncode != 0
    assert "DRAGON_DATABASE_URL is required" in missing.stderr
    assert "Traceback" not in missing.stderr


def test_invalid_database_configuration_sanitized():
    result = run_cli(["migrate"], "garbage-password-secret")
    assert result.returncode != 0
    assert "secret" not in result.stderr
    assert "Traceback" not in result.stderr


def test_cli_migrate_unknown_revision_is_sanitized(database):
    with database.engine.begin() as connection:
        connection.execute(
            text("UPDATE alembic_version SET version_num = :revision"),
            {"revision": "private-revision-marker"},
        )
    result = run_cli(
        ["migrate"],
        database.engine.url.render_as_string(hide_password=False),
    )
    assert result.returncode != 0
    assert result.stderr.strip() == "Migration failed"
    assert "private-revision-marker" not in result.stdout + result.stderr
    assert "Traceback" not in result.stdout + result.stderr


def test_cli_migrate_register_import_publish_revoke(database_url, tmp_path):
    assert run_cli(["migrate"], database_url).returncode == 0
    assert run_cli(["migrate"], database_url).returncode == 0
    grant_path = tmp_path / "grant.json"
    grant_path.write_text(grant().model_dump_json(), encoding="utf-8")
    assert run_cli(["source-register", str(grant_path)], database_url).returncode == 0
    # Wall-clock-safe cutoff; the factory's fixed noon is intentionally not used.
    from datetime import UTC, datetime

    payload = batch(as_of=datetime.now(UTC))
    batch_path = tmp_path / "batch.json"
    batch_path.write_text(payload.model_dump_json(), encoding="utf-8")
    imported = run_cli(["import", str(batch_path), "--key", "cli"], database_url)
    assert imported.returncode == 0, imported.stderr
    batch_id = json.loads(imported.stdout)["batch_id"]
    retry = run_cli(["import", str(batch_path), "--key", "cli"], database_url)
    assert json.loads(retry.stdout)["batch_id"] == batch_id
    published = run_cli(["publish", batch_id], database_url)
    assert published.returncode == 0, published.stderr
    assert json.loads(published.stdout)["revision"] == 1
    changed = payload.model_dump(mode="json")
    changed["complete_pages"] = False
    batch_path.write_text(json.dumps(changed), encoding="utf-8")
    conflict = run_cli(["import", str(batch_path), "--key", "cli"], database_url)
    assert conflict.returncode != 0
    assert "conflict" in conflict.stderr.lower()
    assert "Traceback" not in conflict.stderr
    assert run_cli(["source-revoke", "synthetic"], database_url).returncode == 0
    assert run_cli(["publish", batch_id], database_url).returncode != 0


def test_cli_invalid_json_and_structural_payload_not_archived(database, tmp_path):
    path = tmp_path / "invalid.json"
    for content in ('{"private-secret":', '{"private-secret": "credentials"}'):
        path.write_text(content, encoding="utf-8")
        result = run_cli(
            ["import", str(path), "--key", "bad"],
            database.engine.url.render_as_string(hide_password=False),
        )
        assert result.returncode != 0
        assert "private-secret" not in result.stderr
        assert "credentials" not in result.stderr
        assert "Traceback" not in result.stderr


def test_cli_bad_key_and_missing_file_are_safe(database_url, tmp_path):
    result = run_cli(
        ["import", str(tmp_path / "sensitive-filename"), "--key", "bad\nkey"],
        database_url,
    )
    assert result.returncode != 0
    assert "sensitive-filename" not in result.stderr


def test_serve_host_port_and_no_proxy_trust(monkeypatch, database_url):
    from dragon_radar.cli import main

    calls = []
    monkeypatch.setenv("DRAGON_DATABASE_URL", database_url)
    monkeypatch.setattr("uvicorn.run", lambda app, **kwargs: calls.append(kwargs))
    assert main(["serve", "--host", "127.0.0.1", "--port", "8123"]) == 0
    assert calls[0]["host"] == "127.0.0.1"
    assert calls[0]["port"] == 8123
    assert calls[0]["proxy_headers"] is False
    assert calls[0]["access_log"] is False
