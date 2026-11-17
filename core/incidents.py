"""Persistent incident records shared by the operator and phone clients."""

from contextlib import contextmanager
from pathlib import Path
import re
import sqlite3
import time
from uuid import uuid4

from core.alert_dispatch import incident_from_state

REVIEWS = {"acknowledged", "resolved", "false-positive", "unreviewed"}
INCIDENT_ID = re.compile(r"[0-9a-f]{32}")
HISTORY_LIMIT = 200
RETENTION_SECONDS = 7 * 86400
INCIDENT_STILLS = {
    "before.jpg": ".before.jpg",
    "thumbnail.jpg": ".jpg",
    "after.jpg": ".after.jpg",
}


def incident_source(source):
    if source.isascii() and source.isdecimal():
        return f"Camera {source}"
    if source == "browser-webcam":
        return "Browser webcam"
    if "://" in source:
        return "Network camera"
    return re.split(r"[/\\]", source)[-1]


def incident_record(row):
    record = dict(row)
    record["severity"] = (
        "Test"
        if record["test"]
        else "High"
        if record["category"] in {"Violence/Fight", "Fire Threat", "Weapon Threat"}
        else "Review"
    )
    return record


class IncidentStore:
    def __init__(self, directory, *, site="Campus", camera="Primary camera"):
        self.directory = Path(directory).resolve()
        self.directory.mkdir(parents=True, exist_ok=True)
        self.path = self.directory / "incidents.sqlite3"
        self.media_directory = self.directory / "incident-media"
        self.media_directory.mkdir(exist_ok=True)
        self.site = site
        self.camera = camera
        with self.connect() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript("""
                CREATE TABLE IF NOT EXISTS incidents (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    id TEXT NOT NULL UNIQUE,
                    title TEXT NOT NULL,
                    category TEXT NOT NULL,
                    scene TEXT,
                    source TEXT NOT NULL,
                    source_time REAL NOT NULL,
                    observed_at REAL NOT NULL,
                    site TEXT NOT NULL,
                    camera TEXT NOT NULL,
                    confidence REAL,
                    review TEXT NOT NULL DEFAULT 'unreviewed',
                    clip TEXT NOT NULL DEFAULT 'unavailable',
                    test INTEGER NOT NULL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS incident_actions (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    incident_id TEXT NOT NULL REFERENCES incidents(id) ON DELETE CASCADE,
                    action TEXT NOT NULL,
                    actor TEXT NOT NULL,
                    note TEXT NOT NULL,
                    created_at REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS incident_actions_history
                    ON incident_actions(incident_id, sequence);
            """)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=2)
        try:
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA foreign_keys=ON")
            with db:
                yield db
        finally:
            db.close()

    def record(self, event, source, *, clip="unavailable"):
        alerts = dict.fromkeys(event["alerts"])
        incident = incident_from_state(
            event["scene"], alerts, event["start"], event["id"]
        )
        if incident is None:
            return
        self._insert(
            event["id"],
            incident.title,
            next(iter(alerts), "Violence/Fight"),
            event["scene"],
            source,
            event["start"],
            event.get("confidence"),
            clip,
            False,
        )

    def test_alert(self):
        incident_id = uuid4().hex
        self._insert(
            incident_id,
            "Test alert from RakshaNetra",
            "Test alert",
            None,
            "Operator test",
            0,
            None,
            "unavailable",
            True,
        )
        return incident_id

    def detection_notice(self, event):
        """Save a basic notice without camera identity, footage, or detection details."""
        titles = {
            "Weapon Threat": "Possible weapon detected",
            "Fire Threat": "Possible fire detected",
            "Smoke Threat": "Possible smoke detected",
            "Fall Detection": "Possible fall detected",
            "Violence/Fight": "Possible fight detected",
        }
        category = next((name for name in event["alerts"] if name in titles), "Violence/Fight")
        self._insert(
            event["id"], titles[category], category, None, "Detection notification",
            0, None, "unavailable", False,
        )

    def _insert(
        self,
        incident_id,
        title,
        category,
        scene,
        source,
        source_time,
        confidence,
        clip,
        test,
    ):
        with self.connect() as db:
            db.execute(
                """INSERT INTO incidents
                (id,title,category,scene,source,source_time,observed_at,site,camera,confidence,clip,test)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO NOTHING""",
                (
                    incident_id,
                    title,
                    category,
                    scene,
                    source,
                    source_time,
                    time.time(),
                    self.site,
                    self.camera,
                    confidence,
                    clip,
                    test,
                ),
            )

    def list(self, *, responder=False):
        with self.connect() as db:
            rows = db.execute(
                """SELECT incidents.*, EXISTS (
                    SELECT 1 FROM incident_actions
                    WHERE incident_id=incidents.id AND action='escalated'
                ) AS escalated
                FROM incidents WHERE observed_at>=? AND (NOT ? OR escalated)
                ORDER BY sequence DESC LIMIT ?""",
                (time.time() - RETENTION_SECONDS, responder, HISTORY_LIMIT),
            )
            return [incident_record(row) for row in rows]

    def get(self, incident_id, *, responder=False):
        with self.connect() as db:
            row = db.execute(
                "SELECT * FROM incidents WHERE id=? AND observed_at>=?",
                (incident_id, time.time() - RETENTION_SECONDS),
            ).fetchone()
            if row is None:
                return None
            actions = [
                dict(action)
                for action in db.execute(
                    """SELECT action,actor,note,created_at FROM incident_actions
                       WHERE incident_id=? ORDER BY sequence DESC LIMIT 100""",
                    (incident_id,),
                )
            ]
            if responder and not self._escalated(db, incident_id):
                return None
        stills = [
            name
            for name, suffix in INCIDENT_STILLS.items()
            if self.media(incident_id, suffix).is_file()
        ]
        return dict(incident_record(row), actions=actions, stills=stills)

    @staticmethod
    def _escalated(db, incident_id):
        return (
            db.execute(
                """SELECT 1 FROM incident_actions WHERE incident_id=? AND action='escalated'""",
                (incident_id,),
            ).fetchone()
            is not None
        )

    def review(self, incident_id, action, actor, note="", *, responder=False):
        if not isinstance(action, str) or action not in REVIEWS:
            raise ValueError("Choose a valid review status.")
        if not isinstance(note, str) or len(note) > 500:
            raise ValueError("Notes can contain up to 500 characters.")
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if responder and not self._escalated(db, incident_id):
                return False
            if not db.execute(
                "UPDATE incidents SET review=? WHERE id=?", (action, incident_id)
            ).rowcount:
                return False
            audit_action = (
                "responder-acknowledged"
                if responder and action == "acknowledged"
                else action
            )
            db.execute(
                """INSERT INTO incident_actions (incident_id,action,actor,note,created_at)
                   VALUES (?,?,?,?,?)""",
                (incident_id, audit_action, actor, note.strip(), time.time()),
            )
            return True

    def media(self, incident_id, suffix):
        if not INCIDENT_ID.fullmatch(incident_id) or (
            suffix != ".mp4" and suffix not in INCIDENT_STILLS.values()
        ):
            raise ValueError("Invalid incident media")
        return self.media_directory / (incident_id + suffix)

    def opened(self, incident_id, actor):
        with self.connect() as db:
            db.execute(
                """INSERT INTO incident_actions (incident_id,action,actor,note,created_at)
                   SELECT ?,'opened',?,'',? WHERE EXISTS (SELECT 1 FROM incidents WHERE id=?)
                   AND NOT EXISTS (SELECT 1 FROM incident_actions WHERE incident_id=?
                                   AND action='opened' AND actor=?)""",
                (incident_id, actor, time.time(), incident_id, incident_id, actor),
            )

    def set_clip(self, incident_id, status):
        with self.connect() as db:
            db.execute("UPDATE incidents SET clip=? WHERE id=?", (status, incident_id))

    def recover_clips(self):
        with self.connect() as db:
            pending = db.execute(
                "SELECT id FROM incidents WHERE clip='processing'"
            ).fetchall()
        for row in pending:
            self.set_clip(
                row["id"],
                "ready" if self.media(row["id"], ".mp4").is_file() else "unavailable",
            )

    def prune(self):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            expired = db.execute(
                """DELETE FROM incidents WHERE observed_at < ? OR id IN
                   (SELECT id FROM incidents ORDER BY sequence DESC LIMIT -1 OFFSET 500)
                   RETURNING id""",
                (time.time() - RETENTION_SECONDS,),
            ).fetchall()
        for row in expired:
            for suffix in (*INCIDENT_STILLS.values(), ".mp4"):
                self.media(row["id"], suffix).unlink(missing_ok=True)
