"""VIGLANCE local dashboard with authenticated control and streaming uploads.

Usage:
    python ui.py --source 0                      # webcam + real YOLO detector
    python ui.py --source video.mp4              # MP4/AVI/MOV/etc. + detector
    python ui.py --config config/default.json     # default trained fight model
    python ui.py --synthetic                     # scripted demo, no camera/weights
    python ui.py --source 0 --fight-checkpoint models/stgcnpp/best_fight_stgcnpp_v1.pth

Then open http://localhost:8000 in a browser.

Endpoints: / (dashboard), /stream.mjpg (MJPEG), /api/status (JSON),
/snapshot.jpg (single frame), /api/upload (multipart video), and
/api/source (webcam/source switching). OpenCV handles MP4, AVI, MOV, MKV,
WebM, MPEG, and other locally supported codecs. The auxiliary YOLO threat
model (`xDevine/yolo11s-multiclass-surveillance-detection`) downloads from
Hugging Face on first use unless disabled.
"""

from __future__ import annotations

import argparse
from ipaddress import IPv4Address, IPv4Network
import os
from pathlib import Path
import sqlite3
import ssl
import threading
import time
from urllib.parse import urlsplit
from uuid import uuid4
from core.http_server import BoundedHTTPServer, make_handler

from config import VigilanceConfig
from core.gpu import cuda_device_name, require_cuda
from core.incident_video import MAX_FRAME_BYTES, IncidentRecorder
from core.incidents import IncidentStore, incident_source
from core.logging_utils import get_logger, setup_logging
from core.pipeline import Pipeline
from core.stgcn_adapter import is_buffer_ready
from core.video_upload import UploadStore
from core.source_utils import CaptureClock, redact_source
from core.paths import resolve_asset
from core.phone_recipients import PhoneRecipients
import secrets

log = get_logger("ui")

#: Uploaded/file videos play at most this many times, then stop on an
#: "ended" status instead of looping forever. Webcams (digit sources) and
#: the synthetic demo are unaffected.
MAX_VIDEO_PLAYS = 3

try:
    import cv2
except ImportError as exc:
    raise ImportError(
        "opencv-python is required for the UI (capture + JPEG encoding)."
    ) from exc


# --------------------------------------------------------------------------
# shared live state
# --------------------------------------------------------------------------


class LiveState:
    def __init__(
        self,
        allowed_sources=(),
        inference_mode="model",
        allowed_hosts=None,
        allowed_origins=None,
        secure_cookies=False,
        judge_mode=False,
        video_plays=MAX_VIDEO_PLAYS,
        incident_store=None,
        phone_origin=None,
        emergency_number=None,
    ):
        self.token = secrets.token_urlsafe(32)
        self.allowed_hosts = allowed_hosts
        self.allowed_origins = allowed_origins
        self.secure_cookies = bool(secure_cookies)
        self.judge_mode = bool(judge_mode)
        self.video_plays = int(video_plays)
        self.uploads = UploadStore()
        self.allowed_sources = frozenset(map(str, allowed_sources))
        self.inference_mode = inference_mode
        self.stream_slots = threading.BoundedSemaphore(4)
        self.active_source = None
        self.frame_updated_at = 0.0
        self.event_counter = 0
        self.active_event_id = None
        self.lock = threading.Lock()
        self.jpeg: bytes | None = None
        self.status: dict = {"running": False, "timeline": []}
        self.stop = False
        self.source_request = None
        self.source_generation = 0
        self.timeline = []
        self.active_event = None
        self.active_events = {}
        self.notification_events = []
        self.browser_camera = None
        self.model_status = {}
        self.incident_store = incident_store
        self.incident_recorder = None
        self.phone_origin = phone_origin
        self.emergency_number = emergency_number
        self.phone_host = urlsplit(phone_origin).netloc if phone_origin else None
        self.phone_recipients = (
            PhoneRecipients(incident_store) if incident_store is not None else None
        )
        self.phone_push = None

    def request_source(self, source: str) -> int:
        if self.inference_mode == "synthetic":
            raise ValueError("Restart in model mode to analyze uploaded or live video")
        with self.lock:
            previous = self.source_request[1] if self.source_request else None
            active = self.active_source
            self.source_generation += 1
            self.source_request = (self.source_generation, str(source))
            self.browser_camera = None
            generation = self.source_generation
            self.status = dict(self.status, running=False, source_pending=True)
        if previous and previous != active and previous != str(source):
            self.uploads.release(previous)
        return generation

    def start_browser_camera(self):
        from core.browser_camera import BrowserCamera

        generation = self.request_source("browser-webcam")
        with self.lock:
            if self.source_generation != generation:
                raise ValueError("Another source was selected; try again")
            self.browser_camera = BrowserCamera(generation)
            return self.browser_camera.token, generation

    def reset_timeline(self) -> None:
        with self.lock:
            self.timeline.clear()
            self.active_event = None
            self.active_event_id = None
            self.active_events.clear()
            self.notification_events = []

    def record_event(
        self, timestamp: float, scene: str | None, alerts: dict, generation=None
    ):
        observations = []
        fight_scenes = {"FIGHT", "MULTI_PERSON_FIGHT", "GROUP_ASSAULT"}
        for name, data in alerts.items():
            for incident in data.get("incidents") or [data]:
                identities = (
                    incident.get("identities")
                    or incident.get("members")
                    or incident.get("person_ids")
                )
                if identities is None:
                    identities = [
                        incident.get("person_id", incident.get("bag_id", "scene")),
                        incident.get("generation", 0),
                    ]
                event_scene = incident.get(
                    "scene", scene if name == "Violence/Fight" else "THREAT"
                )
                key = (name, repr(identities), event_scene)
                observations.append((key, event_scene, {name: dict(data, **incident)}))
        if scene in fight_scenes and "Violence/Fight" not in alerts:
            observations.append((("fight", "scene", scene), scene, {}))
        with self.lock:
            if generation is not None and generation != self.source_generation:
                return []
            current = {}
            notifications = []
            created = []
            for key, event_scene, event_alerts in observations:
                entry = self.active_events.get(key)
                if entry is None:
                    self.event_counter += 1
                    event_id = (
                        uuid4().hex
                        if self.incident_store is not None
                        else f"{self.source_generation}:{self.event_counter}"
                    )
                    event = {
                        "id": event_id,
                        "start": round(float(timestamp), 3),
                        "end": round(float(timestamp), 3),
                        "scene": event_scene,
                        "alerts": sorted(event_alerts),
                        "observed_at": time.time(),
                    }
                    entry = (event_id, event)
                    self.timeline.append(event)
                    created.append(event)
                entry[1]["end"] = round(float(timestamp), 3)
                confidences = [
                    data.get("confidence")
                    for data in event_alerts.values()
                    if isinstance(data.get("confidence"), (int, float))
                ]
                if confidences:
                    entry[1]["confidence"] = max(confidences)
                current[key] = entry
                notifications.append((entry[0], event_scene, event_alerts))
            del self.timeline[:-200]
            self.active_events = current
            self.notification_events = notifications
            self.active_event_id = notifications[0][0] if notifications else None
            self.active_event = next(iter(current.values()))[1] if current else None
            return [dict(event) for event in created]

    def timeline_snapshot(self) -> list:
        with self.lock:
            return [dict(event) for event in self.timeline]


# --------------------------------------------------------------------------
# frame pump
# --------------------------------------------------------------------------


def _model_status(pipeline: Pipeline) -> dict:
    adapter = pipeline.pair_adapter
    ckpt = getattr(adapter, "checkpoint", None)
    return {
        "ready": bool(adapter.is_ready),
        "head_trained": bool(getattr(adapter, "head_trained", False)),
        "checkpoint": getattr(ckpt, "path", None) or None,
    }


def _alert_details(result_alerts, threat_alerts) -> dict:
    """Name -> {confidence, message} for every live alert (sidebar data)."""
    details = {}
    for source in (result_alerts or {}, threat_alerts or {}):
        for name, data in source.items():
            if not isinstance(data, dict):
                continue
            try:
                conf = data.get("confidence")
                conf = None if conf is None else round(float(conf), 3)
            except (TypeError, ValueError):
                conf = None
            details[str(name)] = {
                "confidence": conf,
                "message": str(data.get("message", "")),
            }
    return details


def _status_from_result(
    pipeline: Pipeline, result, fps: float, source: str, threat_frame=None
) -> dict:
    persons = [
        {
            "id": p.track_id,
            "gen": p.generation,
            "bbox": [round(float(v), 1) for v in p.bbox],
        }
        for p in result.persons
    ]
    pairs = []
    window = float(pipeline.config.fight_cfg.window_seconds)
    for t in pipeline.pair_manager.open_tracks():
        edge = pipeline.graph.edges.get(t.key)
        times = [float(e.get("t", 0.0)) for e in t.buffer] if t.buffer else []
        span = (max(times) - min(times)) if len(times) > 1 else 0.0
        pairs.append(
            {
                "a": t.id_a,
                "b": t.id_b,
                "key": list(t.key),
                "score": (
                    None
                    if edge is None or edge.score is None
                    else round(float(edge.score), 3)
                ),
                "buf": t.frame_count,
                "span": round(span, 2),
                "ready": bool(is_buffer_ready(t, window)),
                "dist": round(
                    float(t.latest_features.get("normalized_center_distance", 0.0)), 2
                ),
            }
        )
    scene = None
    if result.scene is not None:
        scene = {
            "verdict": result.scene.scene.value,
            "incidents": [
                {
                    "members": inc.members,
                    "violent_edges": len(inc.violent_edges),
                    "candidate": inc.candidate.value,
                }
                for inc in result.scene.incidents
            ],
        }
    threat_frame = threat_frame or {}
    threat_detections = threat_frame.get("detections", [])
    threat_alerts = threat_frame.get("alerts", {})
    return {
        "running": True,
        "source": redact_source(source),
        "frame": result.frame_index,
        "ts": round(result.timestamp, 3),
        "fps": round(fps, 1),
        "people": persons,
        "pairs": pairs,
        "scene": scene,
        "fight_status": result.fight_status,
        "fight_window_seconds": window,
        "scenario_errors": result.scenario_errors,
        "model": _model_status(pipeline),
        "alerts": sorted(set(result.alerts) | set(threat_alerts)),
        "alert_details": _alert_details(result.alerts, threat_alerts),
        "threats": threat_detections,
        "threat_models": threat_frame.get("models", {}),
        "diagnostics": result.diagnostics,
    }


def _draw_model_banner(frame, pipeline: Pipeline) -> None:
    ms = _model_status(pipeline)
    if pipeline.fight_status == "ASSESSED":
        text, color = "Fight detection: scene assessed", (0, 200, 0)
    elif ms["ready"] and ms["head_trained"]:
        text, color = "Fight detection: " + pipeline.fight_status.lower(), (0, 200, 255)
    elif ms["ready"]:
        text, color = (
            "Fight detection: not trained - results may be unreliable",
            (0, 200, 255),
        )
    else:
        text, color = "Fight detection: not installed - movement only", (150, 150, 150)
    cv2.putText(frame, text, (10, 22), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 0, 0), 3)
    cv2.putText(frame, text, (10, 22), cv2.FONT_HERSHEY_SIMPLEX, 0.55, color, 1)


def open_capture(source):
    """Open a video source, trying alternate Windows backends for webcams.

    Returns (cap, backend_label) on success, else (None, error_text).
    A successful open is verified with a real frame grab: some laptop
    cameras report "opened" yet deliver nothing usable under the default
    backend, which previously surfaced as garbage frames downstream.
    """
    is_cam = str(source).isdigit()
    src: object = (
        int(source)
        if is_cam
        else (source if "://" in str(source) else resolve_asset(source))
    )
    backends = [None]
    if is_cam:
        backends += [cv2.CAP_DSHOW, cv2.CAP_MSMF]
    tried = []
    for backend in backends:
        label = "default" if backend is None else str(backend)
        tried.append(label)
        try:
            cap = (
                cv2.VideoCapture(src)
                if backend is None
                else cv2.VideoCapture(src, backend)
            )
        except Exception as exc:
            log.warning("event=ui_capture_open_failed backend=%s error=%r", label, exc)
            continue
        if not cap.isOpened():
            cap.release()
            continue
        ok, frame = cap.read()
        if not ok or frame is None or min(frame.shape[:2]) <= 0:
            cap.release()
            log.warning(
                "event=ui_capture_no_frames backend=%s source=%r",
                label,
                redact_source(source),
            )
            continue
        if not is_cam:
            # The probe grab consumed frame 0; reopen so playback starts
            # at the beginning instead of silently dropping the first frame.
            cap.release()
            cap = cv2.VideoCapture(src)
            if not cap.isOpened():
                continue
        log.info(
            "event=ui_capture_open source=%r backend=%s size=%sx%s",
            redact_source(source),
            label,
            frame.shape[1],
            frame.shape[0],
        )
        return cap, label
    return (
        None,
        f"cannot open source {redact_source(source)!r} (tried backends: {', '.join(tried)}; "
        "close other apps using the camera and retry)",
    )


def pump_frames(
    state: LiveState,
    pipeline: Pipeline,
    source,
    fps_fallback: float,
    synthetic_frames=None,
    threat_detector=None,
) -> None:
    from core.visualization import (
        draw_alerts,
        draw_pairs,
        draw_scene_panel,
        draw_threats,
        draw_tracks,
    )

    cap = None
    current_source = str(source)
    current_generation = 0
    fps = float(fps_fallback)
    idx, last, ema = 0, time.perf_counter(), None
    plays, ended = 0, current_source == "idle"
    clock = CaptureClock(False, fps)
    using_synthetic = synthetic_frames is not None
    recorder = state.incident_recorder
    try:
        while not state.stop:
            if recorder is not None:
                recorder.maintain()
            with state.lock:
                requested = state.source_request
            if requested is not None and requested[0] != current_generation:
                old_source = current_source
                current_generation, current_source = requested
                using_synthetic = False
                if recorder is not None:
                    recorder.finish_source()
                if cap is not None:
                    cap.release()
                if old_source != current_source:
                    state.uploads.release(old_source)
                with state.lock:
                    state.active_source = current_source
                cap = None
                pipeline.reset()
                if threat_detector is not None:
                    threat_detector.reset()
                state.reset_timeline()
                idx, ema = 0, None
                plays, ended = 0, False
                with state.lock:
                    state.jpeg = None
                    state.status = {
                        "running": False,
                        "source": redact_source(current_source),
                        "source_generation": current_generation,
                        "timeline": [],
                    }
            browser_timestamp = None
            if current_source == "browser-webcam":
                with state.lock:
                    camera = state.browser_camera
                    captured = camera.take() if camera else None
                    timed_out = (
                        camera is not None and time.monotonic() - camera.received > 10
                    )
                    if timed_out:
                        state.status = dict(
                            state.status,
                            running=False,
                            error="Webcam stopped sending frames",
                        )
                if captured is None:
                    if timed_out and recorder is not None:
                        recorder.finish_source()
                    time.sleep(0.02)
                    continue
                frame, browser_timestamp = captured
            elif current_source == "idle":
                time.sleep(0.1)
                continue
            elif not using_synthetic:
                if cap is None and not ended:
                    cap, open_error = open_capture(current_source)
                    if cap is None:
                        with state.lock:
                            state.status = {
                                "running": False,
                                "source": redact_source(current_source),
                                "source_generation": current_generation,
                                "error": open_error,
                            }
                        if not (
                            str(current_source).isdigit() or "://" in current_source
                        ):
                            ended = True
                        time.sleep(0.1)
                        continue
                    fps = float(cap.get(cv2.CAP_PROP_FPS) or fps_fallback)
                    clock = CaptureClock(
                        str(current_source).isdigit() or "://" in current_source, fps
                    )
                    fps = clock.fps
                    idx = 0
                    pipeline.reset()
                    with state.lock:
                        state.active_source = current_source
                        state.active_event = None
                        state.active_event_id = None
                        state.active_events.clear()
                        state.notification_events = []
                    if threat_detector is not None:
                        threat_detector.reset()
                if cap is None:
                    time.sleep(0.1)
                    continue
                ok, frame = cap.read()
                if not ok:
                    cap.release()
                    cap = None
                    if recorder is not None:
                        recorder.finish_source()
                    if str(current_source).isdigit() or "://" in current_source:
                        timeline = state.timeline_snapshot()
                        with state.lock:
                            state.status = {
                                "running": False,
                                "source": redact_source(current_source),
                                "source_generation": current_generation,
                                "timeline": timeline,
                            }
                        time.sleep(0.1)
                        continue
                    plays += 1
                    if plays >= state.video_plays:
                        ended = True
                        timeline = state.timeline_snapshot()
                        with state.lock:
                            state.status = dict(
                                state.status,
                                running=False,
                                source=redact_source(current_source),
                                timeline=timeline,
                                ended=True,
                            )
                        log.info(
                            "event=ui_replay_finished source=%s plays=%d",
                            redact_source(current_source),
                            plays,
                        )
                    time.sleep(0.1)
                    continue
            else:
                frame = synthetic_frames[idx % len(synthetic_frames)].copy()

            ts = (
                idx / float(fps or fps_fallback)
                if using_synthetic
                else browser_timestamp
                if browser_timestamp is not None
                else clock.timestamp(idx, cap.get(cv2.CAP_PROP_POS_MSEC))
            )
            try:
                result = pipeline.process(frame, timestamp=ts)
            except Exception as exc:
                log.error(
                    "event=ui_frame_error frame=%d error=%s", idx, type(exc).__name__
                )
                with state.lock:
                    state.status = dict(
                        state.status,
                        running=False,
                        fight_status="ERROR",
                        error="frame processing failed",
                    )
                    state.jpeg = None
                idx += 1
                time.sleep(0.1)
                continue

            threat_detections = getattr(result, "threat_detections", [])
            threat_data = {
                "detections": [d.to_dict() for d in threat_detections],
                "alerts": {},
                "models": getattr(result, "threat_models", {}),
            }
            if threat_detector is not None:
                threat_frame = threat_detector.detect(frame, result.persons, ts)
                threat_detections = threat_frame.detections
                threat_data = {
                    "detections": [d.to_dict() for d in threat_frame.detections],
                    "alerts": threat_frame.alerts,
                    "models": threat_frame.models,
                }
            alerts = dict(result.alerts)
            alerts.update(threat_data["alerts"])
            scene_name = result.scene.scene.value if result.scene is not None else None
            with state.lock:
                if current_generation != state.source_generation:
                    continue
            incidents = state.record_event(ts, scene_name, alerts, current_generation)
            demo = getattr(state, "demo_recipient_state", None)
            if demo is not None:
                for event in incidents:
                    try:
                        demo.incident_store.detection_notice(event)
                        demo.phone_push.wake.set()
                    except sqlite3.Error:
                        log.exception("event=public_notice_save_failed")
            if state.incident_store is not None and recorder is None:
                for event in incidents:
                    try:
                        state.incident_store.record(
                            event, incident_source(current_source)
                        )
                    except sqlite3.Error:
                        log.exception("event=incident_save_failed id=%s", event["id"])
            persons_by_id = {p.track_id: p for p in result.persons}
            draw_tracks(frame, result.persons, plain=True)
            draw_threats(frame, threat_detections, plain=True)
            draw_pairs(
                frame,
                pipeline.pair_manager.open_tracks(),
                persons_by_id,
                pipeline.graph,
                plain=True,
            )
            if result.scene is not None:
                draw_scene_panel(frame, result.scene, pipeline.graph, plain=True)
            draw_alerts(frame, alerts, plain=True)
            _draw_model_banner(frame, pipeline)
            ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 80])
            jpeg = bytes(buf) if ok else None
            if recorder is not None:
                evidence_jpeg = None
                if jpeg and (incidents or recorder.wants_frame(ts)):
                    height, width = frame.shape[:2]
                    scale = min(720 / width, 540 / height, 1)
                    if scale < 1 or len(jpeg) > MAX_FRAME_BYTES:
                        size = (max(2, round(width * scale)), max(2, round(height * scale)))
                        evidence_frame = (
                            cv2.resize(frame, size, interpolation=cv2.INTER_AREA)
                            if scale < 1 else frame
                        )
                        encoded, smaller = cv2.imencode(
                            ".jpg", evidence_frame, [cv2.IMWRITE_JPEG_QUALITY, 70]
                        )
                        evidence_jpeg = bytes(smaller) if encoded else None
                    else:
                        evidence_jpeg = jpeg
                recorder.ingest(
                    ts, evidence_jpeg, incidents, incident_source(current_source)
                )
            if not ok:
                idx += 1
                continue
            now = time.perf_counter()
            ema = (now - last) if ema is None else 0.9 * ema + 0.1 * (now - last)
            last = now
            timeline = state.timeline_snapshot()
            with state.lock:
                if current_generation != state.source_generation:
                    continue
                state.jpeg = jpeg
                state.frame_updated_at = time.monotonic()
                state.status = _status_from_result(
                    pipeline,
                    result,
                    (1.0 / ema) if ema else 0.0,
                    current_source,
                    dict(threat_data, alerts=alerts),
                )
                state.status["timeline"] = timeline
                state.status["running"] = True
                state.status["source_generation"] = current_generation
                state.status["active_event_ids"] = [
                    item[0] for item in state.notification_events
                ]
            idx += 1
            if using_synthetic:
                time.sleep(1 / max(fps, 1))
    finally:
        if cap is not None:
            cap.release()
        if recorder is not None:
            recorder.finish_source()
        with state.lock:
            state.status = dict(state.status, running=False)
        log.info("event=ui_pump_end frames=%d", idx)


# --------------------------------------------------------------------------
# HTTP layer
# --------------------------------------------------------------------------


def parse_args(argv=None):
    p = argparse.ArgumentParser(description="VIGLANCE web UI (MJPEG + dashboard)")
    p.add_argument("--source", default=None, help="webcam index, video file, or stream")
    p.add_argument(
        "--allow-source",
        action="append",
        default=[],
        help="additional source allowed for dashboard switching",
    )
    p.add_argument("--config", default=None)
    p.add_argument("--device", type=cuda_device_name, default=None)
    p.add_argument("--port", type=int, default=8000)
    p.add_argument("--site-name", default="Campus", help="location on incident alerts")
    p.add_argument(
        "--camera-name", default="Primary camera", help="camera label on incidents"
    )
    p.add_argument(
        "--recipient-origin",
        help="HTTPS origin for the recipient app",
    )
    p.add_argument(
        "--recipient-port",
        type=int,
        help="separate recipient-only port, on loopback unless TLS is configured",
    )
    p.add_argument("--recipient-cert", help="PEM certificate for LAN recipient HTTPS")
    p.add_argument("--recipient-key", help="PEM private key for LAN recipient HTTPS")
    p.add_argument("--public-demo-origin", help="HTTPS origin for the test-only public recipient app")
    p.add_argument("--public-demo-directory", type=Path, help="Existing isolated demo incident directory")
    p.add_argument(
        "--public-recipient-evidence", action="store_true",
        help="Allow phones joining the public app to view matching incident clips and photos without email login",
    )
    p.add_argument(
        "--push-subject",
        default=os.environ.get("VIGILANCE_PUSH_SUBJECT"),
        help="VAPID contact URI (mailto: or HTTPS) for Web Push",
    )
    p.add_argument(
        "--emergency-number",
        help="optional local emergency number shown as a call link",
    )
    p.add_argument(
        "--frontend-origin",
        action="append",
        default=[],
        help="exact allowed HTTPS frontend origin (repeatable)",
    )
    p.add_argument(
        "--judge-mode",
        action="store_true",
        help="shared judge demo: permit browser video stream access without login",
    )
    p.add_argument(
        "--require-gpu",
        action="store_true",
        help="fail startup unless CUDA and a trained fight model are available",
    )
    p.add_argument(
        "--video-plays",
        type=int,
        default=MAX_VIDEO_PLAYS,
        help="number of plays per uploaded video",
    )
    p.add_argument(
        "--host",
        default="127.0.0.1",
        help="bind address; use 0.0.0.0 only behind an HTTPS reverse proxy",
    )
    p.add_argument(
        "--public-origin",
        default=None,
        help="exact HTTPS origin used by the trusted reverse proxy, e.g. https://demo.example",
    )
    p.add_argument(
        "--synthetic",
        action="store_true",
        help="scripted demo persons; no camera or YOLO weights needed",
    )
    p.add_argument(
        "--fight-checkpoint",
        default=None,
        help="post-training plug-in: ST-GCN++ fight checkpoint (.pth)",
    )
    p.add_argument("--no-fight", action="store_true")
    p.add_argument(
        "--no-threats",
        action="store_true",
        help="disable the auxiliary YOLO threat model",
    )
    p.add_argument(
        "--no-fire",
        action="store_true",
        help="disable fire/smoke alerts from the threat model",
    )
    p.add_argument(
        "--threats",
        action="store_true",
        help="enable auxiliary weapon alerts, including knives",
    )
    p.add_argument(
        "--enable-weapon-alerts",
        action="store_true",
        help="enable weapon alerts, including knives (enabled in the default config)",
    )
    p.add_argument(
        "--no-threat-download",
        action="store_true",
        help="use only the local auxiliary YOLO weights",
    )
    p.add_argument(
        "--surveillance-weights",
        default=None,
        help="local weights for the surveillance YOLO model",
    )
    p.add_argument("--log-level", default=None)
    return p.parse_args(argv)


def build_config(args) -> VigilanceConfig:
    """Resolve startup flags before opening models, cameras, or notifications."""
    cfg = VigilanceConfig()
    if args.config:
        cfg = VigilanceConfig.from_file(args.config)
    cfg.from_env()
    cfg.apply_overrides(
        source=None if args.synthetic else args.source,
        device=args.device,
        log_level=args.log_level,
    )
    if args.no_fight:
        cfg.enable_fight = False
    cfg.enable_fire = cfg.enable_fire and not (
        args.no_fire or args.no_threats or args.synthetic
    )
    cfg.enable_threats = (args.threats or args.enable_weapon_alerts or cfg.enable_threats) and not (
        args.no_threats or args.synthetic
    )
    if args.fight_checkpoint:
        cfg.fight_cfg.model.checkpoint = args.fight_checkpoint
        cfg.fight_cfg.model.autoload = False
    if args.synthetic:
        cfg.fight_cfg.model.autoload = False
    cfg.validate()
    if args.require_gpu:
        if args.synthetic:
            raise ValueError("--require-gpu requires model mode and a CUDA device")
        cfg.device = require_cuda(cfg.device)
        cfg.fight_cfg.model.device = require_cuda(
            cfg.device
            if cfg.fight_cfg.model.device == "auto"
            else cfg.fight_cfg.model.device
        )
    return cfg


def build_threat_detector(cfg, args):
    if not (cfg.enable_threats or cfg.enable_fire):
        return None
    from core.threat_detection import SurveillanceThreatDetector

    return SurveillanceThreatDetector(
        surveillance_weights=args.surveillance_weights,
        download_missing=not args.no_threat_download,
        conf=cfg.conf,
        imgsz=cfg.imgsz,
        device=cfg.device,
        bag_proximity_px=cfg.bag_proximity_px,
        enable_weapon_alerts=cfg.enable_threats,
        enable_fire_alerts=cfg.enable_fire,
        enable_bag_alerts=False,
    )


def main(argv=None) -> int:
    args = parse_args(argv)
    cfg = build_config(args)
    setup_logging(cfg.log_level)

    public_origin = None
    allowed_hosts = None
    allowed_origins = None
    secure_cookies = False
    if args.public_origin:
        parsed_origin = urlsplit(args.public_origin)
        if (
            parsed_origin.scheme != "https"
            or not parsed_origin.netloc
            or parsed_origin.username
            or parsed_origin.password
            or parsed_origin.path
            or parsed_origin.query
            or parsed_origin.fragment
        ):
            print("--public-origin must be an HTTPS origin without a path")
            return 2
        public_origin = f"https://{parsed_origin.netloc.lower()}"
        allowed_hosts = {parsed_origin.netloc.lower()}
        allowed_origins = {public_origin}
        secure_cookies = True
    elif args.host not in {"127.0.0.1", "localhost", "::1"}:
        print("Binding outside loopback requires --public-origin with HTTPS")
        return 2

    phone_origin = None
    demo_directory = None
    if args.public_demo_origin or args.public_demo_directory or args.public_recipient_evidence:
        import re

        demo_directory = args.public_demo_directory.resolve() if args.public_demo_directory else None
        if (
            not re.fullmatch(r"https://[a-z0-9-]+\.trycloudflare\.com", args.public_demo_origin or "")
            or args.synthetic or 8789 in {args.port, args.recipient_port}
            or demo_directory is None
            or demo_directory.parent != Path("tmp").resolve()
            or not demo_directory.name.startswith("public-demo-")
            or not (demo_directory / "incidents.sqlite3").is_file()
        ):
            print("Public demo requires its isolated tmp/public-demo directory, a Cloudflare origin, and port 8789.")
            return 2
        with sqlite3.connect(demo_directory / "incidents.sqlite3") as db:
            if db.execute(
                """SELECT 1 FROM incidents WHERE test=0 AND
                   (source!='Detection notification' OR clip!='unavailable') LIMIT 1"""
            ).fetchone() or any((demo_directory / "incident-media").glob("*")):
                print("The public phone database must contain only test alerts and basic notices.")
                return 2
    if args.emergency_number and not (
        args.emergency_number.isascii()
        and args.emergency_number.isdecimal()
        and 2 <= len(args.emergency_number) <= 8
    ):
        print("--emergency-number must be a local number of 2 to 8 digits")
        return 2
    if args.recipient_origin:
        parsed = urlsplit(args.recipient_origin)
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username is not None
            or parsed.path
            or parsed.query
            or parsed.fragment
        ):
            print("--recipient-origin must be an HTTPS origin without a path")
            return 2
        phone_origin = "https://" + parsed.netloc.lower()
        if phone_origin == public_origin or phone_origin in args.frontend_origin:
            print("--recipient-origin must use a distinct host from dashboard origins")
            return 2
        if phone_origin == args.public_demo_origin:
            print("Live recipients and the public demo require different HTTPS origins.")
            return 2
    if args.recipient_port is not None and (
        not phone_origin
        or args.synthetic
        or args.recipient_port == args.port
        or not 1 <= args.recipient_port <= 65535
    ):
        print(
            "--recipient-port requires an HTTPS recipient origin and a separate port in model mode"
        )
        return 2
    phone_host = "127.0.0.1"
    phone_tls = None
    if args.recipient_cert or args.recipient_key:
        if not (args.recipient_cert and args.recipient_key and args.recipient_port):
            print("Direct recipient HTTPS requires --recipient-port, --recipient-cert and --recipient-key")
            return 2
        origin = urlsplit(phone_origin)
        try:
            address = IPv4Address(origin.hostname)
            if origin.port != args.recipient_port or not any(
                address in IPv4Network(network)
                for network in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16")
            ):
                raise ValueError("Use a private LAN IPv4 address and the recipient port in --recipient-origin.")
            phone_tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            phone_tls.minimum_version = ssl.TLSVersion.TLSv1_2
            phone_tls.load_cert_chain(args.recipient_cert, args.recipient_key)
            phone_host = str(address)
        except (ValueError, OSError) as exc:
            print(f"Could not configure recipient HTTPS: {exc}")
            return 2

    from core.demo import ScriptedDetector, black_frames

    if args.synthetic:
        script = [
            [(1, 60.0), (2, 220.0)],
            [(1, 64.0), (2, 216.0), (3, 380.0)],
            [(1, 68.0), (3, 376.0)],
            [(1, 72.0), (2, 208.0), (3, 372.0)],
        ] * 25
        detector, frames = (
            ScriptedDetector(script),
            black_frames(len(script), h=480, w=640),
        )
        source_label = "synthetic"
    else:
        from core.detector import Detector  # real YOLO; raises informatively if missing

        try:
            detector = Detector(
                pose_model_path=cfg.pose_weights,
                det_model_path=cfg.det_weights,
                tracker=cfg.tracker,
                fallback_tracker=cfg.tracker_fallback,
                conf=cfg.conf,
                imgsz=cfg.imgsz,
                device=cfg.device,
                max_people=cfg.max_people,
                load_object_model=cfg.enable_bag,
            )
        except Exception as exc:
            log.error("event=ui_detector_failed error=%r (try --synthetic)", exc)
            print(f"Detector failed: {exc}\nTry: python ui.py --synthetic")
            return 2
        frames, source_label = None, cfg.source

    pipeline = Pipeline(config=cfg, detector=detector)
    if args.fight_checkpoint and cfg.enable_fight and not args.synthetic:
        loaded = pipeline.load_fight_model(args.fight_checkpoint)
        if loaded is None:
            log.warning("event=ui_no_fight_model (continuing without scores)")
    elif (
        cfg.enable_fight
        and not args.synthetic
        and Path(resolve_asset(cfg.fight_cfg.model.checkpoint)).exists()
        and not pipeline.pair_adapter.is_ready
    ):
        pipeline.load_fight_model()
    if args.require_gpu and cfg.enable_fight and not pipeline.pair_adapter.head_trained:
        raise RuntimeError("GPU deployment requires the trained fight checkpoint")

    threat_detector = build_threat_detector(cfg, args)
    # All entrypoints now use core-owned auxiliary detections and health.
    pipeline.threat_detector = threat_detector
    for origin in args.frontend_origin:
        parsed = urlsplit(origin)
        if (
            parsed.scheme != "https"
            or not parsed.netloc
            or parsed.path
            or parsed.query
            or parsed.fragment
            or parsed.username
            or parsed.password
        ):
            raise ValueError("--frontend-origin must be an exact HTTPS origin")
        if allowed_origins is None:
            raise ValueError("--frontend-origin requires --public-origin")
        allowed_origins.add(origin)
    if args.video_plays < 1:
        raise ValueError("--video-plays must be >= 1")

    state = LiveState(
        allowed_sources=[cfg.source, *args.allow_source],
        inference_mode="synthetic" if args.synthetic else "model",
        allowed_hosts=allowed_hosts,
        allowed_origins=allowed_origins,
        secure_cookies=secure_cookies,
        judge_mode=args.judge_mode,
        video_plays=args.video_plays,
        incident_store=None
        if args.synthetic
        else IncidentStore(
            cfg.evidence_dir, site=args.site_name, camera=args.camera_name
        ),
        phone_origin=phone_origin,
        emergency_number=args.emergency_number,
    )
    if state.incident_store is not None:
        try:
            from imageio_ffmpeg import get_ffmpeg_exe

            state.incident_recorder = IncidentRecorder(
                state.incident_store, get_ffmpeg_exe()
            )
        except (ImportError, RuntimeError):
            log.warning("event=incident_encoder_unavailable install=imageio-ffmpeg")
        if args.push_subject:
            from core.phone_push import PhonePush

            state.phone_push = PhonePush(state.phone_recipients, args.push_subject)
    state.model_status = _model_status(pipeline)
    pump = threading.Thread(
        target=pump_frames,
        args=(
            state,
            pipeline,
            cfg.source if frames is None else source_label,
            cfg.fps,
            frames,
            None,
        ),
        daemon=True,
    )
    server = BoundedHTTPServer((args.host, args.port), make_handler(state))
    demo_server = None
    if demo_directory is not None:
        from core.phone_push import PhonePush

        demo = LiveState(
            inference_mode="synthetic",
            incident_store=IncidentStore(demo_directory, site="RakshaNetra demo", camera="Test alerts"),
            phone_origin=args.public_demo_origin,
        )
        demo.public_demo = True
        demo.evidence_state = state
        demo.public_recipient_evidence = args.public_recipient_evidence
        demo.phone_push = PhonePush(demo.phone_recipients, args.public_demo_origin)
        state.demo_recipient_state = demo
        demo_server = BoundedHTTPServer(("127.0.0.1", 8789), make_handler(demo, recipient_only=True))
        threading.Thread(
            target=demo_server.serve_forever, daemon=True, name="demo-recipient-http"
        ).start()
        demo.phone_push.start()
    phone_server = None
    if args.recipient_port is not None:
        try:
            phone_server = BoundedHTTPServer(
                (phone_host, args.recipient_port),
                make_handler(state, recipient_only=True),
                tls_context=phone_tls,
            )
        except OSError:
            server.server_close()
            raise
        threading.Thread(
            target=phone_server.serve_forever, daemon=True, name="recipient-http"
        ).start()
    if state.phone_push is not None:
        state.phone_push.start()
    pump.start()
    display_host = f"[{args.host}]" if ":" in args.host else args.host
    display_origin = public_origin or f"http://{display_host}:{args.port}"
    log.info(
        "event=ui_ready url=%s source=%s",
        display_origin,
        redact_source(source_label),
    )
    print(f"VIGLANCE UI: {display_origin}  (Ctrl+C to stop)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        state.stop = True
        if demo_server is not None:
            demo_server.shutdown()
            demo_server.server_close()
            state.demo_recipient_state.phone_push.close()
            state.demo_recipient_state.uploads.close()
        if phone_server is not None:
            phone_server.shutdown()
            phone_server.server_close()
        server.shutdown()
        server.server_close()
        pump.join(timeout=5)
        if state.incident_recorder is not None and not pump.is_alive():
            state.incident_recorder.close()
        if state.phone_push is not None:
            state.phone_push.close()
        state.uploads.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
