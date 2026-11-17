"""HTTP routes for the role-scoped recipient app and operator enrollment."""

from base64 import b64encode
from http import cookies
from io import BytesIO
import sqlite3

from core.incidents import INCIDENT_ID
from core.paths import ROOT
from core.phone_recipients import SESSION_SECONDS

ASSETS = {
    "/recipient/": ("index.html", "text/html; charset=utf-8"),
    "/recipient/app.js": ("app.js", "text/javascript; charset=utf-8"),
    "/recipient/alarm.js": ("alarm.js", "text/javascript; charset=utf-8"),
    "/recipient/scanner.js": ("scanner.js", "text/javascript; charset=utf-8"),
    "/recipient/style.css": ("style.css", "text/css; charset=utf-8"),
    "/recipient/sw.js": ("sw.js", "text/javascript; charset=utf-8"),
    "/recipient/manifest.webmanifest": (
        "manifest.webmanifest",
        "application/manifest+json",
    ),
    "/recipient/icon-192.png": ("icon-192.png", "image/png"),
    "/recipient/icon-512.png": ("icon-512.png", "image/png"),
    "/recipient/brand.png": ("brand.png", "image/png"),
    "/recipient/vendor/jsqr-1.4.0.js": (
        "vendor/jsqr-1.4.0.js",
        "text/javascript; charset=utf-8",
    ),
    "/recipient/vendor/jsqr-LICENSE": (
        "vendor/jsqr-LICENSE",
        "text/plain; charset=utf-8",
    ),
}


class PhoneRoutes:
    def phone_request(self, state):
        path = self.path
        demo_admin = path == "/api/phone-demo-admin" or path.startswith("/api/phone-demo-admin/")
        phone_host = self.headers.get("Host", "").lower() == state.phone_host
        phone_path = path in {"/recipient", "/api/recipient"} or path.startswith(
            ("/recipient/", "/api/recipient/")
        )
        admin_path = path == "/api/phone-admin" or path.startswith("/api/phone-admin/")
        if not (phone_host or phone_path or admin_path or demo_admin):
            return False
        if phone_host and not phone_path:
            if path == "/join" and self.command == "GET" and getattr(state, "public_demo", False):
                try:
                    code, _ = state.phone_recipients.invite("recipient")
                except ValueError:
                    self._send_json({"error": "The demo is busy. Try scanning again shortly."}, 429)
                    return True
                self._send(302, "text/plain", b"", (("Location", "/recipient/#invite=" + code),
                    ("Referrer-Policy", "no-referrer")))
                return True
            if path == "/" and self.command in {"GET", "HEAD"}:
                self._send(302, "text/plain", b"", (("Location", "/recipient/"),))
            else:
                self._send_json({"error": "Not found"}, 404)
            return True
        if phone_host and self.headers.get("Origin") not in {None, state.phone_origin}:
            self._deny("Origin is not allowed")
            return True
        if not phone_host and not self._allowed(authenticated=admin_path or demo_admin):
            return True
        if demo_admin:
            demo = getattr(state, "demo_recipient_state", None)
            if demo is None:
                self._send_json({"error": "Demo workspace is unavailable"}, 404)
                return True
            state = demo
            admin_path = True
        if state.phone_recipients is None:
            self._send_json({"error": "Recipient workspace is unavailable"}, 503)
            return True
        try:
            if admin_path:
                self._phone_admin(state, "/api/phone-demo-admin" if demo_admin else "/api/phone-admin")
            else:
                self._phone_client(state, secure=phone_host or state.secure_cookies)
        except PermissionError as exc:
            self._send_json({"error": str(exc)}, 403)
        except ValueError as exc:
            self._send_json({"error": str(exc)}, 400)
        except sqlite3.Error:
            self._send_json(
                {"error": "Recipient storage is temporarily unavailable"}, 503
            )
        return True

    def _phone_admin(self, state, base):
        workspace = state.phone_recipients
        origin = (
            state.phone_origin
            or ("https://" if state.secure_cookies else "http://")
            + self.headers["Host"]
        )
        if self.command == "GET" and self.path == base:
            self._send_json(
                {
                    "devices": workspace.devices(),
                    "origin": origin,
                    "phone_https": origin.startswith("https://"),
                    "push": state.phone_push is not None,
                    "demo_available": bool(getattr(state, "demo_recipient_state", None)),
                }
            )
            return
        if self.command != "POST":
            self._send_json({"error": "Not found"}, 404)
            return
        if self.path == base + "/invite":
            token, expires = workspace.invite(self._read_json().get("role"))
            url = origin + "/recipient/#invite=" + token
            qr = None
            if origin.startswith("https://"):
                import qrcode

                image = BytesIO()
                qrcode.make(url).save(image, format="PNG")
                qr = "data:image/png;base64," + b64encode(image.getvalue()).decode()
            self._send_json(
                {"url": url, "qr": qr, "code": token, "expires": expires}, 201
            )
        elif self.path == base + "/invite/progress":
            self._send_json(workspace.invite_progress(self._read_json().get("code")))
        elif self.path == base + "/invite/cancel":
            self._send_json(workspace.cancel_invite(self._read_json().get("code")))
        elif self.path == base + "/revoke":
            device_id = self._read_json().get("id")
            if not isinstance(device_id, str) or not INCIDENT_ID.fullmatch(device_id):
                raise ValueError("Invalid device.")
            workspace.revoke(device_id)
            self._send_json({"ok": True})
        elif self.path == base + "/test":
            self._read_json()
            incident_id = state.incident_store.test_alert()
            if state.phone_push:
                state.phone_push.wake.set()
            self._send_json(
                {
                    "id": incident_id,
                    "delivery": workspace.delivery_summary(incident_id),
                },
                201,
            )
        else:
            self._send_json({"error": "Not found"}, 404)

    def _phone_client(self, state, *, secure):
        if self.path == "/recipient" and self.command in {"GET", "HEAD"}:
            self._send(302, "text/plain", b"", (("Location", "/recipient/"),))
            return
        asset = ASSETS.get(self.path.split("?", 1)[0])
        if asset and self.command in {"GET", "HEAD"}:
            filename, mime = asset
            self._send(
                200,
                mime,
                (ROOT / "web" / "recipient" / filename).read_bytes(),
                (
                    ("Referrer-Policy", "no-referrer"),
                    (
                        "Content-Security-Policy",
                        "default-src 'self'; object-src 'none'; base-uri 'none'; form-action 'self'",
                    ),
                ),
            )
            return
        if self.command == "POST" and self.headers.get("X-Raksha-Recipient") != "1":
            self._deny("Recipient request header required")
            return
        base = "/api/recipient"
        workspace = state.phone_recipients
        if self.path == base + "/invite/status" and self.command == "POST":
            expires = workspace.invite_expiry(self._read_json().get("invite"))
            if expires is None:
                self._send_json(
                    {
                        "error": "This invite has expired or was already used. Scan a new QR from Alert recipients."
                    },
                    410,
                )
            else:
                self._send_json({"expires": expires})
            return
        if self.path == base + "/enroll" and self.command == "POST":
            data = self._read_json()
            token = workspace.enroll(
                data.get("invite"), data.get("name"), existing_token=self._phone_token()
            )
            self._send_json(
                {"ok": True}, 201, (("Set-Cookie", self._phone_cookie(token, secure)),)
            )
            return
        device = workspace.session(self._phone_token())
        if self.path == base + "/session" and self.command == "GET":
            self._send_json(
                {
                    "device": device,
                    "cleared_through": workspace.inbox_cleared_through(device["id"]) if device else 0,
                    "push_key": state.phone_push.public_key
                    if state.phone_push
                    else None,
                    "emergency_number": state.emergency_number,
                    "workspace_name": state.incident_store.site,
                    "notification_status": workspace.notification_status(device["id"]) if device else None,
                }
            )
            return
        if not self.path.startswith(base + "/"):
            self._send_json({"error": "Not found"}, 404)
            return
        if device is None:
            self._send_json({"error": "Enroll this device to continue"}, 401)
            return
        if self.path == base + "/logout" and self.command == "POST":
            self._read_json()
            workspace.revoke(device["id"])
            self._send_json(
                {"ok": True}, 200, (("Set-Cookie", self._phone_cookie("", secure)),)
            )
        elif self.path == base + "/subscribe" and self.command == "POST":
            if state.phone_push is None:
                self._send_json({"error": "Web Push is unavailable"}, 503)
                return
            workspace.subscribe(device["id"], self._read_json())
            state.phone_push.wake.set()
            self._send_json({"ok": True})
        elif self.path == base + "/unsubscribe" and self.command == "POST":
            self._read_json()
            workspace.unsubscribe(device["id"])
            self._send_json({"ok": True})
        elif self.path == base + "/notification/receipt" and self.command == "POST":
            data = self._read_json()
            incident_id = data.get("incident")
            if not isinstance(incident_id, str) or not INCIDENT_ID.fullmatch(incident_id):
                raise ValueError("Invalid incident.")
            incident = state.incident_store.get(incident_id, responder=device["role"] == "responder")
            if incident is None:
                self._send_json({"error": "Incident not found"}, 404)
                return
            workspace.notification_receipt(device["id"], incident_id, data.get("status"))
            self._send_json({"ok": True})
        elif self.path == base + "/incidents" and self.command == "GET":
            self._send_json(
                {
                    "incidents": workspace.inbox(device)
                }
            )
        elif self.path == base + "/inbox/clear" and self.command == "POST":
            cleared = workspace.clear_inbox(device, self._read_json().get("through"))
            self._send_json({"cleared_through": cleared})
        else:
            self._phone_incident(state, device)

    def _phone_incident(self, state, device):
        parts = self.path.split("/")
        if (
            len(parts) not in {5, 6}
            or parts[3] != "incidents"
            or not INCIDENT_ID.fullmatch(parts[4])
        ):
            self._send_json({"error": "Not found"}, 404)
            return
        store, incident_id = state.incident_store, parts[4]
        responder = device["role"] == "responder"
        incident = store.get(incident_id, responder=responder)
        if incident is None:
            self._send_json({"error": "Incident not found"}, 404)
            return

        # Authorize against this workspace before resolving the matching camera
        # record. A shared evidence store must not broaden a responder's scope.
        evidence = getattr(state, "evidence_state", None)
        record = (
            evidence.incident_store.get(incident_id)
            if incident["source"] == "Detection notification" and evidence else None
        )
        inline = record is not None and getattr(state, "public_recipient_evidence", False)
        if len(parts) == 5 and self.command == "GET":
            incident["inbox_cleared"] = state.phone_recipients.inbox_cleared(device["id"], incident_id)
            incident["protected_evidence"] = record is not None and not inline
            incident["inline_evidence"] = bool(inline)
            if record is not None:
                incident["evidence_status"] = record["clip"]
                incident["photo_count"] = len(record["stills"])
                if inline:
                    incident["clip"] = record["clip"]
                    incident["stills"] = record["stills"]
            if not responder:
                incident["handoff_available"] = any(
                    phone["role"] == "responder"
                    for phone in state.phone_recipients.devices()
                )
            self._send_json(incident)
        elif len(parts) == 6 and parts[5] == "evidence" and self.command == "POST":
            self._read_json()
            if record is None:
                self._send_json({"error": "Camera evidence is unavailable for this alert."}, 404)
                return
            if inline:
                self._send_json({"url": state.phone_origin + "/recipient/#incident=" + incident_id})
                return
            token, _ = evidence.phone_recipients.invite("recipient")
            self._send_json({
                "url": evidence.phone_origin + "/recipient/?evidence=1#invite="
                + token + "&incident=" + incident_id
            })
        elif (
            len(parts) == 6
            and self.command in {"GET", "HEAD"}
            and self._incident_media(evidence.incident_store if inline else store, incident_id, parts[5])
        ):
            return
        elif (
            len(parts) == 6
            and self.command == "POST"
            and parts[5] in {"review", "handoff", "opened"}
        ):
            data = self._read_json()
            actor = state.phone_recipients.actor(device)
            saved = True
            if parts[5] == "review":
                saved = store.review(
                    incident_id,
                    data.get("action"),
                    actor,
                    data.get("note", ""),
                    responder=responder,
                )
            elif parts[5] == "opened":
                store.opened(incident_id, actor)
            else:
                if data.get("confirm") is not True:
                    raise ValueError("Confirm the handoff before forwarding.")
                saved = state.phone_recipients.handoff(
                    incident_id, device, data.get("note", "")
                )
                if state.phone_push:
                    state.phone_push.wake.set()
            self._send_json(
                {"ok": True} if saved else {"error": "Incident not found"},
                200 if saved else 404,
            )
        else:
            self._send_json({"error": "Not found"}, 404)

    def _phone_token(self):
        try:
            jar = cookies.SimpleCookie(self.headers.get("Cookie", ""))
            return jar["raksha_phone"].value if "raksha_phone" in jar else ""
        except cookies.CookieError:
            return ""

    @staticmethod
    def _phone_cookie(token, secure):
        return (
            f"raksha_phone={token}; Path=/; HttpOnly; SameSite=Strict; "
            f"Max-Age={SESSION_SECONDS if token else 0}"
            + ("; Secure" if secure else "")
        )
