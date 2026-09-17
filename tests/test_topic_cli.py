"""Topic operator CLI integration tests with disclosure-safe output contracts."""

import json
import os
import subprocess
import sys

from factories import grant
from sqlalchemy import text
from topic_factories import TOPIC_DAYS, topic_batch

SENSITIVE_VALUES = (
    "source-credential-secret",
    "full-evidence-text-secret",
    "synthetic:membership-a",
)


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


def assert_safe_output(result):
    output = result.stdout + result.stderr
    assert "Traceback" not in output
    for value in SENSITIVE_VALUES:
        assert value not in output
    for field in (
        "canonical_json",
        "payload_hash",
        "quality_report",
        "grant",
        "provenance",
        "topics",
        "memberships",
        "evidence_refs",
        "details",
    ):
        assert field not in output


def register_source(database_url, tmp_path):
    source_grant = grant(reference=SENSITIVE_VALUES[0])
    path = tmp_path / "topic-grant.json"
    path.write_text(source_grant.model_dump_json(), encoding="utf-8")
    result = run_cli(["source-register", str(path)], database_url)
    assert result.returncode == 0, result.stderr


def sensitive_batch():
    payload = topic_batch()
    topic = payload.topics[0].model_copy(
        update={"source_reference": SENSITIVE_VALUES[1]}
    )
    membership = payload.memberships[0].model_copy(
        update={"evidence_refs": (SENSITIVE_VALUES[2],)}
    )
    embedded_grant = payload.grant.model_copy(update={"reference": SENSITIVE_VALUES[0]})
    return payload.model_copy(
        update={
            "topics": (topic,),
            "memberships": (membership,),
            "grant": embedded_grant,
        }
    )


def test_topic_import_publish_and_attempt_outputs_are_minimal(database, tmp_path):
    database_url = database.engine.url.render_as_string(hide_password=False)
    register_source(database_url, tmp_path)
    path = tmp_path / "topic-batch.json"
    path.write_text(sensitive_batch().model_dump_json(), encoding="utf-8")

    imported = run_cli(["topic-import", str(path), "--key", "topic-cli"], database_url)
    assert imported.returncode == 0, imported.stderr
    imported_body = json.loads(imported.stdout)
    assert set(imported_body) == {"batch_id", "status"}
    assert imported_body["status"] == "imported"
    assert_safe_output(imported)

    attempts = run_cli(["topic-attempts", "--status", "succeeded"], database_url)
    assert attempts.returncode == 0, attempts.stderr
    attempts_body = json.loads(attempts.stdout)
    assert set(attempts_body) == {"attempts"}
    assert len(attempts_body["attempts"]) == 1
    attempt = attempts_body["attempts"][0]
    assert set(attempt) == {
        "attempt_id",
        "failed_cursor",
        "missing_items",
        "request_from",
        "request_to",
        "retry_from",
        "retry_to",
        "stage",
        "status",
    }
    assert attempt["status"] == "succeeded"
    assert_safe_output(attempts)

    shown = run_cli(["topic-attempt-show", attempt["attempt_id"]], database_url)
    assert shown.returncode == 0, shown.stderr
    assert json.loads(shown.stdout) == attempt
    assert_safe_output(shown)

    published = run_cli(
        ["topic-publish", imported_body["batch_id"]],
        database_url,
    )
    assert published.returncode == 0, published.stderr
    published_body = json.loads(published.stdout)
    assert set(published_body) == {
        "batch_id",
        "revision",
        "snapshot_id",
        "status",
        "supersedes",
    }
    assert published_body["batch_id"] == imported_body["batch_id"]
    assert published_body["status"] == "published"
    assert_safe_output(published)


def test_topic_retry_revalidates_complete_manifest_without_publishing(
    database, tmp_path
):
    database_url = database.engine.url.render_as_string(hide_password=False)
    register_source(database_url, tmp_path)
    payload = sensitive_batch()
    request_from = TOPIC_DAYS[1]
    request_to = TOPIC_DAYS[-2]
    incomplete = payload.model_copy(
        update={
            "page_manifest": payload.page_manifest.model_copy(
                update={
                    "request_from": request_from,
                    "request_to": request_to,
                }
            )
        }
    )
    failed_path = tmp_path / "failed-topic-batch.json"
    failed_path.write_text(incomplete.model_dump_json(), encoding="utf-8")

    failed = run_cli(
        ["topic-import", str(failed_path), "--key", "failed-topic-cli"],
        database_url,
    )
    assert failed.returncode != 0
    assert_safe_output(failed)

    attempts = run_cli(["topic-attempts", "--status", "failed"], database_url)
    assert attempts.returncode == 0, attempts.stderr
    failed_attempt = json.loads(attempts.stdout)["attempts"][0]
    assert failed_attempt["status"] == "failed"
    assert failed_attempt["stage"] == "quality"
    assert failed_attempt["missing_items"]
    assert failed_attempt["request_from"] == request_from.isoformat()
    assert failed_attempt["request_to"] == request_to.isoformat()
    assert failed_attempt["retry_from"] == payload.coverage_from.isoformat()
    assert failed_attempt["retry_to"] == payload.coverage_to.isoformat()
    assert (
        failed_attempt["request_from"],
        failed_attempt["request_to"],
    ) != (
        failed_attempt["retry_from"],
        failed_attempt["retry_to"],
    )
    assert_safe_output(attempts)

    shown = run_cli(
        ["topic-attempt-show", failed_attempt["attempt_id"]],
        database_url,
    )
    assert shown.returncode == 0, shown.stderr
    assert json.loads(shown.stdout) == failed_attempt
    assert_safe_output(shown)

    rejected_retry = run_cli(
        [
            "topic-retry",
            failed_attempt["attempt_id"],
            "--path",
            str(failed_path),
            "--key",
            "retry-still-incomplete",
        ],
        database_url,
    )
    assert rejected_retry.returncode != 0
    assert_safe_output(rejected_retry)
    failed_attempts = json.loads(
        run_cli(["topic-attempts", "--status", "failed"], database_url).stdout
    )["attempts"]
    assert len(failed_attempts) == 2
    with database.engine.connect() as connection:
        assert connection.scalar(text("SELECT count(*) FROM topic_snapshots")) == 0

    retry_path = tmp_path / "retry-topic-batch.json"
    retry_path.write_text(payload.model_dump_json(), encoding="utf-8")
    retried = run_cli(
        [
            "topic-retry",
            failed_attempt["attempt_id"],
            "--path",
            str(retry_path),
            "--key",
            "retry-topic-cli",
        ],
        database_url,
    )
    assert retried.returncode == 0, retried.stderr
    retried_body = json.loads(retried.stdout)
    assert set(retried_body) == {"batch_id", "status"}
    assert retried_body["status"] == "imported"
    assert_safe_output(retried)
    with database.engine.connect() as connection:
        assert connection.scalar(text("SELECT count(*) FROM topic_snapshots")) == 0


def test_topic_retry_rejects_nonfailed_attempt_and_range_mismatch(database, tmp_path):
    database_url = database.engine.url.render_as_string(hide_password=False)
    register_source(database_url, tmp_path)
    payload = sensitive_batch()
    good_path = tmp_path / "good-topic-batch.json"
    good_path.write_text(payload.model_dump_json(), encoding="utf-8")
    imported = run_cli(
        ["topic-import", str(good_path), "--key", "successful-topic-cli"],
        database_url,
    )
    assert imported.returncode == 0, imported.stderr
    succeeded = json.loads(
        run_cli(["topic-attempts", "--status", "succeeded"], database_url).stdout
    )["attempts"][0]

    nonfailed = run_cli(
        [
            "topic-retry",
            succeeded["attempt_id"],
            "--path",
            str(good_path),
            "--key",
            "not-allowed",
        ],
        database_url,
    )
    assert nonfailed.returncode != 0
    assert_safe_output(nonfailed)

    incomplete = payload.model_copy(
        update={
            "coverage_cells": tuple(
                cell
                for cell in payload.coverage_cells
                if cell.trade_date != TOPIC_DAYS[-1]
            )
        }
    )
    failed_path = tmp_path / "failed.json"
    failed_path.write_text(incomplete.model_dump_json(), encoding="utf-8")
    assert (
        run_cli(
            ["topic-import", str(failed_path), "--key", "range-source"],
            database_url,
        ).returncode
        != 0
    )
    failed_attempt = json.loads(
        run_cli(["topic-attempts", "--status", "failed"], database_url).stdout
    )["attempts"][0]
    changed_range = payload.model_copy(
        update={
            "coverage_from": TOPIC_DAYS[1],
            "page_manifest": payload.page_manifest.model_copy(
                update={"request_from": TOPIC_DAYS[1]}
            ),
        }
    )
    changed_path = tmp_path / "changed-range.json"
    changed_path.write_text(changed_range.model_dump_json(), encoding="utf-8")
    mismatched = run_cli(
        [
            "topic-retry",
            failed_attempt["attempt_id"],
            "--path",
            str(changed_path),
            "--key",
            "wrong-range",
        ],
        database_url,
    )
    assert mismatched.returncode != 0
    assert_safe_output(mismatched)
    with database.engine.connect() as connection:
        assert connection.scalar(text("SELECT count(*) FROM topic_snapshots")) == 0


def test_topic_attempt_errors_are_sanitized(database):
    database_url = database.engine.url.render_as_string(hide_password=False)
    invalid_status = run_cli(
        ["topic-attempts", "--status", "database-password-secret"],
        database_url,
    )
    assert invalid_status.returncode != 0
    assert "database-password-secret" not in invalid_status.stderr
    assert_safe_output(invalid_status)

    missing = run_cli(
        ["topic-attempt-show", "00000000-0000-0000-0000-000000000000"],
        database_url,
    )
    assert missing.returncode != 0
    assert missing.stderr.strip() == "Record not found"
    assert_safe_output(missing)
