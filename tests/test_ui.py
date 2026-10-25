"""UI endpoint tests: synthetic mode only, no camera or YOLO weights needed."""

import json
import threading
import time
import urllib.error
import urllib.request
import uuid

import numpy as np
import pytest

cv2 = pytest.importorskip("cv2")

from ui import LiveState, make_handler, open_capture, parse_args, pump_frames


class _FakeCap:
    def __init__(self, frame=None):
        self._frame = frame
        self.released = False

    def isOpened(self):
        return True

    def read(self):
        if self._frame is None:
            return False, None
        return True, self._frame

    def get(self, prop):
        return 30.0

    def release(self):
        self.released = True


def _patch_capture(monkeypatch, frames_by_backend):
    """Map backend -> frame (None = open-but-no-frames). Records attempts."""
    import cv2 as _cv2

    attempts = []

    def fake_capture(src, backend=None):
        attempts.append(backend)
        return _FakeCap(frames_by_backend.get(backend))

    monkeypatch.setattr(_cv2, "VideoCapture", fake_capture)
    return attempts


def test_open_capture_tries_dshow_fallback_for_webcam(monkeypatch):
    import cv2 as _cv2
    import numpy as np

    good = np.zeros((480, 640, 3), dtype=np.uint8)
    attempts = _patch_capture(monkeypatch, {None: None, _cv2.CAP_DSHOW: good})
    cap, backend = open_capture("0")
    assert cap is not None and backend == str(_cv2.CAP_DSHOW)
    assert attempts[0] is None  # default tried first
    # webcam probe frame is consumed once at open; stream continues after it
    ok, frame = cap.read()
    assert ok and frame is not None


def test_open_capture_single_attempt_for_files(monkeypatch):
    attempts = _patch_capture(monkeypatch, {})
    cap, error = open_capture("clip.mp4")
    assert cap is None and "tried backends: default" in error
    assert len(attempts) == 1


def test_open_capture_rejects_empty_frames(monkeypatch):
    import numpy as np

    attempts = _patch_capture(monkeypatch, {None: np.zeros((0, 0, 3), dtype=np.uint8)})
    cap, error = open_capture("0")
    assert cap is None and "close other apps" in error
    assert len(attempts) == 3  # default + DSHOW + MSMF, all bad


def test_dashboard_has_fullscreen_button(live_state):
    port, _ = live_state
    with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=5) as r:
        html = r.read().decode()
    assert "/assets/dashboard.js" in html and "cam-fullscreen-btn" in html


@pytest.fixture()
def live_state():
    from core.http_server import BoundedHTTPServer as ThreadingHTTPServer

    from config import VigilanceConfig
    from core.pipeline import Pipeline
    from tests.conftest import FakeDetector, black_frames

    cfg = VigilanceConfig()
    script = [[(1, 60.0), (2, 220.0)], [(1, 64.0), (2, 216.0)]]
    pipe = Pipeline(config=cfg, detector=FakeDetector(script))
    state = LiveState()
    pump = threading.Thread(
        target=pump_frames,
        args=(
            state,
            pipe,
            "synthetic",
            cfg.fps,
            black_frames(len(script), h=240, w=320),
        ),
        daemon=True,
    )
    pump.start()
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(state))
    port = server.server_address[1]
    old_opener = urllib.request._opener
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    opener.addheaders = [("X-Vigilance-Token", state.token)]
    urllib.request.install_opener(opener)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    for _ in range(100):
        with state.lock:
            ready = state.jpeg is not None and state.status.get("frame", 0) >= 1
        if ready:
            break
        time.sleep(0.05)
    yield port, state
    state.stop = True
    server.shutdown()
    server.server_close()
    pump.join(timeout=5)
    state.uploads.close()
    urllib.request.install_opener(old_opener)


def test_status_endpoint_shape(live_state):
    port, _ = live_state
    with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/status", timeout=5) as r:
        assert r.status == 200
        body = json.loads(r.read())
    for key in (
        "running",
        "frame",
        "fps",
        "people",
        "pairs",
        "scene",
        "fight_window_seconds",
        "model",
        "alerts",
        "alert_details",
        "threats",
        "threat_models",
        "timeline",
        "diagnostics",
    ):
        assert key in body, key
    assert body["running"] is True
    assert isinstance(body["people"], list) and len(body["people"]) >= 1
    assert set(body["model"]) >= {"ready", "head_trained", "checkpoint"}
    assert isinstance(body["alert_details"], dict)


def test_status_reports_inference_mode(live_state):
    port, state = live_state
    state.inference_mode = "synthetic"
    with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/status", timeout=5) as r:
        body = json.loads(r.read())
    assert body["inference_mode"] == "synthetic"


def test_snapshot_is_jpeg(live_state):
    port, _ = live_state
    with urllib.request.urlopen(
        f"http://127.0.0.1:{port}/snapshot.jpg", timeout=5
    ) as r:
        assert r.status == 200
        assert r.headers.get_content_type() == "image/jpeg"
        data = r.read()
    assert data[:2] == b"\xff\xd8" and len(data) > 1000


def test_dashboard_serves_html(live_state):
    port, _ = live_state
    with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=5) as r:
        html = r.read().decode()
    assert (
        "VIGLANCE" in html and "/stream.mjpg" in html and "/assets/dashboard.js" in html
    )
    with urllib.request.urlopen(
        f"http://127.0.0.1:{port}/assets/dashboard.js", timeout=5
    ) as r:
        html += r.read().decode()
    with urllib.request.urlopen(
        f"http://127.0.0.1:{port}/assets/dashboard.css", timeout=5
    ) as r:
        html += r.read().decode()
    # alerts render live from the backend: no hardcoded demo cards/scores
    assert "alertsList" in html and "renderAlerts" in html
    assert "alertCard1" not in html and "cam1Alert1" not in html
    assert "81%" not in html and "Alert: Unattended Bag" not in html
    # persistent panel: store, ACTIVE badge, stale state, capped timeline
    assert "alertStore" in html and "live-badge" in html
    assert "MAX_STORED_ALERTS" in html and "last seen" in html


def test_alert_details_helper():
    from ui import _alert_details

    out = _alert_details(
        {
            "Fall Detection": {
                "alert": True,
                "confidence": 0.77,
                "message": "Person 2 is down.",
            }
        },
        {"Fire Threat": {"alert": True, "confidence": 0.91, "message": "Fire!"}},
    )
    assert out == {
        "Fall Detection": {"confidence": 0.77, "message": "Person 2 is down."},
        "Fire Threat": {"confidence": 0.91, "message": "Fire!"},
    }
    assert _alert_details({"X": "junk"}, {}) == {}
    assert _alert_details({}, {}) == {}


def test_ui_source_defaults_to_config_value():
    assert parse_args([]).source is None
    assert parse_args(["--source", "clip.avi"]).source == "clip.avi"


def test_ui_device_option():
    assert parse_args([]).device is None
    assert parse_args(["--device", "cpu"]).device == "cpu"
    assert parse_args(["--device", "cuda"]).device == "cuda"


def test_threats_opt_in_not_default():
    args = parse_args([])
    assert args.threats is False and args.no_threats is False


def test_no_fire_flag_default_off():
    assert parse_args([]).no_fire is False
    assert parse_args(["--no-fire"]).no_fire is True


def test_uploaded_video_plays_three_times_then_ends(tmp_path):
    import cv2

    from config import VigilanceConfig
    from core.pipeline import Pipeline
    from tests.conftest import FakeDetector

    path = str(tmp_path / "clip.avi")
    writer = cv2.VideoWriter(path, cv2.VideoWriter_fourcc(*"MJPG"), 5.0, (64, 48))
    for _ in range(3):
        writer.write(np.zeros((48, 64, 3), dtype=np.uint8))
    writer.release()

    cfg = VigilanceConfig()
    pipe = Pipeline(config=cfg, detector=FakeDetector([[]]))
    state = LiveState()
    pump = threading.Thread(
        target=pump_frames, args=(state, pipe, path, 5.0), daemon=True
    )
    pump.start()
    try:
        ended = False
        for _ in range(600):
            with state.lock:
                ended = bool(state.status.get("ended")) and not state.status.get(
                    "running"
                )
            if ended:
                break
            time.sleep(0.05)
        assert ended, "video did not reach ended state"
        # Each replay resets the pipeline, so the counter holds exactly the
        # final play's frames; reaching "ended" at all (instead of looping
        # forever) is the regression under test.
        assert pipe.frame_index == 3
        assert state.status["ended"] is True
        assert state.status["frame"] == 2
        assert "model" in state.status
    finally:
        state.stop = True
        pump.join(timeout=10)


def test_synthetic_demo_switches_to_uploaded_video(tmp_path):
    import cv2

    from config import VigilanceConfig
    from core.pipeline import Pipeline
    from tests.conftest import FakeDetector, black_frames

    path = str(tmp_path / "uploaded.avi")
    writer = cv2.VideoWriter(path, cv2.VideoWriter_fourcc(*"MJPG"), 5.0, (64, 48))
    assert writer.isOpened()
    for _ in range(10):
        writer.write(np.zeros((48, 64, 3), dtype=np.uint8))
    writer.release()

    cfg = VigilanceConfig()
    state = LiveState()
    pump = threading.Thread(
        target=pump_frames,
        args=(
            state,
            Pipeline(config=cfg, detector=FakeDetector([[]])),
            "synthetic",
            5.0,
            black_frames(2, h=48, w=64),
        ),
        daemon=True,
    )
    pump.start()
    try:
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            with state.lock:
                if state.status.get("running"):
                    break
            time.sleep(0.02)
        else:
            pytest.fail("synthetic feed did not start")

        state.request_source(path)
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            with state.lock:
                if state.status.get("source") == path:
                    break
            time.sleep(0.02)
        else:
            pytest.fail("uploaded video did not replace the synthetic feed")
    finally:
        state.stop = True
        pump.join(timeout=10)


def test_pair_payload_carries_readiness(live_state):
    port, _ = live_state
    with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/status", timeout=5) as r:
        body = json.loads(r.read())
    assert len(body["pairs"]) >= 1
    for pair in body["pairs"]:
        assert set(pair) >= {"a", "b", "key", "score", "buf", "span", "ready", "dist"}
        assert isinstance(pair["ready"], bool)
        assert pair["span"] >= 0.0


def test_timeline_groups_contiguous_fight_frames():
    state = LiveState()
    state.record_event(2.0, "FIGHT", {})
    state.record_event(2.1, "FIGHT", {})
    state.record_event(2.2, "NO_FIGHT", {})
    assert state.timeline_snapshot() == [
        {
            "id": "0:1",
            "start": 2.0,
            "end": 2.1,
            "scene": "FIGHT",
            "alerts": [],
        }
    ]


def test_timeline_capped_for_long_runs():
    state = LiveState()
    for i in range(300):
        state.record_event(float(i), "NO_FIGHT", {})
        state.record_event(float(i) + 0.05, "FIGHT", {})
    assert len(state.timeline_snapshot()) == 200


class _FallenDetector:
    """Serves one static horizontal person: trips the pose-rule fall alert."""

    def detect(self, frame, timestamp=0.0):
        from core.detector import DetectorOutput
        from core.tracked_person import TrackedPerson

        kps = np.zeros((17, 3), dtype=np.float32)
        for i in range(17):
            kps[i] = [40.0 + i * 10.0, 150.0, 0.9]
        kps[5] = [60.0, 150.0, 0.9]
        kps[6] = [70.0, 150.0, 0.9]
        kps[11] = [140.0, 150.0, 0.9]
        kps[12] = [150.0, 150.0, 0.9]
        person = TrackedPerson(
            track_id=1,
            bbox=[40.0, 120.0, 200.0, 160.0],
            keypoints=kps,
            detection_conf=0.9,
            timestamp=float(timestamp),
        )
        return DetectorOutput(persons=[person], pose_results=None, det_results=[])

    def reset(self):
        pass


class _Recorder:
    ready = True
    sent_count = 0

    def __init__(self):
        self.calls = []

    def maybe_notify(self, incident, jpeg=None):
        self.calls.append(incident)
        return incident is not None


def test_fall_alert_reaches_notifier():
    from config import VigilanceConfig
    from core.pipeline import Pipeline
    from tests.conftest import black_frames

    cfg = VigilanceConfig()
    assert cfg.enable_fall is True
    pipe = Pipeline(config=cfg, detector=_FallenDetector())
    rec = _Recorder()
    state = LiveState()
    pump = threading.Thread(
        target=pump_frames,
        args=(
            state,
            pipe,
            "synthetic",
            cfg.fps,
            black_frames(60, h=240, w=320),
            None,
            rec,
        ),
        daemon=True,
    )
    pump.start()
    try:
        fired = False
        for _ in range(400):
            if any(c is not None for c in rec.calls):
                fired = True
                break
            time.sleep(0.05)
        assert fired, "fall incident never reached the notifier"
        names = [n for c in rec.calls if c is not None for n in c.alerts]
        assert "Fall Detection" in names
    finally:
        state.stop = True
        pump.join(timeout=10)


def test_status_reports_telegram_state(live_state):
    port, _ = live_state
    with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/status", timeout=5) as r:
        body = json.loads(r.read())
    assert body["telegram"] == {"enabled": False, "ready": False, "sent": 0}


def test_upload_endpoint_accepts_avi(live_state):
    port, _ = live_state
    boundary = "----Viglance" + uuid.uuid4().hex
    body = (
        f"--{boundary}\r\n"
        'Content-Disposition: form-data; name="video"; filename="demo.avi"\r\n'
        "Content-Type: video/x-msvideo\r\n\r\n"
        "not-a-real-video\r\n"
        f"--{boundary}--\r\n"
    ).encode()
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}/api/upload",
        data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=5) as response:
        payload = json.loads(response.read())
    assert payload["ok"] is True and payload["source"].endswith(".avi")


def test_unknown_route_404(live_state):
    port, _ = live_state
    try:
        urllib.request.urlopen(f"http://127.0.0.1:{port}/nope", timeout=5)
    except urllib.error.HTTPError as exc:
        assert exc.code == 404
    else:
        raise AssertionError("expected 404")
