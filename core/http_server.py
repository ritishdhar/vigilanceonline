"""Loopback dashboard server with session authentication and resource bounds."""

from http import cookies
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import base64
import json
from pathlib import Path
import secrets
import socket
import threading
import time
from urllib.parse import unquote

from core.paths import ROOT
from core.source_utils import authorize_source, redact_source
from core.video_upload import MAX_UPLOAD_BYTES, VideoUploadError


class BoundedHTTPServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, *args, max_connections=16, **kwargs):
        self._slots = threading.BoundedSemaphore(max_connections)
        super().__init__(*args, **kwargs)

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
            super().process_request_thread(request, address)
        finally:
            self._slots.release()


def make_handler(state):
    class Handler(BaseHTTPRequestHandler):
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
                return super().parse_request()
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
            }
            origins = state.allowed_origins or {f"http://{host}" for host in hosts}
            host = self.headers.get("Host", "").lower()
            origin = self.headers.get("Origin")
            if host not in hosts or (origin is not None and origin not in origins):
                return self._deny("host or origin is not allowed")
            if not authenticated:
                return True
            token = self.headers.get("X-Vigilance-Token", "")
            if self.command == "GET" and not token:
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

        def _recipient_api(self, method):
            recipient_paths = {
                ("GET", "/api/telegram/recipients"),
                ("POST", "/api/telegram/invite"),
                ("POST", "/api/telegram/invite/revoke"),
                ("POST", "/api/telegram/test"),
                ("DELETE", "/api/telegram/recipients"),
            }
            if (method, self.path) not in recipient_paths:
                return False
            if not self._allowed():
                return True
            if self.path.startswith("/api/telegram/"):
                service = state.telegram_enrollment
                if service is None:
                    self._send_json(
                        {"error": "Telegram recipient setup is unavailable"}, 503
                    )
                    return True
                if method == "GET" and self.path == "/api/telegram/recipients":
                    self._send_json(
                        {
                            "bot_username": service.username,
                            "bot_error": service.last_error,
                            "recipients": [
                                {
                                    "chat_id": row["chat_id"],
                                    "name": row["display_name"],
                                    "username": row["username"],
                                    "active": bool(row["active"]),
                                }
                                for row in service.recipients.recipients()
                            ],
                        }
                    )
                    return True
                if method == "POST" and self.path == "/api/telegram/invite":
                    if not service.username or service.last_error:
                        self._send_json(
                            {"error": "Telegram bot is not connected yet"}, 503
                        )
                        return True
                    from io import BytesIO

                    import qrcode

                    token = secrets.token_urlsafe(24)
                    expires = service.recipients.create_invite(token)
                    url = f"https://t.me/{service.username}?start={token}"
                    image = qrcode.make(url)
                    output = BytesIO()
                    image.save(output, format="PNG")
                    self._send_json(
                        {
                            "url": url,
                            "qr_data_url": "data:image/png;base64,"
                            + base64.b64encode(output.getvalue()).decode("ascii"),
                            "expires_at": expires,
                        }
                    )
                    return True
                if method == "POST" and self.path == "/api/telegram/invite/revoke":
                    service.recipients.revoke_invite()
                    self._send_json({"ok": True})
                    return True
                if method == "POST" and self.path == "/api/telegram/test":
                    try:
                        delivered = service.send_test_alert()
                    except (OSError, TimeoutError, ValueError, RuntimeError) as exc:
                        self._send_json(
                            {
                                "error": f"Telegram test send failed: {type(exc).__name__}"
                            },
                            502,
                        )
                        return True
                    if delivered == 0:
                        self._send_json(
                            {"error": "No active Telegram recipients are enrolled"},
                            409,
                        )
                        return True
                    self._send_json({"ok": True, "delivered": delivered})
                    return True
                if method == "DELETE" and self.path == "/api/telegram/recipients":
                    try:
                        payload = self._read_json()
                    except (ValueError, OSError):
                        self._send_json({"error": "invalid JSON request"}, 400)
                        return True
                    chat_id = payload.get("chat_id")
                    if not isinstance(chat_id, str) or len(chat_id) > 32:
                        self._send_json({"error": "invalid recipient"}, 400)
                        return True
                    removed = service.recipients.remove(chat_id)
                    self._send_json({"ok": removed})
                    return True
                self._send_json({"error": "not found"}, 404)
                return True
            return True

        def do_GET(self):
            if self.path == "/healthz":
                self._send_json({"ok": True, "inference_mode": state.inference_mode})
                return
            if self._recipient_api("GET"):
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
            elif self.path == "/api/telegram/status":
                bot = state.telegram_enrollment
                active_count = 0
                if bot and getattr(bot, "recipients", None):
                    try:
                        active_count = len(bot.recipients.active_chat_ids())
                    except Exception:
                        pass
                self._send_json(
                    {
                        "configured": bot is not None,
                        "bot_ready": bool(bot and bot.username and not bot.last_error),
                        "bot_username": bot.username if bot else None,
                        "bot_error": bot.last_error if bot else None,
                        "recipient_count": active_count,
                        "has_recipients": active_count > 0,
                    }
                )
            elif self.path == "/api/status":
                with state.lock:
                    payload = dict(state.status)
                    last_frame = state.frame_updated_at
                payload["inference_mode"] = state.inference_mode
                payload.setdefault("model", state.model_status)
                payload.setdefault("source_generation", 0)
                if state.telegram_enrollment is not None:
                    bot = state.telegram_enrollment
                    telegram = dict(payload.get("telegram") or {})
                    telegram.update(
                        bot_ready=bool(bot.username and not bot.last_error),
                        bot_username=bot.username,
                        bot_error=bot.last_error,
                    )
                    payload["telegram"] = telegram
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
            if self._recipient_api("POST"):
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
            if self._recipient_api("DELETE"):
                return
            self._send_json({"error": "not found"}, 404)

        def do_OPTIONS(self):
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
