"""Outbound alert dispatch: Telegram incident notifications.

One notifier, one rule: a timeline incident notifies ONCE (keyed by its
start time + scene), with a global minimum gap between messages so a
flapping scene cannot spam a phone. Delivery failures never propagate —
they log and the pump keeps running.

Token hygiene: the bot token must come from the ``VIGILANCE_TELEGRAM_BOT_TOKEN``
environment variable (or an explicit CLI flag). It is never written to disk,
logs, or evidence files by this module.
"""

from __future__ import annotations

import json
import time
from collections import OrderedDict
import queue
import threading
import sqlite3
from dataclasses import dataclass, field
from typing import Optional
from urllib.request import Request, urlopen

from core.logging_utils import get_logger

log = get_logger("alerts")

TELEGRAM_API = "https://api.telegram.org"
TIMEOUT_SEC = 10

FIGHT_SCENES = ("FIGHT", "MULTI_PERSON_FIGHT", "GROUP_ASSAULT")


@dataclass
class TelegramConfig:
    enabled: bool = False
    bot_token: str = ""
    chat_id: str = ""
    cooldown_sec: float = 60.0
    notify_fight: bool = True
    notify_fall: bool = True
    notify_fire: bool = True
    notify_bag: bool = True
    notify_weapon: bool = True


@dataclass
class Incident:
    key: str  # start + scene: one notification per incident
    title: str
    scene: Optional[str]
    alerts: list = field(default_factory=list)
    timestamp: float = 0.0


def incident_from_state(
    scene: Optional[str], alerts: dict, timestamp: float, event_id: Optional[str] = None
) -> Optional[Incident]:
    """Decide whether the current frame state deserves a notification."""
    names = sorted(alerts or {})
    fight = scene is not None and scene in FIGHT_SCENES
    if not fight and not names:
        return None
    if fight:
        title = {
            "FIGHT": "Possible fight detected",
            "MULTI_PERSON_FIGHT": "Group fight detected",
            "GROUP_ASSAULT": "Group assault detected",
        }.get(scene, "Fight detected")
    else:
        title = "Alert: " + ", ".join(names)
    key = event_id or f"{round(float(timestamp), 3)}:{scene or ','.join(names)}"
    return Incident(
        key=key, title=title, scene=scene, alerts=names, timestamp=float(timestamp)
    )


class TelegramNotifier:
    def __init__(self, config: TelegramConfig, recipient_store=None):
        self.config = config
        self.recipient_store = recipient_store
        self._notified_keys = OrderedDict()
        self._notified_recipients = OrderedDict()
        self._recipient_last_sent = OrderedDict()
        self._last_sent: float = float("-inf")
        self._next_attempt: float = float("-inf")
        self.last_error = None
        self.sent_count: int = 0

    @property
    def ready(self) -> bool:
        return bool(
            self.config.enabled and self.config.bot_token and self._destinations()
        )

    def _destinations(self) -> list[str]:
        try:
            destinations = (
                self.recipient_store.active_chat_ids() if self.recipient_store else []
            )
        except sqlite3.Error as exc:
            self.last_error = type(exc).__name__
            self._next_attempt = time.monotonic() + 15
            log.warning(
                "event=telegram_recipient_store_failed error=%s", self.last_error
            )
            return []
        if self.config.chat_id:
            destinations.append(str(self.config.chat_id))
        return list(dict.fromkeys(destinations))

    def _wanted(self, incident: Incident) -> bool:
        wanted = []
        if incident.scene in FIGHT_SCENES:
            wanted.append(self.config.notify_fight)
        names = set(incident.alerts)
        if any("Fall" in n for n in names):
            wanted.append(self.config.notify_fall)
        if any("Fire" in n or "Smoke" in n for n in names):
            wanted.append(self.config.notify_fire)
        if any("Bag" in n or "bag" in n for n in names):
            wanted.append(self.config.notify_bag)
        if any("Weapon" in n for n in names):
            wanted.append(self.config.notify_weapon)
        return any(wanted) if wanted else True

    def _post(self, method: str, payload: dict) -> dict:
        url = f"{TELEGRAM_API}/bot{self.config.bot_token}/{method}"
        req = Request(
            url,
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"},
        )
        with urlopen(req, timeout=TIMEOUT_SEC) as resp:
            return json.loads(resp.read().decode())

    def _send_to_chat(
        self, incident: Incident, jpeg: Optional[bytes], chat_id: str
    ) -> bool:
        caption = f"🚨 VIGLANCE: {incident.title}\nt={incident.timestamp:.1f}s"
        if incident.alerts:
            caption += "\n" + ", ".join(incident.alerts)
        if jpeg:
            return self._send_photo(incident, caption, jpeg, chat_id)
        result = self._post("sendMessage", {"chat_id": chat_id, "text": caption})
        return bool(result.get("ok", False))

    def _send(self, incident: Incident, jpeg: Optional[bytes]) -> bool:
        if not self.config.chat_id:
            return False
        return self._send_to_chat(incident, jpeg, str(self.config.chat_id))

    def _send_photo(
        self, incident: Incident, caption: str, jpeg: bytes, chat_id: str
    ) -> bool:
        boundary = "viglance-boundary"
        body = (
            (
                f"--{boundary}\r\nContent-Disposition: form-data; "
                f'name="chat_id"\r\n\r\n{chat_id}\r\n'
                f"--{boundary}\r\nContent-Disposition: form-data; "
                f'name="caption"\r\n\r\n{caption}\r\n'
                f'--{boundary}\r\nContent-Disposition: form-data; name="photo"; '
                f'filename="alert.jpg"\r\nContent-Type: image/jpeg\r\n\r\n'
            ).encode()
            + jpeg
            + f"\r\n--{boundary}--\r\n".encode()
        )
        url = f"{TELEGRAM_API}/bot{self.config.bot_token}/sendPhoto"
        req = Request(
            url,
            data=body,
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        )
        with urlopen(req, timeout=TIMEOUT_SEC) as resp:
            return bool(json.loads(resp.read().decode()).get("ok", False))

    def maybe_notify(
        self,
        incident: Optional[Incident],
        jpeg: Optional[bytes] = None,
        now: Optional[float] = None,
    ) -> bool:
        """Send at most once per incident; never raises."""
        if incident is None or not self.ready or not self._wanted(incident):
            return False
        if incident.key in self._notified_keys:
            return False
        now = time.monotonic() if now is None else now
        recipients = self._destinations()
        sent_recipients = self._notified_recipients.setdefault(incident.key, set())
        self._notified_recipients.move_to_end(incident.key)
        while len(self._notified_recipients) > 1024:
            self._notified_recipients.popitem(last=False)
        remaining = [
            chat_id for chat_id in recipients if chat_id not in sent_recipients
        ]
        if not remaining:
            if sent_recipients.issuperset(recipients):
                self._notified_keys[incident.key] = None
                while len(self._notified_keys) > 1024:
                    self._notified_keys.popitem(last=False)
                self.last_error = None
                self._notified_recipients.pop(incident.key, None)
            return False
        if now < self._next_attempt:
            return False
        failed = False
        deferred = []
        for chat_id in remaining:
            due = (
                self._recipient_last_sent.get(chat_id, float("-inf"))
                + self.config.cooldown_sec
            )
            if now < due:
                deferred.append(due)
                continue
            try:
                if self.recipient_store is None or chat_id == str(self.config.chat_id):
                    sent = self._send(incident, jpeg)
                else:
                    sent = self._send_to_chat(incident, None, chat_id)
            except (OSError, TimeoutError, ValueError, RuntimeError) as exc:
                self.last_error = type(exc).__name__
                self._next_attempt = now + 15
                log.warning("event=telegram_send_failed error=%s", self.last_error)
                failed = True
                continue
            if sent:
                sent_recipients.add(chat_id)
                self.sent_count += 1
                self._last_sent = now
                self._recipient_last_sent[chat_id] = now
                self._recipient_last_sent.move_to_end(chat_id)
                while len(self._recipient_last_sent) > 1024:
                    self._recipient_last_sent.popitem(last=False)
            else:
                self.last_error = "delivery_rejected"
                self._next_attempt = now + 15
                failed = True
        if deferred:
            self._next_attempt = min(
                [*deferred, self._next_attempt] if failed else deferred
            )
        if not failed and not deferred and sent_recipients.issuperset(recipients):
            self._notified_keys[incident.key] = None
            while len(self._notified_keys) > 1024:
                self._notified_keys.popitem(last=False)
            self._last_sent = now
            self.last_error = None
            self._notified_recipients.pop(incident.key, None)
            log.info(
                "event=telegram_sent key=%s title=%s recipients=%d",
                incident.key,
                incident.title,
                len(recipients),
            )
            return True
        if not failed and not deferred:
            self._notified_recipients.pop(incident.key, None)
        return False


class QueuedNotifier:
    """Nonblocking capture-thread interface; one bounded delivery worker."""

    def __init__(self, notifier, capacity=16):
        self.notifier = notifier
        self._queue = queue.Queue(maxsize=capacity)
        self._known = OrderedDict()
        self._stop = threading.Event()
        self.dropped = 0
        self._ready = bool(notifier.config.enabled and notifier.config.bot_token)
        self._known_lock = threading.Lock()
        self._worker = threading.Thread(
            target=self._run, daemon=True, name="incident-delivery"
        )
        self._worker.start()

    @property
    def ready(self):
        return self._ready

    @property
    def sent_count(self):
        return self.notifier.sent_count

    def maybe_notify(self, incident, jpeg=None):
        if incident is None or not self.ready or not self.notifier._wanted(incident):
            return False
        with self._known_lock:
            if incident.key in self._known:
                return False
            self._known[incident.key] = None
            try:
                self._queue.put_nowait((incident, jpeg))
            except queue.Full:
                self._known.pop(incident.key, None)
                self.dropped += 1
                return False
            while len(self._known) > 1024:
                self._known.popitem(last=False)
        return True

    def _run(self):
        while not self._stop.is_set():
            try:
                incident, jpeg = self._queue.get(timeout=0.2)
            except queue.Empty:
                self._ready = self.notifier.ready
                continue
            try:
                sent = False
                for _ in range(3):
                    delay = self.notifier._next_attempt - time.monotonic()
                    if self._stop.wait(max(0, delay)):
                        return
                    if self.notifier.maybe_notify(incident, jpeg):
                        sent = True
                        break
                if not sent:
                    with self._known_lock:
                        self._known.pop(incident.key, None)
                    self.notifier._notified_recipients.pop(incident.key, None)
            except Exception as exc:
                self.notifier.last_error = type(exc).__name__
                log.exception("event=delivery_worker_error")
                with self._known_lock:
                    self._known.pop(incident.key, None)
                self.notifier._notified_recipients.pop(incident.key, None)
            finally:
                self._ready = self.notifier.ready
                self._queue.task_done()

    def close(self):
        self._stop.set()
        self._worker.join(timeout=2)
