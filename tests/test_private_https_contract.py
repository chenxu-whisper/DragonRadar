"""Narrow fake-transport regression, never opens a live gateway."""

import runpy
import ssl
from pathlib import Path
from types import SimpleNamespace

import pytest


@pytest.mark.parametrize(
    "path,host,expected",
    [
        ("/health/ready", "allowed", 200),
        ("/api/v1/snapshots", "allowed", 404),
        ("/api/owner/v1/session", "allowed", 401),
        ("/health/ready", "wrong-port", 403),
        ("/health/ready", "uppercase", 403),
        ("/", "evil.test", 403),
    ],
)
def test_allocated_authority_and_strict_tls(monkeypatch, path, host, expected):
    module = runpy.run_path(str(Path(__file__).with_name("test_private_https.py")))
    monkeypatch.setenv("DRAGON_OWNER_PORT", "49217")
    monkeypatch.setenv("DRAGON_TEST_PRIVATE_CA", "/run/local/ca.crt")
    context = SimpleNamespace(check_hostname=True, verify_mode=ssl.CERT_REQUIRED)
    fixture = module["tls_gateway"].__wrapped__
    fixture.__globals__["ssl"] = SimpleNamespace(
        create_default_context=lambda *, cafile: context,
        CERT_REQUIRED=ssl.CERT_REQUIRED,
    )
    gateway = fixture()
    captured = []

    class Response:
        status = expected
        headers = {"Cache-Control": "no-store"}

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    def urlopen(request, *, context, timeout):
        captured.append(request)
        assert context.check_hostname and context.verify_mode == ssl.CERT_REQUIRED
        assert timeout == 10
        return Response()

    test = module["test_live_gateway_rejects_unpaired_and_wrong_authority"]
    test.__globals__["urlopen"] = urlopen
    test(gateway, path, host, expected)
    assert captured[0].full_url == f"https://localhost:49217{path}"
    assert captured[0].headers["Host"] == {
        "allowed": "localhost:49217",
        "wrong-port": "localhost:49218",
        "uppercase": "LOCALHOST:49217",
    }.get(host, host)
