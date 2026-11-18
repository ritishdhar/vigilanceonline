"""Exercise durable evidence, recipient isolation, and human handoff."""

from http.client import HTTPConnection
import json
from threading import Thread
from uuid import uuid4

import cv2
import numpy as np
import pytest

from core.http_server import BoundedHTTPServer, make_handler
from core.incident_video import FPS, IncidentRecorder
from core.incidents import IncidentStore
from core.phone_recipients import validate_subscription
from ui import LiveState


def observed_event(incident_id):
    return {
        "id": incident_id,
        "scene": "FIGHT",
        "start": 5.0,
        "alerts": [],
        "confidence": 0.82,
    }


@pytest.fixture
def workspace(tmp_path):
    store = IncidentStore(tmp_path, site="East campus", camera="Entrance")
    state = LiveState(incident_store=store, phone_origin="https://phones.example.test")
    server = BoundedHTTPServer(("127.0.0.1", 0), make_handler(state))
    phone_server = BoundedHTTPServer(
        ("127.0.0.1", 0), make_handler(state, recipient_only=True)
    )
    thread = Thread(target=server.serve_forever, daemon=True)
    phone_thread = Thread(target=phone_server.serve_forever, daemon=True)
    thread.start()
    phone_thread.start()

    def request(
        path,
        *,
        body=None,
        method=None,
        host=None,
        cookie=None,
        operator=False,
        recipient_port=False,
        **extra,
    ):
        headers = dict(extra)
        if host:
            headers["Host"] = host
        if operator:
            headers["X-Vigilance-Token"] = state.token
        if cookie:
            headers["Cookie"] = cookie
        if body is not None:
            headers.update(
                {"Content-Type": "application/json", "X-Raksha-Recipient": "1"}
            )
            body = json.dumps(body)
        port = phone_server.server_port if recipient_port else server.server_port
        connection = HTTPConnection("127.0.0.1", port, timeout=5)
        try:
            connection.request(
                method or ("GET" if body is None else "POST"), path, body, headers
            )
            response = connection.getresponse()
            content = response.read()
            if response.headers.get_content_type() == "application/json" and content:
                content = json.loads(content)
            return response.status, content, response.headers
        finally:
            connection.close()

    try:
        yield state, request
    finally:
        phone_server.shutdown()
        phone_server.server_close()
        phone_thread.join(3)
        server.shutdown()
        server.server_close()
        thread.join(3)
        state.uploads.close()


def test_phone_host_only_exposes_recipient_routes(workspace):
    _, request = workspace
    host = "phones.example.test"
    assert request("/", host=host)[0] == 302
    for path in (
        "/api/status",
        "/stream.mjpg",
        "/api/session",
        "/api/phone-admin",
        "/api/incidents",
    ):
        assert request(path, host=host, operator=True)[0] == 404
    assert request("/api/recipient/session", host=host)[0] == 200
    assert (
        request("/api/recipient/session", host=host, Origin="https://outside.example")[
            0
        ]
        == 403
    )
    assert request("/api/phone-admin")[0] == 403
    assert request("/api/recipient/incidents", host=host)[0] == 401
    assert request("/recipient/manifest.webmanifest", host=host)[0] == 200
    assert request("/recipient/", host=host, recipient_port=True)[0] == 200
    assert (
        request("/api/status", host=host, recipient_port=True, operator=True)[0] == 404
    )
    assert request("/api/session", recipient_port=True, operator=True)[0] == 403


def test_loopback_enrollment_has_a_preview_link_without_a_qr(workspace):
    state, request = workspace
    state.phone_origin = None
    code, invite, _ = request(
        "/api/phone-admin/invite", body={"role": "recipient"}, operator=True
    )
    assert code == 201
    assert invite["url"].startswith("http://127.0.0.1:")
    assert invite["qr"] is None


def test_invite_progress_only_reports_the_device_that_used_that_invite(workspace):
    state, request = workspace
    path = "/api/phone-admin/invite/progress"
    phones = state.phone_recipients
    first, _ = phones.invite("recipient")
    second, _ = phones.invite("responder")
    assert request(path, body={"code": first})[0] == 403
    assert request(path, host="phones.example.test", body={"code": first})[0] == 404
    assert request(path, operator=True, body={"code": first})[:2] == (
        200,
        {"status": "waiting"},
    )
    phones.enroll(second, "Other phone")
    assert request(path, operator=True, body={"code": first})[1]["status"] == "waiting"
    token = phones.enroll(first, "iPhone")
    result = request(path, operator=True, body={"code": first})[1]
    assert result["status"] == "linked"
    assert result["device"]["id"] == phones.session(token)["id"]
    assert result["device"]["name"] == "iPhone"
    cancel = "/api/phone-admin/invite/cancel"
    assert request(cancel, operator=True, body={"code": first})[1] == result
    unused, _ = phones.invite("recipient")
    assert request(cancel, body={"code": unused})[0] == 403
    assert request(cancel, operator=True, body={"code": unused})[1] == {"status": "canceled"}
    assert request("/api/recipient/invite/status", body={"invite": unused})[0] == 410
    with pytest.raises(ValueError, match="expired or was already used"):
        phones.enroll(unused, "Canceled phone")
    assert request("/api/recipient/invite/status", body={"invite": first})[0] == 410
    another, _ = phones.invite("recipient")
    assert phones.enroll(another, "Another name", existing_token=token) == token
    assert phones.invite_progress(another)["device"]["id"] == result["device"]["id"]
    assert len(phones.devices()) == 2
    wrong_role, _ = phones.invite("responder")
    with pytest.raises(ValueError, match="different role"):
        phones.enroll(wrong_role, "iPhone", existing_token=token)
    assert phones.invite_progress(wrong_role)["status"] == "waiting"


def test_enrollment_media_review_and_handoff_are_scoped(workspace):
    state, request = workspace
    store = state.incident_store
    incident_id = uuid4().hex
    event = observed_event(incident_id)
    store.record(event, "Camera 0", clip="ready")
    store.record(event, "Camera 0")
    fake_media = b"abcdefghij"
    store.media(incident_id, ".mp4").write_bytes(fake_media)
    store.media(incident_id, ".jpg").write_bytes(b"alert still")
    store.media(incident_id, ".after.jpg").write_bytes(b"after still")
    host = "phones.example.test"
    path = "/api/recipient/incidents/" + incident_id

    def enroll(role):
        code, invite, _ = request(
            "/api/phone-admin/invite", body={"role": role}, operator=True
        )
        assert code == 201 and invite["url"].startswith(
            "https://phones.example.test/recipient/#invite="
        )
        check = {"host": host, "body": {"invite": invite["code"]}}
        assert request("/api/recipient/invite/status", **check)[:2] == (
            200,
            {"expires": invite["expires"]},
        )
        kwargs = {"host": host, "body": {"invite": invite["code"], "name": role}}
        code, _, headers = request("/api/recipient/enroll", **kwargs)
        assert (
            code == 201
            and "HttpOnly" in headers["Set-Cookie"]
            and "Secure" in headers["Set-Cookie"]
        )
        assert request("/api/recipient/enroll", **kwargs)[0] == 400
        assert request("/api/recipient/invite/status", **check)[0] == 410
        return headers["Set-Cookie"].split(";")[0]

    recipient, responder = enroll("recipient"), enroll("responder")
    assert request(path + "/clip.mp4", host=host)[0] == 401
    assert request(path + "/clip.mp4", host=host, cookie=responder)[0] == 404
    assert request(path + "/after.jpg", host=host)[0] == 401
    assert request(path + "/after.jpg", host=host, cookie=responder)[0] == 404
    detail = request(path, host=host, cookie=recipient)[1]
    assert detail["stills"] == ["thumbnail.jpg", "after.jpg"]
    assert detail["handoff_available"]
    code, content, headers = request(path + "/after.jpg", host=host, cookie=recipient)
    assert (code, content, headers["Content-Type"]) == (200, b"after still", "image/jpeg")
    assert request(path + "/before.jpg", host=host, cookie=recipient)[0] == 404
    receipt = {"incident": incident_id, "status": "displayed"}
    receipt_path = "/api/recipient/notification/receipt"
    assert request(receipt_path, host=host, body=receipt)[0] == 401
    assert request(receipt_path, host=host, cookie=responder, body=receipt)[0] == 404
    assert request(receipt_path, host=host, cookie=recipient, body=receipt)[0] == 200
    assert (
        request(path + "/handoff", host=host, cookie=recipient, body={"confirm": True})[
            0
        ]
        == 400
    )

    code, content, headers = request(
        path + "/clip.mp4", host=host, cookie=recipient, Range="bytes=2-5"
    )
    assert (code, content, headers["Content-Range"]) == (206, b"cdef", "bytes 2-5/10")
    assert (
        request(path + "/clip.mp4", host=host, cookie=recipient, Range="bytes=50-")[0]
        == 416
    )
    assert request(path + "/opened", host=host, cookie=recipient, body={})[0] == 200
    assert request(path + "/opened", host=host, cookie=recipient, body={})[0] == 200
    assert (
        request(
            path + "/review",
            host=host,
            cookie=recipient,
            body={"action": "acknowledged"},
        )[0]
        == 200
    )
    assert (
        request(
            path + "/handoff", host=host, cookie=recipient, body={"confirm": False}
        )[0]
        == 400
    )
    for _ in range(2):
        assert (
            request(
                path + "/handoff",
                host=host,
                cookie=recipient,
                body={"confirm": True, "note": "Please inspect"},
            )[0]
            == 200
        )
    assert (
        request(path + "/clip.mp4", method="HEAD", host=host, cookie=responder)[0]
        == 200
    )
    assert request(path + "/after.jpg", host=host, cookie=responder)[0] == 200
    assert (
        request(
            path + "/review",
            host=host,
            cookie=responder,
            body={"action": "acknowledged"},
        )[0]
        == 200
    )

    reopened = IncidentStore(store.directory)
    actions = reopened.get(incident_id)["actions"]
    assert [item["action"] for item in actions] == [
        "responder-acknowledged",
        "escalated",
        "acknowledged",
        "opened",
    ]
    assert actions[1]["note"] == "Please inspect"
    assert len(reopened.list(responder=True)) == 1
    assert len(reopened.list()) == 1
    responder_id = next(
        device["id"]
        for device in state.phone_recipients.devices()
        if device["role"] == "responder"
    )
    assert (
        request("/api/phone-admin/revoke", operator=True, body={"id": responder_id})[0]
        == 200
    )
    assert request(path + "/clip.mp4", host=host, cookie=responder)[0] == 401


def test_recorder_encodes_pre_and_post_frames(tmp_path):
    encoder = pytest.importorskip("imageio_ffmpeg").get_ffmpeg_exe()
    store = IncidentStore(tmp_path)
    recorder = IncidentRecorder(store, encoder)
    incident_id = uuid4().hex
    try:
        for index in range(FPS * 15 + 1):
            _, image = cv2.imencode(".jpg", np.full((64, 96, 3), index, dtype=np.uint8))
            events = [observed_event(incident_id)] if index == 5 * FPS else []
            recorder.ingest(index / FPS, image.tobytes(), events, "Camera 0")
    finally:
        recorder.close()
    incident = store.get(incident_id)
    assert incident["clip"] == "ready"
    assert incident["stills"] == ["before.jpg", "thumbnail.jpg", "after.jpg"]
    frames = [
        cv2.imread(str(store.media(incident_id, suffix))).mean()
        for suffix in (".before.jpg", ".jpg", ".after.jpg")
    ]
    assert frames == [0, 5 * FPS, 15 * FPS]
    clip = cv2.VideoCapture(str(store.media(incident_id, ".mp4")))
    try:
        assert clip.isOpened()
        assert int(clip.get(cv2.CAP_PROP_FRAME_COUNT)) == 15 * FPS + 1
        assert clip.get(cv2.CAP_PROP_FPS) == FPS
    finally:
        clip.release()


def test_public_detection_notice_omits_camera_evidence_and_deduplicates(tmp_path):
    from core.phone_recipients import PhoneRecipients

    store = IncidentStore(tmp_path, site="RakshaNetra demo", camera="Test alerts")
    phones = PhoneRecipients(store)
    token, _ = phones.invite("recipient")
    device = phones.session(phones.enroll(token, "Phone"))
    event = dict(observed_event(uuid4().hex), alerts=["Weapon Threat"], source="private camera")
    store.detection_notice(event)
    store.detection_notice(event)
    records = store.list()
    assert len(records) == 1
    record = store.get(records[0]["id"])
    assert record["title"] == "Possible weapon detected"
    assert record["source"] == "Detection notification"
    assert record["confidence"] is None and record["scene"] is None
    assert record["stills"] == [] and record["clip"] == "unavailable"
    assert device is not None


@pytest.mark.parametrize("label", ["knife", "short-gun"])
def test_confirmed_weapon_reaches_phone_outbox_without_a_fight(tmp_path, monkeypatch, label):
    from base64 import urlsafe_b64encode
    from threading import Event
    from types import SimpleNamespace

    from config import VigilanceConfig
    from core.pipeline import Pipeline
    from core.threat_detection import SurveillanceThreatDetector, ThreatDetection
    from tests.conftest import FakeDetector
    import ui

    live = LiveState(incident_store=IncidentStore(tmp_path / "live"))
    public = LiveState(incident_store=IncidentStore(tmp_path / "public"))
    live.demo_recipient_state = public
    public.phone_push = SimpleNamespace(wake=Event())
    invite, _ = public.phone_recipients.invite("recipient")
    phone = public.phone_recipients.session(public.phone_recipients.enroll(invite, "Phone"))
    public.phone_recipients.subscribe(phone["id"], {
        "endpoint": "https://fcm.googleapis.com/fcm/send/weapon-test",
        "keys": {
            "p256dh": urlsafe_b64encode(b"\x04" + b"X" * 64).decode(),
            "auth": urlsafe_b64encode(b"X" * 16).decode(),
        },
    })
    cfg = VigilanceConfig(enable_fight=False, enable_fall=False, enable_fire=False)
    detector = SurveillanceThreatDetector(conf=cfg.conf, enable_weapon_alerts=cfg.enable_threats)
    detector._load_attempted = True
    detector.models = {"surveillance": object()}
    monkeypatch.setattr(detector, "_predict", lambda _: [
        ThreatDetection("surveillance", label, "weapon", 0.65, [10, 10, 40, 40])
    ])
    pipeline = Pipeline(config=cfg, detector=FakeDetector([[]]), threat_detector=detector)

    def finish(_):
        if pipeline.frame_index >= 4:
            live.stop = True

    monkeypatch.setattr(ui.time, "sleep", finish)
    try:
        ui.pump_frames(live, pipeline, "synthetic", 10, [np.zeros((80, 80, 3), dtype=np.uint8)])
        records = public.incident_store.list()
        assert len(records) == 1
        assert records[0]["title"] == "Possible weapon detected"
        assert live.incident_store.get(records[0]["id"])["confidence"] == 0.65
        delivery = public.phone_recipients.next_delivery()
        assert delivery["incident_id"] == records[0]["id"]
        assert delivery["device_id"] == phone["id"]
        assert public.phone_push.wake.is_set()
    finally:
        live.uploads.close()
        public.uploads.close()


def test_public_notice_opens_evidence_only_at_the_protected_origin(workspace, tmp_path):
    from urllib.parse import parse_qs, urlsplit

    state, request = workspace
    live = LiveState(
        incident_store=IncidentStore(tmp_path / "live"),
        phone_origin="https://protected.example.test",
    )
    state.evidence_state = live
    incident_id = uuid4().hex
    event = dict(observed_event(incident_id), alerts=["Weapon Threat"])
    state.incident_store.detection_notice(event)
    live.incident_store.record(event, "Private camera")
    live.incident_store.media(incident_id, ".jpg").write_bytes(b"private still")
    live.incident_store.set_clip(incident_id, "processing")
    path = "/api/recipient/incidents/" + incident_id
    host = "phones.example.test"
    try:
        assert request(path + "/evidence", body={}, host=host)[0] == 401
        invite, _ = state.phone_recipients.invite("recipient")
        _, _, headers = request(
            "/api/recipient/enroll", host=host,
            body={"invite": invite, "name": "Phone"},
        )
        cookie = headers["Set-Cookie"].split(";")[0]
        detail = request(path, host=host, cookie=cookie)[1]
        assert detail["protected_evidence"]
        assert detail["evidence_status"] == "processing"
        assert detail["photo_count"] == 1
        assert "Private camera" not in json.dumps(detail)
        code, data, _ = request(path + "/evidence", host=host, cookie=cookie, body={})
        link = urlsplit(data["url"])
        assert code == 200 and link.netloc == "protected.example.test"
        assert parse_qs(link.fragment)["incident"] == [incident_id]
        assert live.phone_recipients.invite_expiry(parse_qs(link.fragment)["invite"][0])
        assert request(path + "/clip.mp4", host=host, cookie=cookie)[0] == 404
    finally:
        live.uploads.close()


def test_public_recipient_evidence_plays_inline_with_device_and_incident_scope(workspace, tmp_path):
    state, request = workspace
    live = LiveState(incident_store=IncidentStore(tmp_path / "live"))
    state.evidence_state = live
    state.public_recipient_evidence = True
    incident_id, other_id = uuid4().hex, uuid4().hex
    event = dict(observed_event(incident_id), alerts=["Weapon Threat"])
    state.incident_store.detection_notice(event)
    live.incident_store.record(event, "Private camera", clip="processing")
    live.incident_store.record(observed_event(other_id), "Unshared camera", clip="ready")
    live.incident_store.media(incident_id, ".mp4").write_bytes(b"abcdefghij")
    live.incident_store.media(incident_id, ".jpg").write_bytes(b"alert photo")
    live.incident_store.media(incident_id, ".after.jpg").write_bytes(b"after photo")
    live.incident_store.media(other_id, ".mp4").write_bytes(b"unshared clip")
    path, host = "/api/recipient/incidents/" + incident_id, "phones.example.test"

    def enroll(role):
        invite, _ = state.phone_recipients.invite(role)
        code, _, headers = request(
            "/api/recipient/enroll", host=host, body={"invite": invite, "name": role}
        )
        assert code == 201
        return headers["Set-Cookie"].split(";")[0]

    try:
        recipient, responder = enroll("recipient"), enroll("responder")
        for name in ("", "/clip.mp4", "/thumbnail.jpg", "/after.jpg"):
            assert request(path + name, host=host)[0] == 401
            assert request(path + name, host=host, cookie=responder)[0] == 404
        detail = request(path, host=host, cookie=recipient)[1]
        assert detail["inline_evidence"] and not detail["protected_evidence"]
        assert detail["clip"] == "processing"
        assert detail["stills"] == ["thumbnail.jpg", "after.jpg"]
        assert "Private camera" not in json.dumps(detail)
        assert request(path + "/thumbnail.jpg", host=host, cookie=recipient)[:2] == (200, b"alert photo")
        live.incident_store.set_clip(incident_id, "ready")
        assert request(path, host=host, cookie=recipient)[1]["clip"] == "ready"
        code, content, headers = request(
            path + "/clip.mp4", host=host, cookie=recipient, Range="bytes=2-5"
        )
        assert (code, content, headers["Content-Range"]) == (206, b"cdef", "bytes 2-5/10")
        assert headers["Cache-Control"] == "private, no-store"
        assert request(path + "/after.jpg", host=host, cookie=recipient, method="HEAD")[0] == 200
        fallback = request(path + "/evidence", host=host, cookie=recipient, body={})[1]
        assert fallback["url"] == state.phone_origin + "/recipient/#incident=" + incident_id
        assert live.phone_recipients.devices() == [], "Inline review must not create another workspace session"
        unshared = "/api/recipient/incidents/" + other_id
        assert request(unshared + "/clip.mp4", host=host, cookie=recipient)[0] == 404
        state.incident_store.review(incident_id, "acknowledged", "Recipient")
        state.phone_recipients.handoff(incident_id, {"role": "recipient", "id": "a" * 32, "name": "Recipient"}, "")
        assert request(path + "/after.jpg", host=host, cookie=responder)[:2] == (200, b"after photo")
        assert request(path + "/before.jpg", host=host, cookie=recipient)[0] == 404
        request("/api/recipient/logout", host=host, cookie=recipient, body={})
        assert request(path + "/clip.mp4", host=host, cookie=recipient)[0] == 401
    finally:
        live.uploads.close()


def test_clear_inbox_is_per_device_durable_and_preserves_new_alerts(workspace):
    state, request = workspace
    host = "phones.example.test"
    path = "/api/recipient"

    def enroll():
        invite, _ = state.phone_recipients.invite("recipient")
        _, _, headers = request(path + "/enroll", host=host, body={"invite": invite, "name": "Phone"})
        return headers["Set-Cookie"].split(";")[0]

    first, second = enroll(), enroll()
    incident_id = state.incident_store.test_alert()
    sequence = state.incident_store.get(incident_id)["sequence"]
    with state.incident_store.connect() as db:
        device_id = request(path + "/session", host=host, cookie=first)[1]["device"]["id"]
        db.execute("INSERT INTO phone_outbox (incident_id,device_id,due) VALUES (?,?,0)", (incident_id, device_id))
    assert request(path + "/inbox/clear", host=host, body={"through": sequence})[0] == 401
    for invalid in (True, -1, sequence + 1, "1", None):
        assert request(path + "/inbox/clear", host=host, cookie=first, body={"through": invalid})[0] == 400
    assert request(path + "/inbox/clear", host=host, cookie=first, body={"through": sequence})[:2] == (
        200, {"cleared_through": sequence}
    )
    assert request(path + "/incidents", host=host, cookie=first)[1]["incidents"] == []
    assert len(request(path + "/incidents", host=host, cookie=second)[1]["incidents"]) == 1
    assert state.incident_store.get(incident_id) is not None
    assert request(path + "/incidents/" + incident_id, host=host, cookie=first)[0] == 200
    assert request(path + "/session", host=host, cookie=first)[1]["cleared_through"] == sequence
    from core.phone_recipients import PhoneRecipients
    assert PhoneRecipients(state.incident_store).inbox_cleared_through(device_id) == sequence
    state.phone_recipients.delivery_result({"incident_id": incident_id, "device_id": device_id}, "queued", 1)
    with state.incident_store.connect() as db:
        assert db.execute("SELECT status FROM phone_outbox WHERE device_id=?", (device_id,)).fetchone()[0] == "cleared"
    newer = state.incident_store.test_alert()
    # An older confirmation must not clear an alert that arrived after the prompt.
    request(path + "/inbox/clear", host=host, cookie=first, body={"through": sequence})
    assert [row["id"] for row in request(path + "/incidents", host=host, cookie=first)[1]["incidents"]] == [newer]
    recipient = request(path + "/session", host=host, cookie=first)[1]["device"]
    invite, _ = state.phone_recipients.invite("responder")
    responder = state.phone_recipients.session(state.phone_recipients.enroll(invite, "Responder"))
    state.incident_store.review(newer, "acknowledged", "Recipient")
    state.phone_recipients.handoff(newer, recipient, "")
    state.phone_recipients.clear_inbox(responder, state.incident_store.get(newer)["sequence"])
    assert state.phone_recipients.inbox(responder) == []
    # Forwarding an earlier incident later must still notify the responder.
    state.incident_store.review(incident_id, "acknowledged", "Recipient")
    state.phone_recipients.handoff(incident_id, recipient, "")
    assert [row["id"] for row in state.phone_recipients.inbox(responder)] == [incident_id]


def test_push_outbox_claims_incident_once_and_expires_endpoint(tmp_path, monkeypatch):
    pytest.importorskip("pywebpush")
    from base64 import urlsafe_b64encode
    from types import SimpleNamespace

    import core.phone_push as push_module
    from core.phone_recipients import PhoneRecipients

    incidents = IncidentStore(tmp_path)
    phones = PhoneRecipients(incidents)
    app = push_module.PhonePush(phones, "mailto:operator@example.test")
    assert (
        push_module.PhonePush(phones, "mailto:operator@example.test").public_key
        == app.public_key
    )
    token, _ = phones.invite("recipient")
    device = phones.session(phones.enroll(token, "Recipient phone"))
    payload = {
        "endpoint": "https://fcm.googleapis.com/fcm/send/test-subscription",
        "keys": {
            "p256dh": urlsafe_b64encode(b"\x04" + b"X" * 64).decode(),
            "auth": urlsafe_b64encode(b"X" * 16).decode(),
        },
    }
    phones.subscribe(device["id"], payload)
    original_device = device
    replacement_invite, _ = phones.invite("recipient")
    device = phones.session(phones.enroll(replacement_invite, "Replacement phone"))
    phones.subscribe(device["id"], payload)
    assert next(row for row in phones.devices() if row["id"] == original_device["id"])["subscribed"] == 0
    with pytest.raises(ValueError, match="Unsupported push provider"):
        validate_subscription(
            dict(payload, endpoint="https://127.0.0.1:8000/api/status")
        )

    incident_id = incidents.test_alert()
    delivery = phones.next_delivery()
    assert delivery["incident_id"] == incident_id and phones.next_delivery() is None

    def accepted(**kwargs):
        data = json.loads(kwargs["data"])
        assert data == {
            "title": "RakshaNetra test alert",
            "body": "Tap to open the test alert.",
            "incident": incident_id,
        }
        return SimpleNamespace(status_code=201)

    monkeypatch.setattr(push_module, "webpush", accepted)
    app.deliver(None, delivery)
    assert phones.delivery_summary(incident_id) == {"accepted": 1}
    phones.notification_receipt(device["id"], incident_id, "displayed")
    phones.notification_receipt(device["id"], incident_id, "display-failed")
    assert phones.notification_status(device["id"])["displayed_at"]
    assert phones.notification_status(device["id"])["display_failed"] == 0
    assert phones.next_delivery() is None

    def temporary_failure(**kwargs):
        raise push_module.WebPushException("temporary failure")

    monkeypatch.setattr(push_module, "webpush", temporary_failure)
    retry_id = incidents.test_alert()
    app.deliver(None, phones.next_delivery())
    assert phones.delivery_summary(retry_id) == {"queued": 1}
    assert phones.next_delivery() is None

    def expired(**kwargs):
        raise push_module.WebPushException(
            "gone", response=SimpleNamespace(status_code=410)
        )

    monkeypatch.setattr(push_module, "webpush", expired)
    next_id = incidents.test_alert()
    app.deliver(None, phones.next_delivery())
    assert phones.delivery_summary(next_id) == {"expired": 1}
    assert phones.devices()[0]["subscribed"] == 0
