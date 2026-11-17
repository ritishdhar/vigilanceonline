"""Real loopback requests enforce browser boundaries, lengths, and upload ownership."""

from http.client import HTTPConnection
import json
import socket
import threading

import pytest

from core.http_server import BoundedHTTPServer, make_handler
from ui import LiveState


@pytest.fixture
def server():
    state = LiveState()
    server = BoundedHTTPServer(("127.0.0.1", 0), make_handler(state))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server.server_address[1], state
    state.stop = True
    server.shutdown()
    server.server_close()
    thread.join(timeout=2)
    state.uploads.close()


def request(server, path, method="GET", data=None, headers=None, token=True):
    port, state = server
    actual = {"X-Vigilance-Token": state.token} if token else {}
    actual.update(headers or {})
    conn = HTTPConnection("127.0.0.1", port, timeout=3)
    conn.request(method, path, body=data, headers=actual)
    response = conn.getresponse()
    code, body, received = response.status, response.read(), dict(response.getheaders())
    conn.close()
    return code, body, received


def test_private_reads_require_session_but_bootstrap_issues_strict_cookie(server):
    assert request(server, "/api/status", token=False)[0] == 403
    code, html, headers = request(server, "/", token=False)
    assert code == 200 and b"__VIGILANCE_TOKEN__" not in html
    cookie = headers["Set-Cookie"]
    assert "HttpOnly" in cookie and "SameSite=Strict" in cookie
    assert (
        request(server, "/api/status", headers={"Cookie": cookie}, token=False)[0]
        == 200
    )


def test_foreign_origin_and_rebinding_host_are_rejected(server):
    assert request(server, "/", headers={"Host": "untrusted.example"})[0] == 403
    assert (
        request(
            server,
            "/api/source",
            "POST",
            b'{"source":"0"}',
            {"Content-Type": "application/json", "Origin": "https://untrusted.example"},
        )[0]
        == 403
    )


@pytest.mark.skipif(not socket.has_ipv6, reason="IPv6 not available")
def test_ipv6_loopback_supports_authenticated_requests():
    state = LiveState()
    server = BoundedHTTPServer(("::1", 0), make_handler(state))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    connection = HTTPConnection("::1", server.server_address[1], timeout=3)
    try:
        assert server.address_family == socket.AF_INET6
        connection.request(
            "GET",
            "/api/status",
            headers={
                "X-Vigilance-Token": state.token,
                "Origin": f"http://[::1]:{server.server_address[1]}",
            },
        )
        response = connection.getresponse()
        assert response.status == 200
        response.read()
    finally:
        connection.close()
        state.stop = True
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
        state.uploads.close()


def test_cookie_alone_cannot_authorize_mutation(server):
    _, _, headers = request(server, "/")
    assert (
        request(
            server,
            "/api/source",
            "POST",
            b'{"source":"0"}',
            {"Content-Type": "application/json", "Cookie": headers["Set-Cookie"]},
            token=False,
        )[0]
        == 403
    )


@pytest.mark.parametrize(
    "length,expected", [("-1", 400), ("garbage", 400), ("4097", 413)]
)
def test_invalid_json_lengths_are_rejected_without_reading(server, length, expected):
    assert (
        request(
            server,
            "/api/source",
            "POST",
            b"",
            {"Content-Type": "application/json", "Content-Length": length},
        )[0]
        == expected
    )


def test_source_requires_json_and_allowlist(server):
    assert (
        request(
            server,
            "/api/source",
            "POST",
            b'{"source":"0"}',
            {"Content-Type": "text/plain"},
        )[0]
        == 415
    )
    body = json.dumps({"source": "rtsp://attacker.local/feed"}).encode()
    assert (
        request(
            server, "/api/source", "POST", body, {"Content-Type": "application/json"}
        )[0]
        == 400
    )
    assert (
        request(
            server,
            "/api/source",
            "POST",
            b'{"source":"0"}',
            {"Content-Type": "application/json"},
        )[0]
        == 200
    )


def test_upload_cleanup_only_removes_application_owned_files(server, tmp_path):
    _, state = server
    outside = tmp_path / "keep.avi"
    outside.write_bytes(b"user data")
    state.uploads.release(outside)
    assert outside.exists()
    old = state.uploads.path / "owned.avi"
    old.write_bytes(b"owned")
    state.request_source(str(old))
    state.request_source("0")
    assert not old.exists()


def test_stream_concurrency_is_bounded(server):
    _, state = server
    for _ in range(4):
        assert state.stream_slots.acquire(blocking=False)
    try:
        assert request(server, "/stream.mjpg")[0] == 503
    finally:
        for _ in range(4):
            state.stream_slots.release()


def test_stalled_capture_is_not_reported_healthy(server):
    _, state = server
    with state.lock:
        state.status = {"running": True, "fight_status": "ASSESSED"}
        state.frame_updated_at = 0
    code, body, _ = request(server, "/api/status")
    status = json.loads(body)
    assert code == 200 and not status["running"]
    assert status["fight_status"] == "ERROR"
