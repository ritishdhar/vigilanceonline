"""Judge workflows through the real HTTP handler and frame pump."""

import json
import threading
import time
import urllib.error
import urllib.request
from contextlib import contextmanager

import cv2
import numpy as np
import pytest

from config import VigilanceConfig
from core.browser_camera import BrowserCamera, decode_frame
from core import demo_catalog as catalog_module
from core.demo_catalog import demo_catalog
from core.demo import ScriptedDetector
from core.http_server import BoundedHTTPServer, make_handler
from core.pipeline import Pipeline
from ui import LiveState, pump_frames


@contextmanager
def service(mode="model"):
    state = LiveState(inference_mode=mode, judge_mode=True, video_plays=1)
    cfg = VigilanceConfig()
    pipe = Pipeline(cfg, detector=ScriptedDetector([[]]))
    server = BoundedHTTPServer(("127.0.0.1", 0), make_handler(state))
    pump = threading.Thread(
        target=pump_frames, args=(state, pipe, "idle", 30), daemon=True
    )
    http = threading.Thread(target=server.serve_forever, daemon=True)
    pump.start()
    http.start()
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def request(path, body=None, token=None, method=None, origin=None):
        headers = {"X-Vigilance-Token": state.token}
        if origin:
            headers["Origin"] = origin
        if token:
            headers["X-Vigilance-Camera"] = token
        if isinstance(body, dict):
            body = json.dumps(body).encode()
            headers["Content-Type"] = "application/json"
        elif body is not None:
            headers["Content-Type"] = "image/jpeg"
        req = urllib.request.Request(
            f"http://127.0.0.1:{server.server_port}{path}",
            data=body,
            headers=headers,
            method=method,
        )
        try:
            response = opener.open(req, timeout=5)
        except urllib.error.HTTPError as error:
            response = error
        with response:
            data = response.read()
            if response.headers.get_content_type() == "application/json":
                data = json.loads(data)
            return response.status, data, response.headers

    try:
        yield state, request
    finally:
        state.stop = True
        server.shutdown()
        server.server_close()
        pump.join(3)
        http.join(3)
        state.uploads.close()


def eventually(predicate):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.02)
    assert predicate()


def jpeg(width=96, height=64):
    return cv2.imencode(".jpg", np.zeros((height, width, 3), dtype=np.uint8))[
        1
    ].tobytes()


def test_webcam_frame_is_processed_and_stop_releases_source():
    with service() as (state, request):
        code, start, _ = request("/api/webcam/start", {})
        assert code == 200
        token = start["camera_token"]
        assert request("/api/webcam/frame", jpeg(), token=token)[0] == 200
        eventually(
            lambda: (
                state.status.get("running")
                and state.status.get("source") == "browser-webcam"
            )
        )
        assert state.jpeg.startswith(b"\xff\xd8")
        assert state.status["source_generation"] == start["source_generation"]
        assert request("/api/webcam/stop", {}, token=token)[0] == 200
        eventually(lambda: state.active_source == "idle")
        assert not state.status["running"]
        assert state.browser_camera is None
        assert request("/api/webcam/frame", jpeg(), token=token)[0] == 409


def test_stale_camera_cannot_replace_or_stop_new_source():
    with service() as (state, request):
        _, first, _ = request("/api/webcam/start", {})
        _, second, _ = request("/api/webcam/start", {})
        assert request("/api/webcam/stop", {}, token=first["camera_token"])[0] == 409
        assert state.browser_camera.token == second["camera_token"]
        assert request("/api/webcam/frame", jpeg(), token="wrong")[0] == 409


def test_camera_mailbox_keeps_only_latest_frame():
    camera = BrowserCamera(1)
    camera.put("one")
    camera.put("two")
    assert camera.take()[0] == "two"
    assert camera.take() is None


@pytest.mark.parametrize("data", [b"not an image", jpeg(1281, 64), jpeg(64, 721)])
def test_rejects_invalid_and_oversized_camera_images(data):
    with pytest.raises((ValueError, OSError)):
        decode_frame(data)


def test_sample_catalog_download_and_run_are_real_and_scoped(tmp_path, monkeypatch):
    # Exercise actual decoding without depending on a release sample asset.
    monkeypatch.setattr(catalog_module, "ROOT", tmp_path)
    samples = tmp_path / "samples"
    samples.mkdir()
    writer = cv2.VideoWriter(
        str(samples / "test.avi"), cv2.VideoWriter_fourcc(*"MJPG"), 10, (96, 64)
    )
    assert writer.isOpened()
    try:
        for _ in range(20):
            writer.write(np.zeros((64, 96, 3), dtype=np.uint8))
    finally:
        writer.release()
    with service() as (state, request):
        code, capabilities, _ = request("/api/capabilities")
        assert code == 200 and capabilities["max_active_sources"] == 1
        assert capabilities["samples"] == demo_catalog()
        sample = capabilities["samples"][0]
        code, data, _ = request("/api/demos/download/" + sample["id"])
        assert code == 200 and len(data) == sample["bytes"]
        code, result, _ = request("/api/demo", {"sample": sample["id"]})
        assert code == 200 and result["source_generation"] == 1
        eventually(lambda: state.status.get("frame", -1) >= 0)
        assert request("/api/demo", {"sample": "../config.py"})[0] == 404
        assert request("/api/demos/download/..%2Fconfig.py")[0] == 404
        assert request("/api/demo", {"sample": []})[0] == 404


def test_configured_camera_ids_only_select_advertised_streams():
    with service() as (state, request):
        state.allowed_sources = {
            "private-local.mp4",
            "rtsp://user:password@camera/live",
        }
        streams = request("/api/capabilities")[1]["streams"]
        assert len(streams) == 1 and "password" not in streams[0]["label"]
        for invalid in ["-1", "0", [], True, 1, "999"]:
            assert request("/api/source", {"stream_id": invalid})[0] == 400
        # Capture the requested value without contacting a physical camera.
        selected = []
        state.request_source = lambda source: selected.append(source) or 1
        assert request("/api/source", {"stream_id": streams[0]["id"]})[0] == 200
        assert selected == ["rtsp://user:password@camera/live"]


def test_synthetic_mode_does_not_claim_real_source_support():
    with service("synthetic") as (_, request):
        assert not request("/api/capabilities")[1]["browser_webcam"]
        assert request("/api/webcam/start", {})[0] == 409
        assert request("/api/demo", {"sample": "test1.mp4"})[0] == 409


def test_separate_frontend_can_preflight_webcam_upload():
    with service() as (state, request):
        state.allowed_origins = {"https://judges.example"}
        code, _, headers = request(
            "/api/webcam/frame", method="OPTIONS", origin="https://judges.example"
        )
        assert code == 204
        assert headers["Access-Control-Allow-Origin"] == "https://judges.example"
        assert "X-Vigilance-Camera" in headers["Access-Control-Allow-Headers"]
        assert (
            request("/api/webcam/start", {}, origin="https://other.example")[0] == 403
        )
