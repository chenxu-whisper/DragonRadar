"""Offline transport contracts; no supplier access."""

from datetime import UTC, datetime, timedelta
from urllib.error import HTTPError

import pytest

from dragon_radar.domain.models import SourceGrant
from dragon_radar.ingestion import supplier_transport as transport

NOW = datetime(2026, 11, 16, tzinfo=UTC)
ENDPOINT = "https://quantapi.51ifind.com/api/v1/cmd_history"


def grant():
    return SourceGrant(
        source_id="ifind",
        approved=True,
        reference="offline-test",
        starts_at=NOW - timedelta(days=1),
        expires_at=NOW + timedelta(days=1),
        local_storage=True,
    )


def test_transport_exists():
    assert hasattr(transport, "IfindTransport")


@pytest.mark.parametrize(
    "endpoint",
    [
        "http://quantapi.51ifind.com/api",
        "https://evil.test/api",
        "https://user:secret@quantapi.51ifind.com/api",
        "https://quantapi.51ifind.com/api?token=secret",
        "https://quantapi.51ifind.com:444/api",
        "https://quantapi.51ifind.com/api#secret",
    ],
)
def test_rejects_unsafe_endpoint(endpoint):
    with pytest.raises(transport.SupplierError, match="invalid_endpoint"):
        transport.IfindTransport(endpoint)


def test_post_uses_env_token_and_actual_receipt(monkeypatch):
    monkeypatch.setenv("IFIND_ACCESS_TOKEN", "PRIVATE_TEST_TOKEN")
    calls = []

    def send(request, timeout, maximum):
        calls.append((request, timeout, maximum))
        return transport.HttpResponse(200, b'{"errorcode":0,"tables":[]}')

    client = transport.IfindTransport(ENDPOINT, send=send)
    response = client.post(
        {"codes": ["explicit"]}, grant=grant(), source_id="ifind", clock=lambda: NOW
    )
    assert response.first_received_at == NOW
    assert calls[0][0].get_method() == "POST"
    assert calls[0][0].get_header("Access_token") == "PRIVATE_TEST_TOKEN"
    assert b"PRIVATE_TEST_TOKEN" not in calls[0][0].data
    assert "PRIVATE_TEST_TOKEN" not in repr(client) + repr(response)


@pytest.mark.parametrize(
    ("status", "body", "code", "retryable"),
    [
        (429, b"PRIVATE_TEST_TOKEN", "rate_limited", True),
        (503, b"PRIVATE_TEST_TOKEN", "server_error", True),
        (401, b"PRIVATE_TEST_TOKEN", "authentication_failed", False),
        (302, b"PRIVATE_TEST_TOKEN", "redirect_rejected", False),
        (200, b"not json PRIVATE_TEST_TOKEN", "invalid_response", False),
        (200, b'{"errorcode":9,"message":"secret"}', "invalid_response", False),
        (200, b'{"access_token":"different-secret"}', "invalid_response", False),
        (200, b'{"x":1,"x":2}', "invalid_response", False),
    ],
)
def test_response_errors_are_static(monkeypatch, status, body, code, retryable):
    monkeypatch.setenv("IFIND_ACCESS_TOKEN", "PRIVATE_TEST_TOKEN")
    client = transport.IfindTransport(
        ENDPOINT, send=lambda *_: transport.HttpResponse(status, body)
    )
    with pytest.raises(transport.SupplierError) as caught:
        client.post({}, grant=grant(), source_id="ifind", clock=lambda: NOW)
    assert str(caught.value) == code
    assert caught.value.retryable is retryable
    assert caught.value.__suppress_context__


def test_denied_grant_never_sends(monkeypatch):
    monkeypatch.setenv("IFIND_ACCESS_TOKEN", "PRIVATE_TEST_TOKEN")
    calls = []
    client = transport.IfindTransport(ENDPOINT, send=lambda *_: calls.append(1))
    with pytest.raises(transport.SupplierError, match="storage_denied"):
        client.post({}, grant=SourceGrant(source_id="ifind"), source_id="ifind")
    assert not calls


def test_timeout_safe_and_retryable(monkeypatch):
    monkeypatch.setenv("IFIND_ACCESS_TOKEN", "PRIVATE_TEST_TOKEN")

    def send(*_):
        raise TimeoutError("PRIVATE_TEST_TOKEN")

    with pytest.raises(transport.SupplierError, match="timeout") as caught:
        transport.IfindTransport(ENDPOINT, send=send).post(
            {}, grant=grant(), source_id="ifind", clock=lambda: NOW
        )
    assert caught.value.retryable


def test_stdlib_sender_rejects_redirect_and_reads_bounded(monkeypatch):
    captured = []

    class Response:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def read(self, maximum):
            captured.append(maximum)
            return b'{"x":1}'

    class Opener:
        def open(self, request, timeout):
            captured.append((request.full_url, timeout))
            return Response()

    def build(*handlers):
        redirect = next(
            item for item in handlers if isinstance(item, transport.NoRedirect)
        )
        with pytest.raises(HTTPError):
            redirect.redirect_request(None, None, 302, "", {}, "https://evil.test")
        return Opener()

    monkeypatch.setattr(transport, "build_opener", build)
    result = transport.https_send(transport.Request(ENDPOINT, data=b"{}"), 2, 20)
    assert result.body == b'{"x":1}'
    assert captured == [(ENDPOINT, 2), 21]


@pytest.mark.parametrize("kwargs", [{"timeout": 0}, {"max_response_bytes": 0}])
def test_bounds(kwargs):
    with pytest.raises(transport.SupplierError):
        transport.IfindTransport(ENDPOINT, **kwargs)


def test_oversize_response_is_not_retained(monkeypatch):
    monkeypatch.setenv("IFIND_ACCESS_TOKEN", "PRIVATE_TEST_TOKEN")
    with pytest.raises(transport.SupplierError, match="invalid_response"):
        transport.IfindTransport(
            ENDPOINT,
            max_response_bytes=2,
            send=lambda *_: transport.HttpResponse(200, b'{"x":1}'),
        ).post({}, grant=grant(), source_id="ifind", clock=lambda: NOW)


def test_missing_credentials_never_sends(monkeypatch):
    monkeypatch.delenv("IFIND_ACCESS_TOKEN", raising=False)
    calls = []
    with pytest.raises(transport.SupplierError, match="missing_credentials"):
        transport.IfindTransport(
            ENDPOINT,
            send=lambda *_: calls.append(1),
        ).post({}, grant=grant(), source_id="ifind", clock=lambda: NOW)
    assert not calls


def test_escaped_response_token_is_rejected(monkeypatch):
    monkeypatch.setenv("IFIND_ACCESS_TOKEN", "PRIVATE_TEST_TOKEN")
    with pytest.raises(transport.SupplierError, match="invalid_response"):
        transport.IfindTransport(
            ENDPOINT,
            send=lambda *_: transport.HttpResponse(
                200,
                b'{"message":"\\u0050RIVATE_TEST_TOKEN"}',
            ),
        ).post({}, grant=grant(), source_id="ifind", clock=lambda: NOW)


def test_bad_token_environment_name_has_static_error():
    with pytest.raises(transport.SupplierError, match="invalid_limits"):
        transport.IfindTransport(ENDPOINT, token_env=123)


def test_escaped_token_in_decoded_payload_key_is_rejected():
    with pytest.raises(transport.SupplierError, match="invalid_request"):
        transport.safe_payload({"rows": [{'abc"xyz': 1}]}, token='abc"xyz')


def test_escaped_token_in_response_key_is_not_returned(monkeypatch):
    monkeypatch.setenv("IFIND_ACCESS_TOKEN", 'abc"xyz')
    with pytest.raises(transport.SupplierError, match="invalid_response") as caught:
        transport.IfindTransport(
            ENDPOINT,
            send=lambda *_: transport.HttpResponse(200, b'{"rows":[{"abc\\"xyz":1}]}'),
        ).post({}, grant=grant(), source_id="ifind", clock=lambda: NOW)
    assert str(caught.value) == "invalid_response"
