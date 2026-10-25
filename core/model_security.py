"""Verified artifact downloads and fail-closed YOLO deserialization."""

from hashlib import sha256
import os
from pathlib import Path
from tempfile import NamedTemporaryFile
from urllib.request import urlopen


def file_sha256(path):
    digest = sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def download_verified(url, destination, expected_sha256, max_bytes=64 * 1024 * 1024):
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.is_file() and file_sha256(destination) == expected_sha256:
        return str(destination)
    temporary = None
    try:
        with NamedTemporaryFile(
            dir=destination.parent, suffix=".part", delete=False
        ) as handle:
            temporary = Path(handle.name)
            total = 0
            with urlopen(url, timeout=30) as response:
                for chunk in iter(lambda: response.read(1024 * 1024), b""):
                    total += len(chunk)
                    if total > max_bytes:
                        raise ValueError("model exceeds approved download size")
                    handle.write(chunk)
        if file_sha256(temporary) != expected_sha256:
            raise ValueError("model checksum does not match approved artifact")
        os.replace(temporary, destination)
        return str(destination)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def load_yolo(path):
    # This must precede the first Ultralytics import: its setting is read once.
    os.environ["ULTRALYTICS_SAFE_LOAD"] = "1"
    os.environ["YOLO_AUTOINSTALL"] = "false"
    from ultralytics import YOLO
    from ultralytics.nn import tasks

    safe = getattr(tasks, "_SafeLoad", None)
    if not getattr(safe, "SUPPORTED", False) or not getattr(tasks, "SAFE_LOAD", False):
        raise RuntimeError(
            "Restricted YOLO loading unavailable; install the locked runtime "
            "and restart before loading model artifacts"
        )
    return YOLO(str(path))
