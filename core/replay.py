"""Deterministic replay: same video in -> comparable outputs out.

Rules enforced here:
- ``pipeline.reset()`` before every run (no leaked buffers/counters).
- Timestamps come from ``frame_index / fps`` (video FPS when readable,
  else ``config.fps``), never from wall-clock.
- ``random``/``numpy`` are re-seeded from ``config`` seed so stochastic
  callers behave identically across runs.
- Tracked persons are serialized in ``track_id`` order; alerts in sorted
  key order; floats rounded -- so two runs diff cleanly.
- Evidence is a JSONL file (one record per frame) under ``evidence_dir``.
"""

from __future__ import annotations

import json
import os
import random
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Dict, Iterable, List, Optional

import numpy as np

from config import VigilanceConfig
from core.logging_utils import get_logger
from core.source_utils import CaptureClock, redact_source

log = get_logger("replay")


def seed_everything(seed: int = 0) -> None:
    random.seed(seed)
    try:
        np.random.seed(seed % (2**32))
    except Exception:
        pass


def frame_record(
    frame_index: int, timestamp: float, persons, alerts, result=None
) -> Dict[str, Any]:
    persons_sorted = sorted(list(persons), key=lambda p: p.track_id)
    record = {
        "frame": int(frame_index),
        "timestamp": round(float(timestamp), 6),
        "persons": [p.to_dict() for p in persons_sorted],
        "alerts": {k: alerts[k] for k in sorted(alerts)},
    }
    if result is not None:
        record.update(
            scene=asdict(result.scene) if result.scene is not None else None,
            pair_scores=result.pair_scores,
            fight_status=result.fight_status,
            model_status=result.model_status,
            scenario_errors=result.scenario_errors,
        )

    def primitive(value):
        if isinstance(value, Enum):
            return value.value
        if isinstance(value, np.ndarray):
            return value.tolist()
        if isinstance(value, np.generic):
            return value.item()
        raise TypeError(f"unsupported evidence type: {type(value).__name__}")

    return json.loads(json.dumps(record, default=primitive, allow_nan=False))


def compare_runs(
    a: List[Dict[str, Any]], b: List[Dict[str, Any]], atol: float = 1e-6
) -> Dict[str, Any]:
    """Compare two replay record lists; report first divergence (if any)."""
    result: Dict[str, Any] = {
        "frames_a": len(a),
        "frames_b": len(b),
        "identical": False,
        "first_diff_frame": None,
        "detail": "",
    }
    if len(a) != len(b):
        result["detail"] = f"frame count differs: {len(a)} vs {len(b)}"
        return result

    def difference(x, y, path="record"):
        if isinstance(x, dict) and isinstance(y, dict):
            if x.keys() != y.keys():
                return path + ".keys"
            for key in sorted(x):
                found = difference(x[key], y[key], f"{path}.{key}")
                if found:
                    return found
        elif isinstance(x, list) and isinstance(y, list):
            if len(x) != len(y):
                return path + ".length"
            for idx, (left, right) in enumerate(zip(x, y)):
                found = difference(left, right, f"{path}[{idx}]")
                if found:
                    return found
        elif isinstance(x, (float, int)) and isinstance(y, (float, int)):
            if not np.isclose(x, y, rtol=0, atol=atol):
                return path
        elif type(x) is not type(y) or x != y:
            return path
        return None

    for ra, rb in zip(a, b):
        path = difference(ra, rb)
        if path:
            result.update(first_diff_frame=ra["frame"], detail=f"{path} differs")
            return result
    result["identical"] = True
    result["detail"] = "all frames comparable"
    return result


@dataclass
class ReplayRunner:
    config: VigilanceConfig = field(default_factory=VigilanceConfig)
    seed: int = 0

    def iter_records(self, frames: Iterable[np.ndarray], pipeline=None):
        from core.pipeline import Pipeline

        pipe = pipeline or Pipeline(config=self.config)
        pipe.reset()
        seed_everything(self.seed)
        for idx, frame in enumerate(frames):
            ts = idx / float(self.config.fps)
            result = pipe.process(np.asarray(frame), timestamp=ts)
            yield frame_record(
                result.frame_index,
                result.timestamp,
                result.persons,
                result.alerts,
                result,
            )

    def run_frames(
        self, frames: Iterable[np.ndarray], pipeline=None
    ) -> List[Dict[str, Any]]:
        return list(self.iter_records(frames, pipeline))

    def run_frames_and_save(
        self, frames: Iterable[np.ndarray], run_name: str = "replay", pipeline=None
    ) -> str:
        os.makedirs(self.config.evidence_dir, exist_ok=True)
        path = os.path.join(self.config.evidence_dir, f"{run_name}.jsonl")
        with open(path, "w", encoding="utf-8") as fh:
            for rec in self.iter_records(frames, pipeline=pipeline):
                fh.write(json.dumps(rec, default=float) + "\n")
        log.info("event=evidence_written path=%s", path)
        return path

    def run_video(
        self, source: str, pipeline=None, max_frames: Optional[int] = None
    ) -> List[Dict[str, Any]]:
        try:
            import cv2
        except ImportError as exc:
            raise ImportError("opencv-python is required to read video files.") from exc
        cap = cv2.VideoCapture(int(source) if str(source).isdigit() else source)
        if not cap.isOpened():
            raise FileNotFoundError(
                f"Could not open video source: {redact_source(source)}"
            )
        fps = cap.get(cv2.CAP_PROP_FPS)
        clock = CaptureClock(str(source).isdigit() or "://" in str(source), fps)
        self.config.fps = clock.fps
        from core.pipeline import Pipeline

        pipe = pipeline or Pipeline(config=self.config)
        pipe.reset()
        seed_everything(self.seed)
        records: List[Dict[str, Any]] = []
        idx = 0
        try:
            while True:
                ok, frame = cap.read()
                if not ok:
                    break
                ts = clock.timestamp(idx, cap.get(cv2.CAP_PROP_POS_MSEC))
                result = pipe.process(frame, timestamp=ts)
                records.append(
                    frame_record(
                        result.frame_index,
                        result.timestamp,
                        result.persons,
                        result.alerts,
                        result,
                    )
                )
                idx += 1
                if max_frames is not None and idx >= max_frames:
                    break
        finally:
            cap.release()
        log.info(
            "event=replay_video_done source=%s frames=%d",
            redact_source(source),
            len(records),
        )
        return records
