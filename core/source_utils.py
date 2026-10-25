"""Capture clocks, source authorization, and credential-free display labels."""

from pathlib import Path
from urllib.parse import urlsplit, urlunsplit
import math
import time


def redact_source(source):
    text = str(source)
    try:
        url = urlsplit(text)
        if url.scheme and url.netloc:
            host = url.hostname or "stream"
            if ":" in host:
                host = f"[{host}]"
            if url.port:
                host += f":{url.port}"
            return urlunsplit((url.scheme, host, url.path, "", ""))
    except ValueError:
        return "stream"
    return text


def authorize_source(source, upload_dir, allowed_sources=()):
    text = str(source)
    if text.isdigit() and 0 <= int(text) <= 9:
        return text
    if text in allowed_sources:
        return text
    try:
        path = Path(text).resolve()
        if path.is_file() and path.is_relative_to(Path(upload_dir).resolve()):
            return str(path)
    except (OSError, ValueError):
        pass
    raise ValueError(
        "source must be a camera index, an uploaded video, or an explicitly allowed source"
    )


class CaptureClock:
    def __init__(self, live, fps, monotonic=time.monotonic):
        self.live = bool(live)
        self.fps = float(fps) if math.isfinite(fps) and fps > 0 else 30.0
        self._monotonic = monotonic
        self.start = monotonic()
        self.last = -1.0

    def timestamp(self, index, media_ms=None):
        if self.live:
            value = self._monotonic() - self.start
        else:
            value = float(media_ms or 0) / 1000.0
            if not math.isfinite(value) or value <= self.last:
                value = max(index / self.fps, self.last + 1.0 / self.fps)
        self.last = max(value, self.last)
        return self.last
