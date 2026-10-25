"""Opt-in Telegram recipient enrollment and outbound bot polling."""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from core.logging_utils import get_logger

log = get_logger("telegram_recipients")

TELEGRAM_API = "https://api.telegram.org"
INVITE_SECONDS = 3600
MAX_RECIPIENTS = 100


class TelegramAPIError(RuntimeError):
    """A credential-free error code suitable for status/log output."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def _telegram_error(code: int) -> TelegramAPIError:
    return TelegramAPIError(
        {
            401: "BOT_TOKEN_REJECTED",
            404: "BOT_TOKEN_REJECTED",
            409: "POLLING_CONFLICT",
            429: "RATE_LIMITED",
        }.get(code, "TELEGRAM_REQUEST_FAILED")
    )


class TelegramRecipientStore:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self):
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        return connection

    @contextmanager
    def _db(self):
        connection = self._connect()
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def _initialize(self):
        with self._db() as db:
            db.execute(
                """CREATE TABLE IF NOT EXISTS recipients (
                    chat_id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    username TEXT NOT NULL DEFAULT '',
                    display_name TEXT NOT NULL DEFAULT 'Telegram recipient',
                    active INTEGER NOT NULL DEFAULT 1,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL
                )"""
            )
            db.execute(
                """CREATE TABLE IF NOT EXISTS enrollment_invites (
                    token_hash TEXT PRIMARY KEY,
                    expires_at REAL NOT NULL,
                    created_at REAL NOT NULL
                )"""
            )

    @staticmethod
    def _token_hash(token: str) -> str:
        return hashlib.sha256(token.encode("ascii")).hexdigest()

    def create_invite(self, token: str, now: float | None = None) -> float:
        created = time.time() if now is None else float(now)
        expires = created + INVITE_SECONDS
        with self._db() as db:
            db.execute("DELETE FROM enrollment_invites")
            db.execute(
                "INSERT INTO enrollment_invites(token_hash, expires_at, created_at) VALUES (?, ?, ?)",
                (self._token_hash(token), expires, created),
            )
        return expires

    def revoke_invite(self) -> None:
        with self._db() as db:
            db.execute("DELETE FROM enrollment_invites")

    def enroll(
        self,
        token: str,
        chat_id: str,
        user_id: str,
        username: str = "",
        first_name: str = "",
        now: float | None = None,
    ) -> bool:
        timestamp = time.time() if now is None else float(now)
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", token):
            return False
        name = (first_name or username or "Telegram recipient").strip()[:80]
        safe_username = username.strip().lstrip("@")[:64]
        with self._db() as db:
            invite = db.execute(
                "SELECT expires_at FROM enrollment_invites WHERE token_hash = ?",
                (self._token_hash(token),),
            ).fetchone()
            if invite is None or float(invite["expires_at"]) <= timestamp:
                return False
            existing = db.execute(
                "SELECT 1 FROM recipients WHERE chat_id = ? AND active = 1",
                (str(chat_id),),
            ).fetchone()
            if existing is not None:
                return True
            count = db.execute(
                "SELECT COUNT(*) FROM recipients WHERE active = 1"
            ).fetchone()[0]
            if count >= MAX_RECIPIENTS:
                return False
            db.execute(
                """INSERT INTO recipients
                   (chat_id, user_id, username, display_name, active, created_at, updated_at)
                   VALUES (?, ?, ?, ?, 1, ?, ?)
                   ON CONFLICT(chat_id) DO UPDATE SET
                     user_id=excluded.user_id, username=excluded.username,
                     display_name=excluded.display_name, active=1, updated_at=excluded.updated_at""",
                (
                    str(chat_id),
                    str(user_id),
                    safe_username,
                    name,
                    timestamp,
                    timestamp,
                ),
            )
        return True

    def unsubscribe(self, chat_id: str) -> bool:
        with self._db() as db:
            cursor = db.execute(
                "UPDATE recipients SET active = 0, updated_at = ? WHERE chat_id = ? AND active = 1",
                (time.time(), str(chat_id)),
            )
        return cursor.rowcount > 0

    def remove(self, chat_id: str) -> bool:
        with self._db() as db:
            cursor = db.execute(
                "DELETE FROM recipients WHERE chat_id = ?", (str(chat_id),)
            )
        return cursor.rowcount > 0

    def recipients(self) -> list[dict]:
        with self._db() as db:
            rows = db.execute(
                """SELECT chat_id, username, display_name, active, created_at
                   FROM recipients ORDER BY created_at DESC"""
            ).fetchall()
        return [dict(row) for row in rows]

    def active_chat_ids(self) -> list[str]:
        with self._db() as db:
            rows = db.execute(
                "SELECT chat_id FROM recipients WHERE active = 1 ORDER BY created_at"
            ).fetchall()
        return [str(row["chat_id"]) for row in rows]


class TelegramEnrollmentBot:
    """Long-poll Telegram updates to consume recipient opt-ins without a webhook."""

    def __init__(self, token: str, recipients: TelegramRecipientStore):
        self.token = token
        self.recipients = recipients
        self.username: str | None = None
        self.last_error: str | None = None
        self._stop = threading.Event()
        self._offset = 0
        self._thread: threading.Thread | None = None

    def _request(self, method: str, payload: dict | None = None) -> dict:
        data = None if payload is None else json.dumps(payload).encode("utf-8")
        request = Request(
            f"{TELEGRAM_API}/bot{self.token}/{method}",
            data=data,
            headers={"Content-Type": "application/json"},
        )
        try:
            with urlopen(request, timeout=35) as response:
                result = json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            # HTTPError includes a token-bearing URL. Never propagate its text.
            raise _telegram_error(exc.code) from None
        if not result.get("ok"):
            raise _telegram_error(int(result.get("error_code", 0)))
        return result.get("result")

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._poll, daemon=True, name="telegram-enrollment"
        )
        self._thread.start()

    def close(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5)

    def _send_message(self, chat_id: str, text: str) -> None:
        self._request("sendMessage", {"chat_id": chat_id, "text": text})

    def send_test_alert(self) -> int:
        message = (
            "RakshaNetra test notification. Telegram delivery is connected; "
            "this is not a detected incident."
        )
        delivered = 0
        for chat_id in self.recipients.active_chat_ids():
            self._send_message(chat_id, message)
            delivered += 1
        return delivered

    def _handle_update(self, update: dict) -> None:
        message = update.get("message") or update.get("edited_message") or {}
        chat = message.get("chat") or {}
        sender = message.get("from") or {}
        if chat.get("type") != "private" or not chat.get("id"):
            return
        text = str(message.get("text") or "").strip()
        if text == "/stop":
            self.recipients.unsubscribe(str(chat["id"]))
            self._send_message(
                str(chat["id"]),
                "Alerts paused. You will receive no further notifications.",
            )
            return
        match = re.fullmatch(r"/start(?:@\w+)?(?:\s+([A-Za-z0-9_-]{1,64}))?", text)
        if match is None:
            return
        invite = match.group(1)
        if invite and self.recipients.enroll(
            invite,
            str(chat["id"]),
            str(sender.get("id", chat["id"])),
            str(sender.get("username") or ""),
            str(sender.get("first_name") or ""),
        ):
            self._send_message(
                str(chat["id"]),
                "You are connected to RakshaNetra alerts. Send /stop at any time to unsubscribe. Phone notification sounds depend on your Telegram and device settings.",
            )
        elif invite:
            self._send_message(
                str(chat["id"]),
                "This invite has expired or is no longer available. Ask the workspace owner for a new QR code.",
            )

    def _poll(self) -> None:
        while not self._stop.is_set():
            try:
                if self.username is None:
                    profile = self._request("getMe")
                    webhook = self._request("getWebhookInfo")
                    if webhook.get("url"):
                        raise TelegramAPIError("WEBHOOK_ACTIVE")
                    self.username = str(profile["username"])
                    log.info("event=telegram_enrollment_ready bot=%s", self.username)
                updates = self._request(
                    "getUpdates",
                    {
                        "offset": self._offset,
                        "timeout": 25,
                        "allowed_updates": ["message"],
                    },
                )
                self.last_error = None
                for update in updates:
                    self._offset = max(self._offset, int(update["update_id"]) + 1)
                    self._handle_update(update)
            except (
                OSError,
                TimeoutError,
                ValueError,
                KeyError,
                RuntimeError,
                sqlite3.Error,
            ) as exc:
                self.last_error = (
                    exc.code
                    if isinstance(exc, TelegramAPIError)
                    else type(exc).__name__
                )
                log.warning(
                    "event=telegram_enrollment_poll_failed error=%s", self.last_error
                )
                self._stop.wait(5)
