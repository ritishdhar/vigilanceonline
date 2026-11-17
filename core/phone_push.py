"""Web Push delivery from the persistent recipient outbox."""

from base64 import urlsafe_b64encode
import json
import sqlite3
import threading
from urllib.parse import urlsplit

import requests
from cryptography.hazmat.primitives import serialization
from py_vapid import Vapid
from pywebpush import WebPushException, webpush

from core.logging_utils import get_logger
from core.phone_recipients import validate_subscription

log = get_logger("phone_push")


class PushSession(requests.Session):
    def request(self, *args, **kwargs):
        # Keep push endpoints on the browser vendor hosts validated at enrollment.
        kwargs["allow_redirects"] = False
        return super().request(*args, **kwargs)


class PhonePush:
    def __init__(self, recipients, subject):
        parsed = urlsplit(subject)
        if not (
            parsed.scheme == "mailto"
            and "@" in parsed.path
            or parsed.scheme == "https"
            and parsed.hostname
            and not parsed.username
        ):
            raise ValueError("Use a mailto: or HTTPS contact for --push-subject.")
        self.recipients = recipients
        self.subject = subject
        self.key = Vapid()
        with recipients.incidents.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT value FROM phone_settings WHERE name='vapid'"
            ).fetchone()
            if row:
                self.key = Vapid.from_pem(row["value"])
            else:
                self.key.generate_keys()
                db.execute(
                    "INSERT INTO phone_settings VALUES ('vapid',?)",
                    (self.key.private_pem(),),
                )
        public = self.key.public_key.public_bytes(
            serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
        )
        self.public_key = urlsafe_b64encode(public).decode().rstrip("=")
        self.stop = threading.Event()
        self.wake = threading.Event()
        self.thread = threading.Thread(target=self._run, daemon=True, name="phone-push")

    def start(self):
        self.thread.start()

    def _run(self):
        with PushSession() as session:
            while not self.stop.is_set():
                try:
                    delivery = self.recipients.next_delivery()
                    if delivery:
                        self.deliver(session, delivery)
                        continue
                except sqlite3.Error:
                    log.exception("event=phone_outbox_unavailable")
                self.wake.wait(2)
                self.wake.clear()

    def deliver(self, session, delivery):
        incident_id = delivery["incident_id"]
        title = "RakshaNetra test alert" if delivery["test"] else "RakshaNetra incident"
        notice = delivery.get("source") == "Detection notification"
        if notice:
            title = delivery["title"]
        body = (
            "Tap to open the test alert."
            if delivery["test"]
            else "Tap to review the incident and its video."
        )
        if delivery["role"] == "responder" and not delivery["test"]:
            body = "An incident was forwarded to campus security. Tap to review it."
        if notice:
            body = "A detection alert was raised. Tap to review."
        status, retry_after = "failed", None
        try:
            subscription = validate_subscription(json.loads(delivery["subscription"]))
            response = webpush(
                subscription_info=subscription,
                data=json.dumps(
                    {"title": title, "body": body, "incident": incident_id}
                ),
                vapid_private_key=self.key,
                vapid_claims={"sub": self.subject},
                ttl=300,
                timeout=7,
                headers={"Urgency": "high", "Topic": incident_id},
                requests_session=session,
            )
            code = response.status_code
            if 200 <= code < 300:
                status = "accepted"
            elif code in (404, 410):
                status = "expired"
            elif code in (408, 429) or code >= 500:
                status, retry_after = "queued", 10 * (2 ** delivery["attempts"])
        except WebPushException as exc:
            if exc.response is None:
                status, retry_after = "queued", 10 * (2 ** delivery["attempts"])
            else:
                code = exc.response.status_code
                if code in (404, 410):
                    status = "expired"
                elif code in (408, 429) or code >= 500:
                    status, retry_after = "queued", 10 * (2 ** delivery["attempts"])
        except requests.RequestException:
            status, retry_after = "queued", 10 * (2 ** delivery["attempts"])
        except (TypeError, ValueError):
            log.warning("event=phone_subscription_invalid id=%s", delivery["device_id"])
        self.recipients.delivery_result(delivery, status, retry_after)

    def close(self):
        self.stop.set()
        self.wake.set()
        self.thread.join(timeout=9)
