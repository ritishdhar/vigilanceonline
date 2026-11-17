"""Loopback dashboard server with session authentication and resource bounds."""

from http import cookies
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
import secrets
import socket
import sqlite3
import threading
import time
from urllib.parse import unquote

from core.paths import ROOT
from core.incidents import INCIDENT_ID, INCIDENT_STILLS
from core.phone_http import PhoneRoutes
from core.source_utils import authorize_source, redact_source
from core.video_upload import MAX_UPLOAD_BYTES, VideoUploadError


class BoundedHTTPServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, server_address, *args, max_connections=16, **kwargs):
        self._slots = threading.BoundedSemaphore(max_connections)
        self.tls_context = kwargs.pop("tls_context", None)
        if ":" in server_address[0]:
            self.address_family = socket.AF_INET6
        super().__init__(server_address, *args, **kwargs)

    def process_request(self, request, address):
        if not self._slots.acquire(blocking=False):
            try:
                request.sendall(b"HTTP/1.0 503 Busy\r\nContent-Length: 0\r\n\r\n")
            finally:
                self.shutdown_request(request)
            return
        try:
            super().process_request(request, address)
        except Exception:
            self._slots.release()
            raise

    def process_request_thread(self, request, address):
        try:
            if self.tls_context is not None:
                try:
                    request.settimeout(5)
                    request = self.tls_context.wrap_socket(request, server_side=True)
                except OSError:
                    self.close_request(request)
                    return
            super().process_request_thread(request, address)
        finally:
            self._slots.release()


def make_handler(state, *, recipient_only=False):
    class Handler(PhoneRoutes, BaseHTTPRequestHandler):
        server_version = "ViglanceUI/2.0"
        timeout = 15
        header_timeout = 10

        def setup(self):
            super().setup()

            def expire():
                try:
                    self.connection.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass

            self._header_timer = threading.Timer(self.header_timeout, expire)
            self._header_timer.daemon = True
            self._header_timer.start()

        def parse_request(self):
            try:
                if not super().parse_request():
                    return False
                if (
                    recipient_only
                    and self.headers.get("Host", "").lower() != state.phone_host
                ):
                    return self._deny("Recipient host required")
                return True
            finally:
                self._header_timer.cancel()

        def finish(self):
            self._header_timer.cancel()
            super().finish()

        def _cors_headers(self):
            origin = self.headers.get("Origin")
            if origin and origin in (state.allowed_origins or set()):
                self.send_header("Access-Control-Allow-Origin", origin)
                self.send_header("Access-Control-Allow-Credentials", "true")
                self.send_header("Vary", "Origin")

        def _send(self, code, ctype, body, extra_headers=()):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("Content-Security-Policy", "frame-ancestors 'none'")
            self._cors_headers()
            if self.path == "/" and code == 200:
                secure = "; Secure" if state.secure_cookies else ""
                self.send_header(
                    "Set-Cookie",
                    f"vigilance_session={state.token}; HttpOnly; SameSite=Strict; Path=/{secure}",
                )
            for name, value in extra_headers:
                self.send_header(name, value)
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        def _send_json(self, payload, code=200, extra_headers=()):
            self._send(
                code,
                "application/json",
                json.dumps(payload, allow_nan=False).encode(),
                extra_headers,
            )
            if code >= 400:
                self._discard_small_body()

        def _discard_small_body(self):
            # Avoid a TCP reset hiding the response when a small POST body
            # was sent alongside rejected headers. Never drain large bodies.
            try:
                remaining = getattr(
                    self,
                    "_body_remaining",
                    int(self.headers.get("Content-Length", "0")),
                )
                deadline = time.monotonic() + 0.5
                if self.command == "POST" and 0 < remaining <= 4096:
                    while remaining and time.monotonic() < deadline:
                        self.connection.settimeout(
                            max(0.01, deadline - time.monotonic())
                        )
                        chunk = self.rfile.read1(remaining)
                        if not chunk:
                            break
                        remaining -= len(chunk)
            except (OSError, ValueError):
                pass

        def _deny(self, message):
            self._send_json({"error": message}, 403)
            return False

        def _allowed(self, authenticated=True):
            port = self.server.server_address[1]
            hosts = state.allowed_hosts or {
                f"127.0.0.1:{port}",
                f"localhost:{port}",
                f"[::1]:{port}",
            }
            origins = state.allowed_origins or {f"http://{host}" for host in hosts}
            host = self.headers.get("Host", "").lower()
            origin = self.headers.get("Origin")
            if host not in hosts or (origin is not None and origin not in origins):
                return self._deny("host or origin is not allowed")
            if not authenticated:
                return True
            token = self.headers.get("X-Vigilance-Token", "")
            if self.command in {"GET", "HEAD"} and not token:
                jar = cookies.SimpleCookie()
                try:
                    jar.load(self.headers.get("Cookie", ""))
                    token = (
                        jar["vigilance_session"].value
                        if "vigilance_session" in jar
                        else ""
                    )
                except cookies.CookieError:
                    pass
            try:
                authenticated = secrets.compare_digest(
                    token.encode("ascii"), state.token.encode("ascii")
                )
            except UnicodeEncodeError:
                authenticated = False
            if not authenticated:
                return self._deny("session token required")
            return True

        def _read_json(self, maximum=4096):
            body = self._read_body(maximum)
            payload = json.loads(body or b"{}")
            if not isinstance(payload, dict):
                raise ValueError("JSON object required")
            return payload

        def _read_body(self, maximum):
            try:
                length = int(self.headers.get("Content-Length", "-1"))
            except ValueError as exc:
                raise ValueError("invalid content length") from exc
            if not 0 <= length <= maximum or self.headers.get("Transfer-Encoding"):
                raise ValueError("invalid request length")
            self._body_remaining = length
            deadline = time.monotonic() + 10
            body = bytearray()
            while len(body) < length:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise ValueError("request deadline exceeded")
                self.connection.settimeout(min(self.timeout, remaining))
                chunk = self.rfile.read1(length - len(body))
                if not chunk:
                    break
                body.extend(chunk)
                self._body_remaining -= len(chunk)
            if len(body) != length:
                raise ValueError("incomplete request body")
            return body

        def _serve_evidence(self, path, content_type):
            try:
                handle = path.open("rb")
            except FileNotFoundError:
                self._send_json({"error": "Evidence unavailable"}, 404)
                return
            with handle:
                size = os.fstat(handle.fileno()).st_size
                start, end = 0, size - 1
                header = self.headers.get("Range")
                if header:
                    match = re.fullmatch(r"bytes=([0-9]{0,16})-([0-9]{0,16})", header)
                    if match and match[1]:
                        start = int(match[1])
                        if match[2]:
                            end = min(end, int(match[2]))
                    elif match and match[2]:
                        start = max(0, size - int(match[2]))
                    else:
                        start = size
                    if start > end:
                        self._send(
                            416,
                            "text/plain",
                            b"",
                            (("Content-Range", f"bytes */{size}"),),
                        )
                        return
                remaining = end - start + 1
                self.send_response(206 if header else 200)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(remaining))
                self.send_header("Accept-Ranges", "bytes")
                self.send_header("Cache-Control", "private, no-store")
                self.send_header("X-Content-Type-Options", "nosniff")
                if header:
                    self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
                self._cors_headers()
                self.end_headers()
                if self.command == "HEAD":
                    return
                handle.seek(start)
                try:
                    while remaining and (chunk := handle.read(min(65536, remaining))):
                        self.wfile.write(chunk)
                        remaining -= len(chunk)
                except OSError:
                    pass

        def _incident_media(self, store, incident_id, name):
            if name in INCIDENT_STILLS:
                self._serve_evidence(
                    store.media(incident_id, INCIDENT_STILLS[name]), "image/jpeg"
                )
                return True
            if name == "clip.mp4":
                self._serve_evidence(store.media(incident_id, ".mp4"), "video/mp4")
                return True
            return False

        def _incident_api(self):
            if self.path != "/api/incidents" and not self.path.startswith(
                "/api/incidents/"
            ):
                return False
            if not self._allowed():
                return True
            store = state.incident_store
            if store is None:
                self._send_json({"error": "Incident history is unavailable"}, 503)
                return True
            parts = self.path.split("/")
            try:
                if self.command == "GET" and len(parts) == 3:
                    self._send_json({"incidents": store.list()})
                elif len(parts) >= 4 and INCIDENT_ID.fullmatch(parts[3]):
                    incident_id = parts[3]
                    if (
                        self.command in {"GET", "HEAD"}
                        and len(parts) == 5
                        and self._incident_media(store, incident_id, parts[4])
                    ):
                        return True
                    if self.command == "GET" and len(parts) == 4:
                        incident = store.get(incident_id)
                        if incident and state.phone_recipients:
                            incident["delivery"] = (
                                state.phone_recipients.delivery_summary(incident_id)
                            )
                        self._send_json(
                            incident or {"error": "Incident not found"},
                            200 if incident else 404,
                        )
                    elif self.command == "POST" and parts[4:] == ["review"]:
                        data = self._read_json()
                        saved = store.review(
                            incident_id,
                            data.get("action"),
                            "Operator",
                            data.get("note", ""),
                        )
                        self._send_json(
                            {"ok": True} if saved else {"error": "Incident not found"},
                            200 if saved else 404,
                        )
                    else:
                        self._send_json({"error": "Not found"}, 404)
                else:
                    self._send_json({"error": "Not found"}, 404)
            except ValueError as exc:
                self._send_json({"error": str(exc)}, 400)
            except sqlite3.Error:
                self._send_json(
                    {"error": "Incident storage is temporarily unavailable"}, 503
                )
            return True

        def do_GET(self):
            if self.phone_request(state):
                return
            if self.path == "/healthz":
                self._send_json({"ok": True, "inference_mode": state.inference_mode})
                return
            if self._incident_api():
                return
            public = (
                self.path
                in {
                    "/",
                    "/api/session",
                    "/assets/dashboard.js",
                    "/assets/dashboard.css",
                    "/assets/runtime-config.js",
                }
                or self.path.startswith("/api/demos/download/")
                or (state.judge_mode and self.path in {"/stream.mjpg", "/snapshot.jpg"})
            )
            if not self._allowed(authenticated=not public):
                return
            if self.path == "/api/session":
                self._send_json(
                    {
                        "token": state.token,
                        "judge_mode": state.judge_mode,
                        "max_upload_bytes": MAX_UPLOAD_BYTES,
                    }
                )
            elif self.path == "/":
                html = (ROOT / "web/dashboard.html").read_text(encoding="utf-8")
                self._send(
                    200,
                    "text/html; charset=utf-8",
                    html.replace("__VIGILANCE_TOKEN__", state.token).encode(),
                )
            elif self.path.startswith("/assets/") and public:
                path = ROOT / "web" / Path(self.path).name
                ctype = "text/javascript" if path.suffix == ".js" else "text/css"
                self._send(200, ctype, path.read_bytes())
            elif self.path == "/api/capabilities":
                from core.demo_catalog import demo_catalog

                self._send_json(
                    {
                        "inference_mode": state.inference_mode,
                        "model": state.model_status,
                        "max_active_sources": 1,
                        "max_upload_bytes": MAX_UPLOAD_BYTES,
                        "browser_webcam": state.inference_mode == "model",
                        "samples": demo_catalog(),
                        "streams": [
                            {"id": str(i), "label": redact_source(source)}
                            for i, source in enumerate(sorted(state.allowed_sources))
                            if "://" in source
                        ],
                    }
                )
            elif self.path.startswith("/api/demos/download/"):
                from core.demo_catalog import demo_files

                name = unquote(self.path.removeprefix("/api/demos/download/"))
                path = demo_files().get(name)
                if path is None:
                    self._send_json({"error": "Demo video not found"}, 404)
                    return
                with path.open("rb") as sample:
                    self.send_response(200)
                    self.send_header("Content-Type", "application/octet-stream")
                    self.send_header("Content-Length", str(path.stat().st_size))
                    self.send_header(
                        "Content-Disposition", "attachment; filename=demo" + path.suffix
                    )
                    self._cors_headers()
                    self.end_headers()
                    try:
                        while chunk := sample.read(65536):
                            self.wfile.write(chunk)
                    except OSError:
                        pass
            elif self.path == "/api/status":
                with state.lock:
                    payload = dict(state.status)
                    last_frame = state.frame_updated_at
                payload["inference_mode"] = state.inference_mode
                if state.incident_store is not None:
                    payload["incident_history"] = True
                payload.setdefault("model", state.model_status)
                payload.setdefault("source_generation", 0)
                if payload.get("running") and time.monotonic() - last_frame > 10:
                    payload.update(
                        running=False,
                        fight_status="ERROR",
                        error="frame processing stalled",
                    )
                self._send_json(payload)
            elif self.path == "/snapshot.jpg":
                with state.lock:
                    jpeg = state.jpeg
                self._send(
                    200 if jpeg else 503,
                    "image/jpeg" if jpeg else "text/plain",
                    jpeg or b"no frame yet",
                )
            elif self.path == "/stream.mjpg":
                if not state.stream_slots.acquire(blocking=False):
                    self._send_json({"error": "stream connection limit reached"}, 503)
                    return
                try:
                    self.send_response(200)
                    self.send_header(
                        "Content-Type", "multipart/x-mixed-replace; boundary=frame"
                    )
                    self.send_header("Cache-Control", "no-store")
                    self._cors_headers()
                    self.end_headers()
                    while not state.stop:
                        with state.lock:
                            jpeg = state.jpeg
                        if jpeg:
                            self.wfile.write(
                                b"--frame\r\nContent-Type: image/jpeg\r\n\r\n"
                                + jpeg
                                + b"\r\n"
                            )
                        time.sleep(0.04)
                except (OSError, TimeoutError):
                    pass
                finally:
                    state.stream_slots.release()
            else:
                self._send_json({"error": "not found"}, 404)

        def do_POST(self):
            if self.phone_request(state):
                return
            if self._incident_api():
                return
            if not self._allowed():
                return
            if state.inference_mode == "synthetic" and (
                self.path in {"/api/source", "/api/upload", "/api/demo"}
                or self.path.startswith("/api/webcam/")
            ):
                self._send_json(
                    {
                        "error": "Restart in model mode to analyze uploaded or live video"
                    },
                    409,
                )
                return
            if self.path.startswith("/api/webcam/"):
                self._webcam_api()
                return
            if self.path not in {"/api/source", "/api/upload", "/api/demo"}:
                self._send_json({"error": "not found"}, 404)
                return
            try:
                length = int(self.headers.get("Content-Length", "-1"))
                self._body_remaining = length
                limit = MAX_UPLOAD_BYTES if self.path == "/api/upload" else 4096
                if not 0 <= length <= limit or self.headers.get("Transfer-Encoding"):
                    raise VideoUploadError(
                        "invalid request length", 413 if length > limit else 400
                    )
                deadline = time.monotonic() + (90 if self.path == "/api/upload" else 10)
                handler = self

                class Body:
                    def read(self, size):
                        remaining_time = deadline - time.monotonic()
                        if remaining_time <= 0:
                            raise TimeoutError("request deadline exceeded")
                        handler.connection.settimeout(min(15, remaining_time))
                        chunk = handler.rfile.read1(size)
                        handler._body_remaining -= len(chunk)
                        return chunk

                stream = Body()
                content_type = self.headers.get("Content-Type", "")
                if self.path in {"/api/source", "/api/demo"}:
                    if (
                        content_type.split(";", 1)[0].strip().lower()
                        != "application/json"
                    ):
                        raise VideoUploadError("application/json required", 415)
                    body = bytearray()
                    while len(body) < length:
                        chunk = stream.read(length - len(body))
                        if not chunk:
                            raise VideoUploadError("incomplete request")
                        body.extend(chunk)
                    payload = json.loads(body or b"{}")
                    if self.path == "/api/demo":
                        from core.demo_catalog import demo_files

                        sample = (
                            demo_files().get(payload.get("sample"))
                            if isinstance(payload, dict)
                            and isinstance(payload.get("sample"), str)
                            else None
                        )
                        if sample is None:
                            raise VideoUploadError("Demo video not found", 404)
                        generation = state.request_source(str(sample))
                        self._send_json(
                            {
                                "ok": True,
                                "source": str(sample),
                                "source_generation": generation,
                            }
                        )
                        return
                    if isinstance(payload, dict) and "stream_id" in payload:
                        streams = {
                            str(i): source
                            for i, source in enumerate(sorted(state.allowed_sources))
                            if "://" in source
                        }
                        stream_id = payload["stream_id"]
                        if not isinstance(stream_id, str) or stream_id not in streams:
                            raise VideoUploadError("Camera is not configured")
                        payload["source"] = streams[stream_id]
                    if not isinstance(payload, dict) or not isinstance(
                        payload.get("source"), (str, int)
                    ):
                        raise VideoUploadError(
                            "source must be a string or camera index"
                        )
                    source = authorize_source(
                        payload["source"], state.uploads.path, state.allowed_sources
                    )
                else:
                    source = str(state.uploads.save(stream, content_type, length))
                generation = state.request_source(source)
                self._send_json(
                    {
                        "ok": True,
                        "message": "source requested",
                        "source": redact_source(source),
                        "source_generation": generation,
                    }
                )
            except VideoUploadError as exc:
                self._send_json({"error": str(exc)}, exc.status_code)
            except (ValueError, TypeError):
                self._send_json(
                    {"error": "invalid or unauthorized source request"}, 400
                )
            except (OSError, TimeoutError):
                self._send_json(
                    {"error": "request timed out or storage unavailable"}, 408
                )

        def _webcam_api(self):
            from core.browser_camera import MAX_FRAME_BYTES, decode_frame

            try:
                if self.path == "/api/webcam/start":
                    self._read_json()
                    token, generation = state.start_browser_camera()
                    self._send_json(
                        {
                            "ok": True,
                            "camera_token": token,
                            "source": "browser-webcam",
                            "source_generation": generation,
                        }
                    )
                    return
                token = self.headers.get("X-Vigilance-Camera", "")
                with state.lock:
                    camera = state.browser_camera
                    valid = camera is not None and secrets.compare_digest(
                        token.encode(), camera.token.encode()
                    )
                if not valid:
                    self._send_json(
                        {"error": "Webcam session ended or another source is active"},
                        409,
                    )
                    return
                if self.path == "/api/webcam/stop":
                    self._read_json()
                    with state.lock:
                        if state.browser_camera is camera:
                            state.source_generation += 1
                            state.source_request = (state.source_generation, "idle")
                            state.browser_camera = None
                            state.jpeg = None
                            state.status = dict(state.status, running=False)
                    self._send_json({"ok": True})
                elif self.path == "/api/webcam/frame":
                    if self.headers.get("Content-Type") != "image/jpeg":
                        raise ValueError("JPEG camera frame required")
                    frame = decode_frame(self._read_body(MAX_FRAME_BYTES))
                    with state.lock:
                        if state.browser_camera is not camera:
                            raise ValueError("Webcam session has changed")
                        camera.put(frame)
                    self._send_json({"ok": True})
                else:
                    self._send_json({"error": "not found"}, 404)
            except (ValueError, OSError):
                self._send_json(
                    {
                        "error": "Invalid webcam request or image; use JPEG up to 1280 × 720"
                    },
                    400,
                )

        def do_DELETE(self):
            if self.phone_request(state):
                return
            self._send_json({"error": "not found"}, 404)

        def do_HEAD(self):
            if not self.phone_request(state) and not self._incident_api():
                self._send(404, "text/plain", b"")

        def do_OPTIONS(self):
            if self.phone_request(state):
                return
            if not self._allowed(authenticated=False):
                return
            self._send(
                204,
                "text/plain",
                b"",
                (
                    ("Access-Control-Allow-Methods", "GET, POST, DELETE, OPTIONS"),
                    (
                        "Access-Control-Allow-Headers",
                        "Content-Type, X-Vigilance-Token, X-Vigilance-Camera",
                    ),
                    ("Access-Control-Max-Age", "600"),
                ),
            )

        def log_message(self, *args):
            pass

    return Handler
