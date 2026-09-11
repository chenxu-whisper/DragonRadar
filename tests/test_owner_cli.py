"""Operator CLI integration: real isolated storage, processes and local files."""

import json
import os
import stat
import subprocess
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import func, select
from test_cli import run_cli

from dragon_radar.owner.errors import InvalidPairing, SessionUnauthorized
from dragon_radar.owner.models import owner_pairings
from dragon_radar.owner.repository import OwnerRepository

ORIGIN = "https://localhost:8030"


def url(database):
    return database.engine.url.render_as_string(hide_password=False)


def cli_pair(database, path, origin=ORIGIN):
    return run_cli(
        ["owner-pair", "--origin", origin, "--output", str(path)], url(database)
    )


def assert_safe_failure(result):
    assert result.returncode == 1
    assert result.stdout == ""
    assert "secret-marker" not in result.stderr
    assert "Traceback" not in result.stderr


def test_owner_commands_advertised_without_database():
    result = run_cli(["--help"])
    assert result.returncode == 0
    for command in (
        "owner-init",
        "owner-pair",
        "owner-revoke",
        "serve-owner",
        "source-register",
    ):
        assert command in result.stdout


def test_owner_initialize_and_revoke_are_explicit(database):
    owners = OwnerRepository(database)
    result = run_cli(["owner-init"], url(database))
    assert result.returncode == 0, result.stderr
    owner_id = owners.initialize()
    assert json.loads(result.stdout) == {"owner_id": str(owner_id)}
    assert run_cli(["owner-init"], url(database)).stdout == result.stdout
    issued = owners.exchange_pairing(owners.issue_pairing(ORIGIN), ORIGIN)
    code = owners.issue_pairing(ORIGIN)
    revoked = run_cli(["owner-revoke"], url(database))
    assert revoked.returncode == 0
    assert json.loads(revoked.stdout) == {"status": "revoked"}
    with pytest.raises(SessionUnauthorized):
        owners.authenticate(issued.token)
    with pytest.raises(InvalidPairing):
        owners.exchange_pairing(code, ORIGIN)


def test_owner_pair_file_and_stdout_no_credentials(database, tmp_path):
    owners = OwnerRepository(database)
    owners.initialize()
    path = tmp_path / "pairing.code"
    before = datetime.now(UTC)
    result = cli_pair(database, path)
    assert result.returncode == 0, result.stderr
    after = datetime.now(UTC)
    code = path.read_text(encoding="ascii").strip()
    assert len(code) == 43
    assert code not in result.stdout + result.stderr
    data = json.loads(result.stdout)
    assert set(data) == {"path", "expires_at"}
    assert Path(data["path"]) == path
    expires = datetime.fromisoformat(data["expires_at"])
    assert expires.tzinfo is not None
    assert 290 <= (expires - before).total_seconds() <= 310
    assert (expires - after).total_seconds() <= 300
    assert owners.exchange_pairing(code, ORIGIN).owner_id == owners.initialize()
    if os.name != "nt":
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
    else:
        command = (
            "$a=Get-Acl -LiteralPath $env:DRAGON_ACL_TEST_PATH; "
            "$i=[Security.Principal.WindowsIdentity]::GetCurrent().User.Value; "
            "$r=@($a.Access|ForEach-Object {"
            "$_.IdentityReference.Translate([Security.Principal.SecurityIdentifier]).Value"
            "}); "
            "@{protected=$a.AreAccessRulesProtected;current=$i;rules=$r}"
            "|ConvertTo-Json -Compress"
        )
        # No pairing value enters the shell environment or command line.
        checked = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", command],
            env={**os.environ, "DRAGON_ACL_TEST_PATH": str(path)},
            capture_output=True,
            text=True,
            check=False,
            timeout=20,
        )
        assert checked.returncode == 0, checked.stderr
        acl = json.loads(checked.stdout)
        assert acl["protected"]
        assert set(acl["rules"]) == {acl["current"], "S-1-5-18"}


@pytest.mark.parametrize("kind", ["file", "directory"])
def test_owner_pair_never_overwrites_or_issues_for_existing_target(
    database, tmp_path, kind
):
    owners = OwnerRepository(database)
    owners.initialize()
    old_code = owners.issue_pairing(ORIGIN)
    path = tmp_path / "secret-marker"
    if kind == "file":
        path.write_text("existing")
    else:
        path.mkdir()
    result = cli_pair(database, path)
    assert_safe_failure(result)
    if kind == "file":
        assert path.read_text() == "existing"
    assert owners.exchange_pairing(old_code, ORIGIN)


def test_owner_pair_uninitialized_removes_reserved_file(database, tmp_path):
    path = tmp_path / "secret-marker"
    result = cli_pair(database, path)
    assert_safe_failure(result)
    assert not path.exists()


@pytest.mark.parametrize(
    "origin",
    [
        "http://localhost:8030",
        "https://localhost:0",
        "https://localhost:8030/",
        "secret-marker",
    ],
)
def test_owner_pair_invalid_origin_no_file_or_issuance(database, tmp_path, origin):
    owners = OwnerRepository(database)
    owners.initialize()
    path = tmp_path / "pair.code"
    assert_safe_failure(cli_pair(database, path, origin))
    assert not path.exists()
    with database.engine.connect() as connection:
        assert connection.scalar(select(func.count()).select_from(owner_pairings)) == 0


def test_owner_pair_missing_parent_is_sanitized(database, tmp_path):
    OwnerRepository(database).initialize()
    path = tmp_path / "secret-marker" / "pair.code"
    assert_safe_failure(cli_pair(database, path))
    assert not path.parent.exists()


@pytest.mark.parametrize("ancestor", [False, True])
def test_owner_pair_rejects_link_targets_and_ancestors(database, tmp_path, ancestor):
    OwnerRepository(database).initialize()
    target = tmp_path / "target"
    target.mkdir()
    link = tmp_path / "link"
    if os.name == "nt":
        made = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(link), str(target)],
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
        )
        assert made.returncode == 0, made.stderr
    else:
        link.symlink_to(target, target_is_directory=True)
    path = link / "pair.code" if ancestor else link
    try:
        assert_safe_failure(cli_pair(database, path))
        assert list(target.iterdir()) == []
    finally:
        if os.name == "nt":
            link.rmdir()
        else:
            link.unlink()


def test_serve_owner_disables_proxy_trust_access_log_and_validates_configuration(
    monkeypatch, database_url, capsys
):
    from dragon_radar.cli import main

    calls = []
    monkeypatch.setenv("DRAGON_DATABASE_URL", database_url)
    monkeypatch.setattr(
        "uvicorn.run", lambda app, **kwargs: calls.append((app, kwargs))
    )
    assert main(["serve-owner", "--origin", ORIGIN]) == 0
    app, options = calls[0]
    assert app.state.owner_origin == ORIGIN
    assert options == {
        "host": "0.0.0.0",
        "port": 8000,
        "proxy_headers": False,
        "access_log": False,
    }
    for args in (
        ["--port", "0"],
        ["--port", "65536"],
        ["--origin", "https://evil.test"],
    ):
        assert main(["serve-owner", *args]) == 1
    assert len(calls) == 1
    output = capsys.readouterr()
    assert "Traceback" not in output.err


def test_owner_cli_missing_db_and_storage_failure_sanitized(database_url):
    for command in ("owner-init", "owner-revoke"):
        assert_safe_failure(run_cli([command]))
        assert_safe_failure(run_cli([command], "secret-marker"))
        assert_safe_failure(run_cli([command], database_url))


def test_pair_file_flush_failure_revokes_and_removes_plaintext(
    database, tmp_path, monkeypatch, capsys
):
    from dragon_radar.cli import main

    owners = OwnerRepository(database)
    owners.initialize()
    issued = owners.exchange_pairing(owners.issue_pairing(ORIGIN), ORIGIN)
    monkeypatch.setenv("DRAGON_DATABASE_URL", url(database))

    def fail(fd):
        raise OSError("secret-marker")

    monkeypatch.setattr(os, "fsync", fail)
    path = tmp_path / "pair.code"
    assert main(["owner-pair", "--output", str(path)]) == 1
    assert not path.exists()
    with database.engine.connect() as connection:
        assert (
            connection.scalar(
                select(func.count())
                .select_from(owner_pairings)
                .where(
                    owner_pairings.c.revoked_at.is_(None),
                    owner_pairings.c.consumed_at.is_(None),
                )
            )
            == 0
        )
    with pytest.raises(SessionUnauthorized):
        owners.authenticate(issued.token)
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "secret-marker" not in captured.err


def test_cli_pairing_expiry_uses_original_aware_clock(database, tmp_path):
    from datetime import timedelta, timezone

    from dragon_radar.owner.cli import issue_pairing_file

    now = datetime.now(timezone(timedelta(hours=8)))

    def clock():
        return now

    owners = OwnerRepository(database, clock=clock)
    owners.initialize()
    path = tmp_path / "pair.code"
    result = issue_pairing_file(owners, ORIGIN, path)
    assert owners.clock is clock
    assert datetime.fromisoformat(result["expires_at"]) == now + timedelta(minutes=5)
    assert owners.exchange_pairing(path.read_text().strip(), ORIGIN)
    owners.clock = lambda: datetime(2026, 9, 11)
    with pytest.raises(ValueError, match="aware datetime"):
        issue_pairing_file(owners, ORIGIN, tmp_path / "naive.code")
    assert not (tmp_path / "naive.code").exists()


def test_pair_refuses_hardlink_without_changing_source(database, tmp_path):
    OwnerRepository(database).initialize()
    source = tmp_path / "source"
    source.write_text("existing")
    target = tmp_path / "pair.code"
    os.link(source, target)
    assert_safe_failure(cli_pair(database, target))
    assert source.read_text() == "existing"
    assert target.read_text() == "existing"
