"""Render the real dashboard in an ephemeral browser against synthetic state."""

import argparse
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--node", default="node")
    parser.add_argument("--playwright", default="playwright")
    parser.add_argument("--browser", required=True)
    args = parser.parse_args()
    runtime = ROOT / ".runtime-tests"
    runtime.mkdir(exist_ok=True)
    tempfile.tempdir = str(runtime)
    import cv2
    from config import VigilanceConfig
    from core.demo import ScriptedDetector, black_frames
    from core.http_server import BoundedHTTPServer, make_handler
    from core.pipeline import Pipeline
    from ui import LiveState, _status_from_result

    state = LiveState()
    frame = black_frames(1)[0]
    pipe = Pipeline(VigilanceConfig(), detector=ScriptedDetector([[]]))
    result = pipe.process(frame)
    state.status = _status_from_result(pipe, result, 30, "synthetic")
    state.frame_updated_at = time.monotonic()
    state.jpeg = cv2.imencode(".jpg", frame)[1].tobytes()
    server = BoundedHTTPServer(("127.0.0.1", 0), make_handler(state))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        completed = subprocess.run(
            [
                args.node,
                str(ROOT / "tools/browser_smoke.cjs"),
                f"http://127.0.0.1:{server.server_address[1]}/",
                args.playwright,
                args.browser,
                str(runtime / "dashboard.png"),
            ],
            cwd=ROOT,
            timeout=45,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        print(completed.stdout, end="")
        if completed.stderr:
            print(completed.stderr, end="", file=sys.stderr)
        return completed.returncode
    finally:
        state.stop = True
        server.shutdown()
        server.server_close()
        state.uploads.close()
        thread.join(timeout=2)


if __name__ == "__main__":
    raise SystemExit(main())
