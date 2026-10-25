"""Abandoned-bag / bag-snatch logic (person-bag distance + separation timer).

Ported WITHOUT behavior change to the rule itself. Foundational fixes:
- ``update(results, timestamp)``: timestamp is an explicit argument.
- No import-time side effects; global instance is lazy.
- ``reset()`` clears bag tracks for deterministic replay.
- ``logging`` instead of ``print``.
"""

from __future__ import annotations

import math
from typing import Any, Dict

from core.logging_utils import get_logger

log = get_logger("bag")


def calculate_distance(box1, box2) -> float:
    c1_x = (box1[0] + box1[2]) / 2.0
    c1_y = (box1[1] + box1[3]) / 2.0
    c2_x = (box2[0] + box2[2]) / 2.0
    c2_y = (box2[1] + box2[3]) / 2.0
    return math.hypot(c1_x - c2_x, c1_y - c2_y)


def calculate_center(box):
    return (box[0] + box[2]) / 2.0, (box[1] + box[3]) / 2.0


class BagDetector:
    def __init__(
        self,
        proximity_threshold: float = 150.0,
        speed_threshold: float = 30.0,
        confirm_sec: float = 1.5,
    ):
        self.tracked_bags: Dict[int, dict] = {}
        self.next_bag_id = 1
        self.proximity_threshold = float(proximity_threshold)
        self.speed_threshold = float(speed_threshold)
        self.confirm_sec = float(confirm_sec)

    def reset(self) -> None:
        self.tracked_bags.clear()
        self.next_bag_id = 1

    def update(self, results, timestamp: float) -> Dict[str, Any]:
        output: Dict[str, Any] = {
            "alert": False,
            "bag_bbox": None,
            "person_bbox": None,
            "distance": 0.0,
            "message": "Normal",
        }
        if results is None or len(results) == 0:
            self.tracked_bags.clear()
            return output
        r = results[0]
        if r.boxes is None or len(r.boxes) == 0:
            self.tracked_bags.clear()
            return output

        boxes = r.boxes.xyxy.cpu().numpy()
        classes = r.boxes.cls.cpu().numpy()
        confs = r.boxes.conf.cpu().numpy()

        person_boxes = [
            boxes[i]
            for i in range(len(boxes))
            if int(classes[i]) == 0 and confs[i] > 0.3
        ]
        bag_boxes = [
            boxes[i]
            for i in range(len(boxes))
            if int(classes[i]) == 26 and confs[i] > 0.2
        ]

        current_tracked_bags: Dict[int, dict] = {}
        for bag_bbox in bag_boxes:
            bag_center = calculate_center(bag_bbox)
            min_p_dist = float("inf")
            nearest_p_bbox = None
            for p_bbox in person_boxes:
                d = calculate_distance(bag_bbox, p_bbox)
                if d < min_p_dist:
                    min_p_dist = d
                    nearest_p_bbox = p_bbox

            best_match_id = None
            best_match_dist = float("inf")
            for t_id, t_data in self.tracked_bags.items():
                if t_id in current_tracked_bags:
                    continue
                d = math.hypot(
                    bag_center[0] - t_data["center"][0],
                    bag_center[1] - t_data["center"][1],
                )
                if d < 100.0 and d < best_match_dist:
                    best_match_dist = d
                    best_match_id = t_id

            if best_match_id is None:
                bag_id = self.next_bag_id
                self.next_bag_id += 1
                snatch_time = None
                associated = min_p_dist < self.proximity_threshold
                separation_speed = 0.0
            else:
                bag_id = best_match_id
                snatch_time = self.tracked_bags[bag_id]["snatch_start_time"]
                associated = self.tracked_bags[bag_id].get("associated", False)
                previous = self.tracked_bags[bag_id]
                elapsed = float(timestamp) - previous["timestamp"]
                separation_speed = (
                    (min_p_dist - previous["min_person_dist"]) / elapsed
                    if elapsed > 0
                    and math.isfinite(min_p_dist)
                    and math.isfinite(previous["min_person_dist"])
                    else 0.0
                )
                if (
                    associated
                    and min_p_dist > self.proximity_threshold
                    and snatch_time is None
                ):
                    snatch_time = float(timestamp)
                if min_p_dist < self.proximity_threshold:
                    snatch_time = None
                    associated = True

            current_tracked_bags[bag_id] = {
                "center": bag_center,
                "min_person_dist": min_p_dist,
                "snatch_start_time": snatch_time,
                "bbox": bag_bbox.tolist(),
                "associated": associated,
                "timestamp": float(timestamp),
            }
            if (
                snatch_time is not None
                and float(timestamp) - snatch_time >= self.confirm_sec
            ):
                log.info("event=bag_alert dist=%.1f ts=%.3f", min_p_dist, timestamp)
                output = {
                    "alert": True,
                    "bag_id": bag_id,
                    "bag_bbox": bag_bbox.tolist(),
                    "person_bbox": nearest_p_bbox.tolist()
                    if nearest_p_bbox is not None
                    else [],
                    "distance": round(float(min_p_dist), 2)
                    if math.isfinite(min_p_dist)
                    else None,
                    "separation_speed_px_s": round(separation_speed, 2),
                    "rapid_separation": separation_speed > self.speed_threshold,
                    "message": f"Unattended bag candidate: separation persisted {self.confirm_sec:g}s.",
                }
        self.tracked_bags = current_tracked_bags
        return output
