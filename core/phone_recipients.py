"""Device enrollment, private incident access, and responder handoff."""

from base64 import b64decode, urlsafe_b64encode
from hashlib import sha256
import json
import re
import secrets
import time
from urllib.parse import urlsplit
from uuid import uuid4

ROLES = {"recipient", "responder"}
SESSION_SECONDS = 30 * 86400
INVITE_SECONDS = 600
TOKEN = re.compile(r"[A-Za-z0-9_-]{43}")


def token_digest(token):
    return sha256(token.encode("ascii")).hexdigest()


def validate_subscription(value):
    if not isinstance(value, dict) or not isinstance(value.get("keys"), dict):
        raise ValueError("Invalid push subscription.")
    endpoint, keys = value.get("endpoint"), value["keys"]
    if not isinstance(endpoint, str) or not endpoint.isascii() or len(endpoint) > 2048:
        raise ValueError("Invalid push URL.")
    url = urlsplit(endpoint)
    allowed = {
        "fcm.googleapis.com",
        "updates.push.services.mozilla.com",
        "web.push.apple.com",
    }
    hostname = url.hostname or ""
    if (
        url.scheme != "https"
        or not url.path
        or url.username is not None
        or url.password is not None
        or url.port not in {None, 443}
        or url.fragment
        or (hostname not in allowed and not hostname.endswith(".notify.windows.com"))
    ):
        raise ValueError("Unsupported push provider.")
    normalized = {}
    for name, size in (("p256dh", 65), ("auth", 16)):
        raw = keys.get(name)
        if not isinstance(raw, str) or not re.fullmatch(
            r"[A-Za-z0-9_-]{20,88}={0,2}", raw
        ):
            raise ValueError("Invalid subscription keys.")
        try:
            decoded = b64decode(
                raw + "=" * (-len(raw) % 4), altchars=b"-_", validate=True
            )
        except ValueError as exc:
            raise ValueError("Invalid subscription keys.") from exc
        if len(decoded) != size or (name == "p256dh" and decoded[0] != 4):
            raise ValueError("Invalid subscription keys.")
        normalized[name] = urlsafe_b64encode(decoded).decode().rstrip("=")
    return {"endpoint": endpoint, "keys": normalized}


class PhoneRecipients:
    def __init__(self, incidents):
        self.incidents = incidents
        with incidents.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS phone_invites (
                    digest TEXT PRIMARY KEY, role TEXT NOT NULL, expires REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS phone_devices (
                    id TEXT PRIMARY KEY, token_digest TEXT NOT NULL UNIQUE,
                    name TEXT NOT NULL, role TEXT NOT NULL, expires REAL NOT NULL,
                    subscription TEXT
                );
                CREATE TABLE IF NOT EXISTS phone_outbox (
                    incident_id TEXT NOT NULL REFERENCES incidents(id) ON DELETE CASCADE,
                    device_id TEXT NOT NULL REFERENCES phone_devices(id) ON DELETE CASCADE,
                    status TEXT NOT NULL DEFAULT 'queued',
                    attempts INTEGER NOT NULL DEFAULT 0, due REAL NOT NULL,
                    PRIMARY KEY (incident_id,device_id)
                );
                CREATE TABLE IF NOT EXISTS phone_invite_links (
                    digest TEXT PRIMARY KEY,
                    device_id TEXT NOT NULL REFERENCES phone_devices(id) ON DELETE CASCADE,
                    expires REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS phone_outbox_due ON phone_outbox(status,due);
                CREATE TABLE IF NOT EXISTS phone_notification_receipts (
                    incident_id TEXT NOT NULL REFERENCES incidents(id) ON DELETE CASCADE,
                    device_id TEXT NOT NULL REFERENCES phone_devices(id) ON DELETE CASCADE,
                    received_at REAL NOT NULL,
                    displayed_at REAL, opened_at REAL, display_failed INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY (incident_id,device_id)
                );
                CREATE TABLE IF NOT EXISTS phone_settings (name TEXT PRIMARY KEY, value BLOB);
                CREATE TABLE IF NOT EXISTS phone_inbox (
                    device_id TEXT NOT NULL REFERENCES phone_devices(id) ON DELETE CASCADE,
                    incident_id TEXT NOT NULL REFERENCES incidents(id) ON DELETE CASCADE,
                    PRIMARY KEY (device_id,incident_id)
                );
                CREATE TRIGGER IF NOT EXISTS phone_new_incident AFTER INSERT ON incidents BEGIN
                    INSERT INTO phone_outbox (incident_id,device_id,due)
                    SELECT NEW.id,id,NEW.observed_at FROM phone_devices
                    WHERE role='recipient' AND subscription IS NOT NULL AND expires>NEW.observed_at;
                END;
            """)

    def invite(self, role):
        if not isinstance(role, str) or role not in ROLES:
            raise ValueError("Choose a recipient role.")
        token, now = secrets.token_urlsafe(32), time.time()
        with self.incidents.connect() as db:
            db.execute("DELETE FROM phone_invites WHERE expires<=?", (now,))
            db.execute("DELETE FROM phone_invite_links WHERE expires<=?", (now,))
            if db.execute("SELECT COUNT(*) FROM phone_invites").fetchone()[0] >= 50:
                raise ValueError(
                    "Too many active invites. Wait for an invite to expire."
                )
            db.execute(
                "INSERT INTO phone_invites VALUES (?,?,?)",
                (token_digest(token), role, now + INVITE_SECONDS),
            )
        return token, int(now + INVITE_SECONDS)

    def invite_expiry(self, token):
        if not isinstance(token, str) or not TOKEN.fullmatch(token):
            return None
        with self.incidents.connect() as db:
            row = db.execute(
                "SELECT expires FROM phone_invites WHERE digest=? AND expires>?",
                (token_digest(token), time.time()),
            ).fetchone()
            return int(row["expires"]) if row else None

    def enroll(self, invite, name, *, existing_token=""):
        if not isinstance(invite, str) or not TOKEN.fullmatch(invite):
            raise ValueError("This invite is invalid or expired.")
        if not isinstance(name, str) or not name.strip() or len(name.strip()) > 48:
            raise ValueError("Enter a device name of 1–48 characters.")
        now, token = time.time(), secrets.token_urlsafe(32)
        digest, device_id = token_digest(invite), uuid4().hex
        with self.incidents.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("DELETE FROM phone_devices WHERE expires<=?", (now,))
            row = db.execute(
                "DELETE FROM phone_invites WHERE digest=? AND expires>? RETURNING role",
                (digest, now),
            ).fetchone()
            if row is None:
                raise ValueError("This invite has expired or was already used.")
            existing = None
            if TOKEN.fullmatch(existing_token):
                existing = db.execute(
                    "SELECT id,role FROM phone_devices WHERE token_digest=?",
                    (token_digest(existing_token),),
                ).fetchone()
            if existing:
                if existing["role"] != row["role"]:
                    raise ValueError("Disconnect this phone before linking it with a different role.")
                device_id, token = existing["id"], existing_token
                db.execute(
                    "UPDATE phone_devices SET expires=? WHERE id=?",
                    (now + SESSION_SECONDS, device_id),
                )
            else:
                if db.execute("SELECT COUNT(*) FROM phone_devices").fetchone()[0] >= 100:
                    raise ValueError("The workspace already has 100 devices.")
                db.execute(
                    "INSERT INTO phone_devices VALUES (?,?,?,?,?,NULL)",
                    (device_id, token_digest(token), name.strip(), row["role"], now + SESSION_SECONDS),
                )
            db.execute(
                "INSERT INTO phone_invite_links VALUES (?,?,?)",
                (digest, device_id, now + INVITE_SECONDS),
            )
        return token

    def cancel_invite(self, token):
        if not isinstance(token, str) or not TOKEN.fullmatch(token):
            raise ValueError("Invalid invite code.")
        with self.incidents.connect() as db:
            deleted = db.execute(
                "DELETE FROM phone_invites WHERE digest=?",
                (token_digest(token),),
            ).rowcount
        return {"status": "canceled"} if deleted else self.invite_progress(token)

    def invite_progress(self, token):
        if not isinstance(token, str) or not TOKEN.fullmatch(token):
            raise ValueError("Invalid invite code.")
        now, digest = time.time(), token_digest(token)
        with self.incidents.connect() as db:
            row = db.execute(
                """SELECT 'linked' AS status,d.id,d.name,d.role,
                          (d.subscription IS NOT NULL) AS subscribed FROM phone_invite_links l
                   JOIN phone_devices d ON d.id=l.device_id
                   WHERE l.digest=? AND l.expires>? AND d.expires>?
                   UNION ALL
                   SELECT 'waiting',NULL,NULL,NULL,NULL FROM phone_invites
                   WHERE digest=? AND expires>?
                   LIMIT 1""",
                (digest, now, now, digest, now),
            ).fetchone()
            if row and row["status"] == "linked":
                return {"status": "linked", "device": dict(row)}
        return {"status": row["status"] if row else "expired"}

    def session(self, token):
        if not TOKEN.fullmatch(token):
            return None
        with self.incidents.connect() as db:
            row = db.execute(
                """SELECT id,name,role,(subscription IS NOT NULL) AS subscribed
                   FROM phone_devices WHERE token_digest=? AND expires>?""",
                (token_digest(token), time.time()),
            ).fetchone()
            return dict(row) if row else None

    def devices(self):
        with self.incidents.connect() as db:
            return [
                dict(row)
                for row in db.execute(
                    """SELECT id,name,role,(subscription IS NOT NULL) AS subscribed
                       FROM phone_devices WHERE expires>? ORDER BY name,id""",
                    (time.time(),),
                )
            ]

    def inbox_cleared_through(self, device_id):
        with self.incidents.connect() as db:
            return db.execute(
                """SELECT COALESCE(MAX(i.sequence),0) FROM phone_inbox p
                   JOIN incidents i ON i.id=p.incident_id WHERE p.device_id=?""", (device_id,)
            ).fetchone()[0]

    def inbox_cleared(self, device_id, incident_id):
        with self.incidents.connect() as db:
            return db.execute(
                "SELECT 1 FROM phone_inbox WHERE device_id=? AND incident_id=?", (device_id, incident_id)
            ).fetchone() is not None

    def inbox(self, device):
        with self.incidents.connect() as db:
            cleared = {row[0] for row in db.execute(
                "SELECT incident_id FROM phone_inbox WHERE device_id=?", (device["id"],)
            )}
        return [
            incident for incident in self.incidents.list(responder=device["role"] == "responder")
            if incident["id"] not in cleared
        ]

    def clear_inbox(self, device, through):
        if type(through) is not int or through < 0:
            raise ValueError("Invalid inbox position. Refresh and try again.")
        with self.incidents.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            latest = db.execute("SELECT COALESCE(MAX(sequence),0) FROM incidents").fetchone()[0]
            if through > latest:
                raise ValueError("Invalid inbox position. Refresh and try again.")
            db.execute(
                """INSERT OR IGNORE INTO phone_inbox (device_id,incident_id)
                   SELECT ?,i.id FROM incidents i WHERE i.sequence<=? AND
                   (NOT ? OR EXISTS (SELECT 1 FROM incident_actions a
                       WHERE a.incident_id=i.id AND a.action='escalated'))""",
                (device["id"], through, device["role"] == "responder"),
            )
            cleared = db.execute(
                """SELECT COALESCE(MAX(i.sequence),0) FROM phone_inbox p
                   JOIN incidents i ON i.id=p.incident_id WHERE p.device_id=?""", (device["id"],)
            ).fetchone()[0]
            db.execute(
                """UPDATE phone_outbox SET status='cleared' WHERE device_id=? AND status='queued'
                   AND incident_id IN (SELECT incident_id FROM phone_inbox WHERE device_id=?)""",
                (device["id"], device["id"]),
            )
        return cleared

    def revoke(self, device_id):
        with self.incidents.connect() as db:
            return bool(
                db.execute(
                    "DELETE FROM phone_devices WHERE id=?", (device_id,)
                ).rowcount
            )

    def subscribe(self, device_id, payload):
        subscription = json.dumps(validate_subscription(payload), separators=(",", ":"))
        with self.incidents.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute(
                "UPDATE phone_devices SET subscription=NULL WHERE id!=? AND subscription=?",
                (device_id, subscription),
            )
            db.execute(
                "UPDATE phone_devices SET subscription=? WHERE id=? AND expires>?",
                (subscription, device_id, time.time()),
            )

    def unsubscribe(self, device_id):
        with self.incidents.connect() as db:
            db.execute("UPDATE phone_devices SET subscription=NULL WHERE id=?", (device_id,))

    def notification_receipt(self, device_id, incident_id, status):
        if not isinstance(status, str) or status not in {"displayed", "display-failed", "opened"}:
            raise ValueError("Invalid notification receipt.")
        now = time.time()
        with self.incidents.connect() as db:
            db.execute(
                """INSERT INTO phone_notification_receipts
                   (incident_id,device_id,received_at,displayed_at,opened_at,display_failed)
                   VALUES (?,?,?,?,?,?) ON CONFLICT(incident_id,device_id) DO UPDATE SET
                   displayed_at=COALESCE(excluded.displayed_at,displayed_at),
                   opened_at=COALESCE(excluded.opened_at,opened_at),
                   display_failed=CASE WHEN excluded.displayed_at IS NOT NULL THEN 0
                     WHEN displayed_at IS NOT NULL THEN display_failed ELSE excluded.display_failed END""",
                (incident_id, device_id, now, now if status == "displayed" else None,
                 now if status == "opened" else None, int(status == "display-failed")),
            )

    def notification_status(self, device_id):
        with self.incidents.connect() as db:
            row = db.execute(
                """SELECT o.status AS provider,r.received_at,r.displayed_at,r.opened_at,r.display_failed
                   FROM phone_outbox o JOIN incidents i ON i.id=o.incident_id
                   LEFT JOIN phone_notification_receipts r ON r.incident_id=o.incident_id AND r.device_id=o.device_id
                   WHERE o.device_id=? ORDER BY i.observed_at DESC LIMIT 1""",
                (device_id,),
            ).fetchone()
        return dict(row) if row else None

    @staticmethod
    def actor(device):
        return f"{device['name']} ({device['id'][:8]})"

    def handoff(self, incident_id, device, note):
        if device["role"] != "recipient":
            raise PermissionError("Only a recipient can request a handoff.")
        if not isinstance(note, str) or len(note) > 500:
            raise ValueError("Notes can contain up to 500 characters.")
        with self.incidents.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            incident = db.execute(
                "SELECT review FROM incidents WHERE id=?", (incident_id,)
            ).fetchone()
            if incident is None:
                return False
            if self.incidents._escalated(db, incident_id):
                return True
            if incident["review"] != "acknowledged":
                raise ValueError("Acknowledge the incident before forwarding it.")
            now = time.time()
            if (
                db.execute(
                    "SELECT 1 FROM phone_devices WHERE role='responder' AND expires>?",
                    (now,),
                ).fetchone()
                is None
            ):
                raise ValueError("Enroll a campus responder before forwarding.")
            db.execute(
                """INSERT INTO incident_actions (incident_id,action,actor,note,created_at)
                   VALUES (?,'escalated',?,?,?)""",
                (incident_id, self.actor(device), note.strip(), now),
            )
            db.execute(
                """INSERT INTO phone_outbox (incident_id,device_id,due)
                   SELECT ?,id,? FROM phone_devices
                   WHERE role='responder' AND subscription IS NOT NULL AND expires>?""",
                (incident_id, now, now),
            )
            return True

    def delivery_summary(self, incident_id):
        with self.incidents.connect() as db:
            return {
                row["status"]: row["number"]
                for row in db.execute(
                    "SELECT status,COUNT(*) AS number FROM phone_outbox WHERE incident_id=? GROUP BY status",
                    (incident_id,),
                )
            }

    def next_delivery(self):
        now = time.time()
        with self.incidents.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute(
                """UPDATE phone_outbox SET status='failed'
                   WHERE status='queued' AND (attempts>=5 OR device_id IN (
                       SELECT id FROM phone_devices WHERE expires<=? OR subscription IS NULL
                   ) OR incident_id IN (SELECT id FROM incidents WHERE observed_at<?)
                   AND device_id IN (SELECT id FROM phone_devices WHERE role='recipient'))""",
                (now, now - 600),
            )
            row = db.execute(
                """SELECT o.incident_id,o.device_id,o.attempts,d.subscription,d.role,i.test,i.title,i.source
                   FROM phone_outbox o JOIN phone_devices d ON o.device_id=d.id
                   JOIN incidents i ON o.incident_id=i.id
                   WHERE o.status='queued' AND o.due<=? ORDER BY o.due LIMIT 1""",
                (now,),
            ).fetchone()
            if row is None:
                return None
            db.execute(
                """UPDATE phone_outbox SET attempts=attempts+1,due=?
                   WHERE incident_id=? AND device_id=?""",
                (now + 60, row["incident_id"], row["device_id"]),
            )
            return dict(row)

    def delivery_result(self, delivery, status, retry_after=None):
        with self.incidents.connect() as db:
            db.execute(
                """UPDATE phone_outbox SET status=?,due=? WHERE incident_id=? AND device_id=?
                   AND status!='cleared'""",
                (
                    status,
                    time.time() + (retry_after or 0),
                    delivery["incident_id"],
                    delivery["device_id"],
                ),
            )
            if status == "expired":
                db.execute(
                    "UPDATE phone_devices SET subscription=NULL WHERE id=? AND subscription=?",
                    (delivery["device_id"], delivery["subscription"]),
                )
