"""Streaming multipart video uploads with bounded memory and owned-file cleanup."""

from pathlib import Path
from tempfile import TemporaryDirectory
import threading
from uuid import uuid4

MAX_UPLOAD_BYTES = 256 * 1024 * 1024
VIDEO_EXTENSIONS = frozenset(
    {".avi", ".m4v", ".mkv", ".mov", ".mp4", ".mpeg", ".mpg", ".webm", ".wmv"}
)


class VideoUploadError(Exception):
    def __init__(self, message, status_code=400):
        super().__init__(message)
        self.status_code = status_code


def save_video_upload(stream, content_type, content_length, upload_dir):
    from python_multipart import MultipartParser
    from python_multipart.multipart import parse_options_header

    if not 0 <= content_length <= MAX_UPLOAD_BYTES:
        raise VideoUploadError(
            "invalid upload length", 413 if content_length > MAX_UPLOAD_BYTES else 400
        )
    mime, options = parse_options_header(content_type)
    boundary = options.get(b"boundary", b"")
    if mime.lower() != b"multipart/form-data" or not 1 <= len(boundary) <= 200:
        raise VideoUploadError("expected multipart/form-data with a valid boundary")
    upload_dir = Path(upload_dir)
    upload_dir.mkdir(parents=True, exist_ok=True)
    destination = None
    handle = None
    headers = {}
    header_name, header_value = bytearray(), bytearray()
    header_bytes = fields = 0
    ended = False

    def part_begin():
        nonlocal fields
        fields += 1
        if fields != 1:
            raise VideoUploadError("request must contain exactly one video field")
        headers.clear()

    def header_field(data, start, end):
        nonlocal header_bytes
        header_bytes += end - start
        if header_bytes > 8192:
            raise VideoUploadError("multipart headers exceed limit")
        header_name.extend(data[start:end])

    def header_value_part(data, start, end):
        nonlocal header_bytes
        header_bytes += end - start
        if header_bytes > 8192:
            raise VideoUploadError("multipart headers exceed limit")
        header_value.extend(data[start:end])

    def header_end():
        headers[bytes(header_name).lower()] = bytes(header_value)
        header_name.clear()
        header_value.clear()

    def headers_finished():
        nonlocal handle, destination
        disposition, params = parse_options_header(
            headers.get(b"content-disposition", b"")
        )
        if disposition != b"form-data" or params.get(b"name") != b"video":
            raise VideoUploadError("expected one video field")
        suffix = Path(
            params.get(b"filename", b"").decode("utf-8", "replace")
        ).suffix.lower()
        if suffix not in VIDEO_EXTENSIONS:
            raise VideoUploadError("unsupported video extension")
        destination = upload_dir / f"{uuid4().hex}{suffix}"
        handle = destination.open("xb")

    def part_data(data, start, end):
        if handle is None:
            raise VideoUploadError("video headers missing")
        handle.write(data[start:end])

    def end():
        nonlocal ended
        ended = True

    callbacks = {
        "on_part_begin": part_begin,
        "on_header_field": header_field,
        "on_header_value": header_value_part,
        "on_header_end": header_end,
        "on_headers_finished": headers_finished,
        "on_part_data": part_data,
        "on_end": end,
    }
    try:
        parser = MultipartParser(boundary, callbacks, max_size=MAX_UPLOAD_BYTES)
        remaining = content_length
        while remaining:
            chunk = stream.read(min(65536, remaining))
            if not chunk:
                raise VideoUploadError("upload ended before Content-Length")
            remaining -= len(chunk)
            parser.write(chunk)
        parser.finalize()
        if not ended or destination is None or fields != 1:
            raise VideoUploadError("incomplete multipart upload")
        handle.close()
        return destination
    except Exception as exc:
        if handle is not None:
            handle.close()
        if destination is not None:
            destination.unlink(missing_ok=True)
        if isinstance(exc, VideoUploadError):
            raise
        raise VideoUploadError("invalid upload or storage failure") from exc


class UploadStore:
    """Only files in a unique application-owned directory may be deleted."""

    def __init__(self):
        self._temporary = TemporaryDirectory(prefix="vigilance_uploads_")
        self.path = Path(self._temporary.name)
        self.lock = threading.Lock()

    def save(self, stream, content_type, length):
        with self.lock:
            size = sum(p.stat().st_size for p in self.path.iterdir() if p.is_file())
            if size + length > 2 * MAX_UPLOAD_BYTES:
                raise VideoUploadError("session upload quota exceeded", 413)
            return save_video_upload(stream, content_type, length, self.path)

    def release(self, source):
        path = Path(str(source)).resolve()
        if path.parent == self.path.resolve():
            with self.lock:
                path.unlink(missing_ok=True)

    def close(self):
        self._temporary.cleanup()
