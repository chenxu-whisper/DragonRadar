"""Disposable Task20 lifecycle. Only sanitized facts survive secret cleanup."""

from __future__ import annotations

import json
import os
import re
import secrets
import socket
import stat
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
STATE = ROOT / ".temp/acceptance-stack"
ENV_FILE = STATE / "compose.env"
OVERRIDE = STATE / "compose.override.yaml"
CA_FILE = STATE / "caddy-root.crt"
CLEANUP_ARCHIVE = ROOT / ".temp/acceptance/acceptance-stack-cleanup.json"
RUN_LABEL = "dragon-radar.acceptance-run"
PROJECT_LABEL = "com.docker.compose.project"
BACKENDS = ("public-api", "owner-api", "analysis-worker", "ai-worker")
ALLOWED = {
    "DRAGON_TEST_DATABASE_URL",
    "DRAGON_TOPIC_CURSOR_SECRET",
    "DRAGON_REALTIME_CURSOR_SECRET",
    "DRAGON_TASK20_PERF_ISOLATION",
}


class Failure(Exception):
    def __init__(self, message: str, code: int = 2):
        super().__init__(message)
        self.code = code


def protected(path: Path, directory: bool = False) -> None:
    try:
        info = path.lstat()
    except OSError as error:
        raise Failure("Missing protected acceptance state") from error
    kind = stat.S_ISDIR if directory else stat.S_ISREG
    if (
        not kind(info.st_mode)
        or info.st_uid != os.getuid()
        or stat.S_IMODE(info.st_mode) != (0o700 if directory else 0o600)
    ):
        raise Failure("Malformed or unprotected acceptance state")


def fields(project: str, password: str, port: str) -> dict[str, str]:
    return {
        "DRAGON_DB_PASSWORD": password,
        "DRAGON_PRIVATE_PORT": port,
        "DRAGON_PRIVATE_PUBLISHED_PORT": port,
        "DRAGON_OWNER_PORT": port,
        "DRAGON_PUBLIC_PORT": "0",
        "DRAGON_PUBLIC_BIND": "127.0.0.1",
        "DRAGON_OWNER_SECRETS_DIR": str(STATE / "owner-secrets"),
        "COMPOSE_PROJECT_NAME": project,
        "DRAGON_AI_PROVIDER": "",
        "DRAGON_AI_TOKEN": "",
        "DRAGON_AI_MODEL_VERSION": "",
    }


def load_state() -> dict[str, str]:
    protected(STATE, True)
    protected(ENV_FILE)
    data: dict[str, str] = {}
    for line in ENV_FILE.read_text().splitlines():
        key, separator, value = line.partition("=")
        if not separator or key in data:
            raise Failure("Malformed acceptance state")
        data[key] = value
    project = data.get("COMPOSE_PROJECT_NAME", "")
    password = data.get("DRAGON_DB_PASSWORD", "")
    port = data.get("DRAGON_OWNER_PORT", "")
    if (
        not re.fullmatch(r"dragon-radar-task20-[a-f0-9]{24}", project)
        or not re.fullmatch(r"[a-f0-9]{64}", password)
        or not port.isdecimal()
        or not 1024 <= int(port) <= 65535
        or data != fields(project, password, port)
    ):
        raise Failure("Malformed acceptance identity or database state")
    protected(OVERRIDE)
    if OVERRIDE.read_text() != override_text(data):
        raise Failure("Acceptance override differs from protected identity")
    protected(STATE / "owner-secrets", True)
    return data


def exports(data: dict[str, str]) -> dict[str, str]:
    return {
        key: data[key]
        for key in (
            "DRAGON_DB_PASSWORD",
            "DRAGON_PRIVATE_PORT",
            "DRAGON_PRIVATE_PUBLISHED_PORT",
            "DRAGON_OWNER_PORT",
        )
    } | {
        "DRAGON_TASK20_COMPOSE_PROJECT": data["COMPOSE_PROJECT_NAME"],
        "DRAGON_TEST_PRIVATE_CA": str(CA_FILE),
    }


def validate_environment(data: dict[str, str] | None) -> None:
    matching = exports(data) | data if data else {}
    for key, value in os.environ.items():
        if key in ALLOWED:
            if key == "DRAGON_TEST_DATABASE_URL":
                parsed = urlsplit(value)
                if (
                    parsed.scheme != "postgresql+psycopg"
                    or parsed.hostname not in {"localhost", "127.0.0.1"}
                    or parsed.path != "/dragon_test"
                    or not parsed.username
                    or not parsed.password
                ):
                    raise Failure("Ordinary pytest requires separate local dragon_test")
            elif key.endswith("CURSOR_SECRET") and not value.startswith("synthetic-"):
                raise Failure("Only synthetic cursor secrets are accepted")
            elif key == "DRAGON_TASK20_PERF_ISOLATION" and value != "1":
                raise Failure("Invalid performance isolation flag")
        elif key.startswith(
            ("DRAGON_", "DAILY", "COMPOSE_", "AI_", "POSTGRES_")
        ) or key in {"DATABASE_URL", "DB_PASSWORD"}:
            if key not in matching or value != matching[key]:
                raise Failure(f"Refusing inherited configuration: {key}")


def override_text(data: dict[str, str]) -> str:
    # C1 semantics: dedicated database, all four backend URLs, blank AI provider.
    label = json.dumps(data["COMPOSE_PROJECT_NAME"])
    lines = [
        "services:",
        "  db:",
        "    environment:",
        "      POSTGRES_DB: dragon_test",
        "    healthcheck:",
        '      test: ["CMD-SHELL", "pg_isready -U dragon_radar -d dragon_test"]',
    ]
    for service in BACKENDS:
        lines += [
            f"  {service}:",
            "    environment:",
            '      DRAGON_DATABASE_URL: "postgresql+psycopg://dragon_radar:${DRAGON_DB_PASSWORD}@db:5432/dragon_test"',
        ]
        if service == "ai-worker":
            lines += [
                '      DRAGON_AI_PROVIDER: ""',
                '      DRAGON_AI_TOKEN: ""',
                '      DRAGON_AI_MODEL_VERSION: ""',
            ]
    # Compose assigns project labels too; this second label proves run ownership.
    for service in ("db", *BACKENDS, "public-caddy", "owner-caddy"):
        # Separate service label mapping is merged below, not duplicate YAML keys.
        index = lines.index(f"  {service}:") if f"  {service}:" in lines else len(lines)
        if index == len(lines):
            lines += [f"  {service}:"]
        lines[index + 1 : index + 1] = ["    labels:", f"      {RUN_LABEL}: {label}"]
    for section, names in {
        "volumes": (
            "reset_postgres_data",
            "reset_owner_caddy_data",
            "reset_owner_caddy_config",
        ),
        "networks": (
            "public-data",
            "public-edge",
            "owner-data",
            "owner-edge",
            "worker-data",
        ),
    }.items():
        lines.append(f"{section}:")
        for name in names:
            lines += [f"  {name}:", "    labels:", f"      {RUN_LABEL}: {label}"]
    return "\n".join(lines) + "\n"


def write_state() -> dict[str, str]:
    if STATE.exists() or STATE.is_symlink():
        raise Failure("Existing acceptance directory requires explicit teardown")
    os.umask(0o077)
    STATE.mkdir(parents=True, mode=0o700)
    (STATE / "owner-secrets").mkdir(mode=0o700)
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = str(probe.getsockname()[1])
    data = fields(
        "dragon-radar-task20-" + secrets.token_hex(12), secrets.token_hex(32), port
    )
    ENV_FILE.write_text("".join(f"{key}={value}\n" for key, value in data.items()))
    OVERRIDE.write_text(override_text(data))
    return load_state()


class Stack:
    def __init__(self, data: dict[str, str]):
        self.data = data
        self.project = data["COMPOSE_PROJECT_NAME"]
        self.environment = {
            key: value
            for key, value in os.environ.items()
            if not key.startswith(("DRAGON_", "DAILY", "COMPOSE_", "AI_", "POSTGRES_"))
            and key not in {"DATABASE_URL", "DB_PASSWORD"}
        }

    def command(self, *argv: str, timeout: int = 600) -> subprocess.CompletedProcess:
        try:
            return subprocess.run(
                argv,
                cwd=ROOT,
                env=self.environment,
                capture_output=True,
                timeout=timeout,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            raise Failure("Acceptance subprocess failed to execute", 124) from error

    def compose(self, *args: str) -> subprocess.CompletedProcess:
        result = self.command(
            "docker",
            "compose",
            "--project-name",
            self.project,
            "--project-directory",
            str(ROOT),
            "--env-file",
            str(ENV_FILE),
            "-f",
            str(ROOT / "compose.yaml"),
            "-f",
            str(OVERRIDE),
            *args,
        )
        if result.returncode:
            # Docker config/build stderr can include credentials. Never archive it.
            raise Failure(f"Compose {args[0]} failed", result.returncode)
        return result

    def validate_config(self) -> None:
        try:
            config = json.loads(self.compose("config", "--format", "json").stdout)
            services = config["services"]
            db = services["db"]
            assert db["environment"]["POSTGRES_DB"] == "dragon_test"
            assert db["environment"]["POSTGRES_USER"] == "dragon_radar"
            assert (
                db["environment"]["POSTGRES_PASSWORD"]
                == self.data["DRAGON_DB_PASSWORD"]
            )
            assert db["healthcheck"]["test"] == [
                "CMD-SHELL",
                "pg_isready -U dragon_radar -d dragon_test",
            ]
            expected = f"postgresql+psycopg://dragon_radar:{self.data['DRAGON_DB_PASSWORD']}@db:5432/dragon_test"
            for name in BACKENDS:
                assert services[name]["environment"]["DRAGON_DATABASE_URL"] == expected
            authority = "localhost:" + self.data["DRAGON_OWNER_PORT"]
            assert (
                services["owner-caddy"]["environment"]["DRAGON_OWNER_AUTHORITY"]
                == authority
            )
            assert (
                services["owner-api"]["environment"]["DRAGON_OWNER_AUTHORITY"]
                == authority
            )
            command = services["owner-api"]["command"]
            assert command[command.index("--origin") + 1] == "https://" + authority
            owner_ports = services["owner-caddy"]["ports"]
            assert len(owner_ports) == 1
            assert str(owner_ports[0]["published"]) == self.data["DRAGON_OWNER_PORT"]
            assert owner_ports[0]["target"] == 8443
            for service in services.values():
                assert service["labels"][RUN_LABEL] == self.project
                for key, value in service.get("environment", {}).items():
                    if key in {
                        "DRAGON_AI_TOKEN",
                        "DRAGON_AI_PROVIDER",
                        "DRAGON_AI_MODEL_VERSION",
                    }:
                        assert value in ("", None)
                for port in service.get("ports", []):
                    assert port["host_ip"] == "127.0.0.1"
            for section in ("networks", "volumes"):
                for resource in config.get(section, {}).values():
                    assert resource["labels"][RUN_LABEL] == self.project
        except (
            KeyError,
            ValueError,
            TypeError,
            IndexError,
            AttributeError,
            AssertionError,
        ) as error:
            raise Failure(
                "Unsafe or incomplete Compose database/port/identity mapping"
            ) from error
        (STATE / "config-facts.json").write_text(
            json.dumps(
                {
                    "project": self.project,
                    "database": "dragon_test",
                    "backends": list(BACKENDS),
                    "ownerAuthority": authority,
                    "ownerOrigin": "https://" + authority,
                    "publishedPortsLoopback": True,
                    "aiCredentialsEmpty": True,
                },
                indent=2,
            )
            + "\n"
        )

    def publish_exports(self) -> None:
        github_env = os.environ.get("GITHUB_ENV")
        if github_env:
            print("::add-mask::" + self.data["DRAGON_DB_PASSWORD"], flush=True)
            with Path(github_env).open("a") as output:
                for key, value in exports(self.data).items():
                    output.write(f"{key}={value}\n")

    def start(self) -> None:
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", int(self.data["DRAGON_OWNER_PORT"])))
        self.compose("up", "--detach", "--wait", "db")
        self.compose("run", "--rm", "--no-deps", "analysis-worker", "migrate")
        self.compose("up", "--detach", "owner-caddy")
        self.compose("run", "--rm", "owner-api", "owner-init")
        self.compose("up", "--detach", "--wait")
        certificate = self.compose(
            "exec",
            "-T",
            "owner-caddy",
            "cat",
            "/data/caddy/pki/authorities/local/root.crt",
        ).stdout
        CA_FILE.write_bytes(certificate)
        for argv in (
            ("openssl", "x509", "-in", str(CA_FILE), "-noout", "-checkend", "0"),
            ("openssl", "verify", "-CAfile", str(CA_FILE), str(CA_FILE)),
            (
                "curl",
                "--fail",
                "--silent",
                "--show-error",
                "--cacert",
                str(CA_FILE),
                "--resolve",
                f"localhost:{self.data['DRAGON_OWNER_PORT']}:127.0.0.1",
                f"https://localhost:{self.data['DRAGON_OWNER_PORT']}/health/ready",
            ),
        ):
            result = self.command(*argv, timeout=30)
            if result.returncode:
                raise Failure("Strict local TLS verification failed", result.returncode)

    def cleanup(self, primary: int = 0) -> int:
        errors: list[dict] = []
        actions: list[dict] = []
        residuals: list[dict] = []
        owned: list[tuple[str, str]] = []
        safe_down = True

        def attempt(label: str, argv: tuple[str, ...]) -> bytes | None:
            try:
                result = self.command(*argv, timeout=120)
                actions.append({"action": label, "exit": result.returncode})
                if result.returncode:
                    errors.append({"action": label, "exit": result.returncode})
                    return None
                return result.stdout
            except Failure as error:
                errors.append({"action": label, "exit": error.code})
                return None

        def inventory(kind: str) -> list[str] | None:
            prefix = (
                ("docker", "ps", "-aq")
                if kind == "container"
                else ("docker", kind, "ls", "-q")
            )
            raw = attempt(
                f"list-{kind}",
                (*prefix, "--filter", f"label={PROJECT_LABEL}={self.project}"),
            )
            if raw is None:
                return None
            names = raw.decode().split()
            if any(not re.fullmatch(r"[a-zA-Z0-9_.-]+", name) for name in names):
                errors.append({"action": f"invalid-{kind}-identity", "exit": 2})
                return None
            return names

        for kind in ("container", "volume", "network"):
            names = inventory(kind)
            if names is None:
                safe_down = False
                continue
            for name in names:
                raw = attempt(f"inspect-{kind}", ("docker", kind, "inspect", name))
                try:
                    item = json.loads(raw)[0] if raw is not None else {}
                    labels = (
                        item.get("Config", {}).get("Labels", {})
                        if kind == "container"
                        else item.get("Labels", {})
                    )
                    assert labels.get(PROJECT_LABEL) == self.project
                    assert labels.get(RUN_LABEL) == self.project
                except (
                    KeyError,
                    ValueError,
                    TypeError,
                    IndexError,
                    AttributeError,
                    AssertionError,
                ):
                    safe_down = False
                    errors.append({"action": f"unknown-{kind}", "exit": 2})
                    residuals.append({"kind": kind, "id": name, "owned": False})
                    continue
                owned.append((kind, name))
        if safe_down:
            try:
                self.compose("down", "--volumes", "--remove-orphans")
                actions.append({"action": "compose-down", "exit": 0})
            except Failure as error:
                errors.append({"action": "compose-down", "exit": error.code})
        # Independently attempt all proven resources, even after a down failure.
        for kind, name in owned:
            remaining = inventory(kind)
            if remaining is None or name in remaining:
                argv = (
                    ("docker", "rm", "-f", "-v", name)
                    if kind == "container"
                    else ("docker", kind, "rm", name)
                )
                attempt(f"remove-{kind}", argv)
        for kind in ("container", "volume", "network"):
            names = inventory(kind)
            if names is None:
                errors.append({"action": f"residual-{kind}-unverified", "exit": 2})
            else:
                for name in names:
                    if not any(
                        item["kind"] == kind and item["id"] == name
                        for item in residuals
                    ):
                        residuals.append(
                            {"kind": kind, "id": name, "owned": (kind, name) in owned}
                        )
        if residuals:
            errors.append({"action": "residual-resources", "exit": 2})
        # Only known run-created files are removed. Unknown paths remain evidence.
        for path in (CA_FILE, ENV_FILE, OVERRIDE):
            try:
                if path.exists() or path.is_symlink():
                    protected(path)
                    path.unlink()
            except (Failure, OSError):
                errors.append({"action": "remove-owned-config", "exit": 2})
        try:
            protected(STATE / "owner-secrets", True)
            (STATE / "owner-secrets").rmdir()
        except (Failure, OSError):
            errors.append({"action": "remove-owned-secrets", "exit": 2})
        receipt = {
            "project": self.project,
            "primaryExit": primary,
            "actions": actions,
            "errors": errors,
            "residuals": residuals,
            "secretsRemoved": not ENV_FILE.exists()
            and not (STATE / "owner-secrets").exists(),
        }
        try:
            content = json.dumps(receipt, indent=2) + "\n"
            for path in (STATE / "cleanup.json", CLEANUP_ARCHIVE):
                if path.is_symlink() or path.parent.is_symlink():
                    raise OSError("Linked cleanup evidence is forbidden")
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(content)
        except OSError:
            print("Unable to retain sanitized cleanup receipt", file=sys.stderr)
            return 1
        return 1 if errors else 0


def main() -> int:
    os.umask(0o077)
    arguments = sys.argv[1:]
    if arguments not in (["build"], ["setup"], ["setup", "--skip-build"], ["teardown"]):
        raise Failure("usage: acceptance-stack.sh build|setup [--skip-build]|teardown")
    reuse = arguments in (["setup", "--skip-build"], ["teardown"])
    data = load_state() if reuse else None
    validate_environment(data)
    stack = Stack(data if data else write_state())
    if arguments == ["teardown"]:
        return stack.cleanup()
    try:
        stack.validate_config()
        if not reuse:
            stack.compose("build")
        if arguments[0] == "setup":
            stack.start()
        stack.publish_exports()
        return 0
    except (Failure, OSError) as error:
        primary = error.code if isinstance(error, Failure) else 2
        stack.cleanup(primary)
        print(
            f"Acceptance {arguments[0]} failed (exit {primary}); "
            "sanitized cleanup retained",
            file=sys.stderr,
        )
        return primary


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (Failure, OSError, ValueError) as error:
        print(
            str(error)
            if isinstance(error, Failure)
            else "Acceptance state validation failed",
            file=sys.stderr,
        )
        sys.exit(error.code if isinstance(error, Failure) else 2)
