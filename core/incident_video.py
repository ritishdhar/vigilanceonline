"""Bounded pre/post-event buffer and asynchronous MP4 encoding."""

from collections import deque
from concurrent.futures import ThreadPoolExecutor
import sqlite3
import subprocess
import threading
import time

from core.logging_utils import get_logger

log = get_logger("incident_video")
FPS = 8
PRE_SECONDS = 5
POST_SECONDS = 10
MAX_FRAME_BYTES = 384 * 1024
MAX_CLIP_BYTES = 16 * 1024 * 1024
MAX_JOBS = 4


class IncidentRecorder:
    def __init__(self, store, encoder):
        self.store = store
        self.encoder = encoder
        self.history = deque(maxlen=FPS * PRE_SECONDS + 2)
        self.pending = {}
        self.slots = threading.BoundedSemaphore(MAX_JOBS)
        self.worker = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="incident-video"
        )
        self.next_prune = time.monotonic() + 600
        self.last_sample = float("-inf")
        store.recover_clips()
        store.prune()

    def wants_frame(self, timestamp):
        return timestamp < self.last_sample or timestamp >= self.last_sample + (0.9 / FPS)

    def ingest(self, timestamp, jpeg, events, source):
        if timestamp < self.last_sample:
            self.finish_source()
        valid_frame = jpeg and len(jpeg) <= MAX_FRAME_BYTES
        sampled = valid_frame and self.wants_frame(timestamp)
        if sampled:
            self.last_sample = timestamp
            self.history.append((timestamp, jpeg))
        while self.history and self.history[0][0] < timestamp - PRE_SECONDS:
            self.history.popleft()
        for event in events:
            self._begin(
                event, source, timestamp, jpeg if valid_frame else None, sampled
            )
        for incident_id, (alert_at, frames) in tuple(self.pending.items()):
            until = alert_at + POST_SECONDS
            if sampled and frames[-1][0] < timestamp <= until + 1 / FPS:
                frames.append((timestamp, jpeg))
            if timestamp >= until:
                self._submit(incident_id)

    def maintain(self):
        if time.monotonic() < self.next_prune:
            return
        self.next_prune = time.monotonic() + 600
        try:
            self.store.prune()
        except (OSError, sqlite3.Error):
            log.exception("event=incident_retention_failed")

    def _begin(self, event, source, timestamp, jpeg, sampled):
        incident_id = event["id"]
        reserved = jpeg is not None and self.slots.acquire(blocking=False)
        try:
            self.store.record(
                event, source, clip="processing" if reserved else "unavailable"
            )
        except sqlite3.Error:
            if reserved:
                self.slots.release()
            log.exception("event=incident_save_failed id=%s", incident_id)
            return
        if jpeg is None:
            return
        if reserved:
            frames = list(self.history)
            if not sampled:
                frames.append((timestamp, jpeg))
            self.pending[incident_id] = (timestamp, frames)
        self._save_still(incident_id, ".jpg", jpeg)

    def _save_still(self, incident_id, suffix, jpeg):
        path = self.store.media(incident_id, suffix)
        temporary = path.with_suffix(".jpg.part")
        try:
            temporary.write_bytes(jpeg)
            temporary.replace(path)
        except OSError:
            log.exception("event=incident_still_failed id=%s", incident_id)

    def _submit(self, incident_id):
        alert_at, frames = self.pending.pop(incident_id)
        self.worker.submit(self._write, incident_id, alert_at, frames)

    def _write(self, incident_id, alert_at, frames):
        path = self.store.media(incident_id, ".mp4")
        temporary = path.with_suffix(".part.mp4")
        status = "unavailable"
        try:
            before_at, before = frames[0]
            after_at, after = frames[-1]
            if before_at < alert_at:
                self._save_still(incident_id, ".before.jpg", before)
            if after_at > alert_at:
                self._save_still(incident_id, ".after.jpg", after)
            if len(frames) < 2:
                return
            result = subprocess.run(
                [
                    self.encoder,
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-y",
                    "-f",
                    "image2pipe",
                    "-framerate",
                    str(FPS),
                    "-vcodec",
                    "mjpeg",
                    "-i",
                    "pipe:0",
                    "-an",
                    "-vf",
                    "scale=w='min(1280,iw)':h='min(720,ih)':force_original_aspect_ratio=decrease:force_divisible_by=2",
                    "-c:v",
                    "libx264",
                    "-preset",
                    "ultrafast",
                    "-crf",
                    "24",
                    "-pix_fmt",
                    "yuv420p",
                    "-movflags",
                    "+faststart",
                    str(temporary),
                ],
                input=sample_bytes(frames),
                capture_output=True,
                timeout=25,
                check=False,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            if (
                result.returncode == 0
                and 0 < temporary.stat().st_size <= MAX_CLIP_BYTES
            ):
                temporary.replace(path)
                status = "ready"
            else:
                log.warning(
                    "event=incident_encode_failed id=%s return=%d",
                    incident_id,
                    result.returncode,
                )
        except (OSError, subprocess.SubprocessError):
            log.exception("event=incident_encode_failed id=%s", incident_id)
        finally:
            try:
                temporary.unlink(missing_ok=True)
                self.store.set_clip(incident_id, status)
            except (OSError, sqlite3.Error):
                log.exception("event=incident_clip_status_failed id=%s", incident_id)
            finally:
                self.slots.release()

    def finish_source(self):
        for incident_id in tuple(self.pending):
            self._submit(incident_id)
        self.history.clear()
        self.last_sample = float("-inf")

    def close(self):
        self.finish_source()
        self.worker.shutdown(wait=True)


def sample_bytes(frames):
    """Resample sparse camera frames to the clip's fixed playback rate."""
    count = min(
        round((PRE_SECONDS + POST_SECONDS + 1) * FPS),
        round((frames[-1][0] - frames[0][0]) * FPS) + 1,
    )
    start = frames[0][0]
    index = 0
    samples = []
    for tick in range(count):
        target = start + tick / FPS + 0.5 / FPS
        while index + 1 < len(frames) and frames[index + 1][0] <= target:
            index += 1
        samples.append(frames[index][1])
    return b"".join(samples)
