"""Opt-in, credential-free checks against a running local TLS gateway."""

import os
import ssl
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest


@pytest.fixture
def tls_gateway():
    certificate = os.environ.get("DRAGON_TEST_PRIVATE_CA")
    if not certificate:
        pytest.skip("DRAGON_TEST_PRIVATE_CA is required for live TLS gateway checks")
    context = ssl.create_default_context(cafile=certificate)
    assert context.check_hostname and context.verify_mode == ssl.CERT_REQUIRED
    return context


@pytest.mark.parametrize(
    "path,host,expected",
    [
        ("/health/ready", "localhost:8030", 200),
        # The private edge closes the legacy read API and the server-side
        # review surface (private review data is local-only); the static
        # Caddyfile contract and the production-isolation E2E pin these to an
        # edge-level 404 even with a paired session, so unpaired callers must
        # be rejected fail-closed at the edge rather than by OwnerGate.
        ("/api/v1/snapshots", "localhost:8030", 404),
        ("/api/owner/v1/notes", "localhost:8030", 404),
        # Whitelisted routes still reach OwnerGate, which rejects unpaired
        # callers with 401: the app-layer defense behind the edge is exercised.
        ("/api/owner/v1/session", "localhost:8030", 401),
        ("/api/owner/v1/not-open", "localhost:8030", 404),
        ("/health/ready", "evil.test", 403),
        ("/", "evil.test", 403),
        ("/api/owner/v1/notes", "evil.test", 403),
        ("/health/ready", "localhost:8031", 403),
        ("/health/ready", "LOCALHOST:8030", 403),
    ],
)
def test_live_gateway_rejects_unpaired_and_wrong_authority(
    tls_gateway, path, host, expected
):
    request = Request("https://localhost:8030" + path, headers={"Host": host})
    try:
        response = urlopen(request, context=tls_gateway, timeout=10)
    except HTTPError as failure:
        response = failure
    with response:
        assert response.status == expected
        assert response.headers["Cache-Control"] == "no-store"
        assert response.headers.get("Access-Control-Allow-Origin") is None
        assert response.headers.get("Set-Cookie") is None
