"""A bounded, single-frame mailbox for the visiting browser's webcam."""

from io import BytesIO
import secrets
import time

MAX_FRAME_BYTES = 1024 * 1024


def decode_frame(data):
    from PIL import Image
    import cv2
    import numpy as np

    try:
        with Image.open(BytesIO(data)) as header:
            if header.format != "JPEG" or not (
                1 <= header.width <= 1280 and 1 <= header.height <= 720
            ):
                raise ValueError("Send JPEG frames no larger than 1280 × 720")
            header.verify()
    except (OSError, Image.DecompressionBombError) as error:
        raise ValueError("Invalid camera image") from error
    frame = cv2.imdecode(np.frombuffer(data, dtype=np.uint8), cv2.IMREAD_COLOR)
    if frame is None:
        raise ValueError("Invalid camera frame")
    return frame


class BrowserCamera:
    # All accesses are protected by LiveState.lock; at most one frame is retained.
    def __init__(self, generation):
        self.token = secrets.token_urlsafe(32)
        self.generation = generation
        self.frame = None
        self.started = self.received = time.monotonic()

    def put(self, frame):
        self.received = time.monotonic()
        self.frame = (frame, self.received - self.started)

    def take(self):
        frame, self.frame = self.frame, None
        return frame
