"""Pipeline-owned fall detection using torso angle, aspect and stillness."""

from __future__ import annotations

import math
from typing import Any, Dict, List

import numpy as np

from core.logging_utils import get_logger

log = get_logger("fall")


def _as_records(tracked_objects) -> List[Dict[str, Any]]:
    items = []
    for obj in tracked_objects or []:
        if hasattr(obj, "to_dict"):
            items.append(obj.to_dict())
        else:
            items.append(obj)
    return items


class FallDetector:
    def __init__(
        self,
        angle_deg: float = 45.0,
        stillness_sec: float = 1.5,
        min_visible_sec: float = 0.5,
        min_torso_conf: float = 0.1,
    ):
        self.angle_deg = float(angle_deg)
        self.stillness_sec = float(stillness_sec)
        self.min_visible_sec = float(min_visible_sec)
        self.min_torso_conf = float(min_torso_conf)
        self.history: Dict[tuple, list] = {}

    def reset(self) -> None:
        self.history.clear()

    def update(self, tracked_objects, timestamp: float) -> Dict[str, Any]:
        best_alert: Dict[str, Any] = {
            "alert": False,
            "person_id": -1,
            "angle": 0.0,
            "confidence": 0.0,
            "message": "Normal",
        }
        incidents = []
        objects = [
            obj for obj in _as_records(tracked_objects) if obj.get("present", True)
        ]
        live = {(obj["track_id"], obj.get("generation", 0)) for obj in objects}
        self.history = {
            key: value for key, value in self.history.items() if key in live
        }
        for obj in objects:
            person_id = obj["track_id"]
            identity = (person_id, obj.get("generation", 0))
            kp = np.asarray(obj["keypoints"], dtype=np.float32).reshape(-1)
            if identity not in self.history:
                self.history[identity] = []
            if float(np.sum(kp)) == 0:
                self.history[identity].clear()
                continue

            ls_x, ls_y, ls_c = kp[5 * 3 : 5 * 3 + 3]
            rs_x, rs_y, rs_c = kp[6 * 3 : 6 * 3 + 3]
            lh_x, lh_y, lh_c = kp[11 * 3 : 11 * 3 + 3]
            rh_x, rh_y, rh_c = kp[12 * 3 : 12 * 3 + 3]
            if (
                min(float(ls_c), float(rs_c), float(lh_c), float(rh_c))
                < self.min_torso_conf
            ):
                self.history[identity].clear()
                continue

            shoulder_mid_x = (float(ls_x) + float(rs_x)) / 2.0
            shoulder_mid_y = (float(ls_y) + float(rs_y)) / 2.0
            hip_mid_x = (float(lh_x) + float(rh_x)) / 2.0
            hip_mid_y = (float(lh_y) + float(rh_y)) / 2.0

            dx = hip_mid_x - shoulder_mid_x
            dy = hip_mid_y - shoulder_mid_y
            angle = math.degrees(math.atan2(abs(dx), abs(dy)))

            xs = kp[0::3]
            ys = kp[1::3]
            confs = kp[2::3]
            valid = confs > 0.1
            w, h = 0.0, 0.0
            if int(np.sum(valid)) >= 2:
                w = float(np.max(xs[valid]) - np.min(xs[valid]))
                h = float(np.max(ys[valid]) - np.min(ys[valid]))
            aspect_ratio_wide = w > h

            if not (angle > self.angle_deg and aspect_ratio_wide):
                self.history[identity].clear()
                continue

            self.history[identity].append(
                {"timestamp": float(timestamp), "x": hip_mid_x, "y": hip_mid_y, "h": h}
            )
            cutoff = float(timestamp) - self.stillness_sec
            while (
                len(self.history[identity]) > 2
                and self.history[identity][1]["timestamp"] <= cutoff
            ):
                self.history[identity].pop(0)

            still = False
            if len(self.history[identity]) > 3:
                start = self.history[identity][0]
                elapsed = float(timestamp) - start["timestamp"]
                visible = float(timestamp) - float(
                    obj.get("first_seen", start["timestamp"])
                )
                if (
                    elapsed + 1e-6 >= self.stillness_sec
                    and visible + 1e-6 >= self.min_visible_sec
                ):
                    displacement = max(
                        math.hypot(item["x"] - start["x"], item["y"] - start["y"])
                        for item in self.history[identity]
                    )
                    if displacement < max(h * 0.15, 15.0):
                        still = True

            alert = (angle > self.angle_deg) and aspect_ratio_wide and still
            confidence = (
                min(1.0, max(0.0, (angle - self.angle_deg) / 45.0)) if alert else 0.0
            )
            if alert:
                log.info(
                    "event=fall_alert person=%s angle=%.1f ts=%.3f",
                    person_id,
                    angle,
                    timestamp,
                )
                incidents.append(
                    {
                        "alert": True,
                        "person_id": person_id,
                        "generation": identity[1],
                        "angle": round(angle, 2),
                        "confidence": round(confidence, 2),
                        "message": f"Fall detected! Person {person_id} is down.",
                    }
                )
        if incidents:
            best_alert = dict(max(incidents, key=lambda item: item["confidence"]))
            best_alert["incidents"] = incidents
            best_alert["person_ids"] = [item["person_id"] for item in incidents]
        return best_alert
