"""Single-model object evidence for the web demo.

The trained ST-GCN++ model remains the violence classifier. This optional
YOLO model only provides object evidence for weapons, fire/smoke, and bag
candidates.

Exactly ONE auxiliary model is used and downloaded:
``xDevine/yolo11s-multiclass-surveillance-detection`` (YOLO11s, 21 classes:
short-gun, long-gun, knife, fire, smoke, phone, person, car, bag,
backpack, handbag, ...). That single model covers every category VIGLANCE
reasons about, so the former second detector
(``Subh775/Threat-Detection-YOLOv8n``) is disabled: it is never resolved,
never downloaded, and its cross-model "agreement" rule is gone. Weapon
    High-confidence bar (``high_conf``) applies to streak counting for every
    category, so weapon AND fire/smoke alerts all need sustained high-confidence
    detections. Bag alerts stay dark unless explicitly enabled.

``MultiModelThreatDetector`` is a backwards-compatible alias for
``SurveillanceThreatDetector`` (the two-model era). ``threat_weights`` is
still accepted so older callers keep working, but it is ignored with a
warning.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional
from urllib.parse import quote

from core.logging_utils import get_logger
from core.model_security import download_verified, file_sha256, load_yolo
from core.paths import ROOT

log = get_logger("threats")


@dataclass
class ThreatDetection:
    source: str
    label: str
    category: str
    confidence: float
    bbox: List[float]

    def to_dict(self) -> dict:
        return {
            "source": self.source,
            "label": self.label,
            "category": self.category,
            "confidence": round(float(self.confidence), 3),
            "bbox": [round(float(v), 1) for v in self.bbox],
        }


@dataclass
class ThreatFrame:
    detections: List[ThreatDetection] = field(default_factory=list)
    alerts: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    models: Dict[str, Dict[str, Any]] = field(default_factory=dict)


class SurveillanceThreatDetector:
    """Run the surveillance YOLO model with temporal consensus."""

    repo = "xDevine/yolo11s-multiclass-surveillance-detection"
    filename = "weights/best.pt"
    revision = "9a58a5a9da9513c23a315cdd668c0a33093eb177"
    expected_sha256 = "b95e99b25c954177fbbb97029a57d2c8e1450135873240ea2a39dda7aff4375f"
    source_name = "surveillance"
    disabled_repo = "Subh775/Threat-Detection-YOLOv8n"

    WEAPONS = {
        "short-gun",
        "long-gun",
        "gun",
        "pistol",
        "rifle",
        "firearm",
        "knife",
    }
    FIRE = {"fire"}
    SMOKE = {"smoke"}
    BAGS = {"bag", "backpack", "handbag", "suitcase", "purse"}

    def __init__(
        self,
        surveillance_weights: Optional[str] = None,
        threat_weights: Optional[str] = None,
        download_missing: bool = True,
        conf: float = 0.35,
        high_conf: float = 0.85,
        confirm_frames: int = 3,
        imgsz: int = 640,
        device: str = "auto",
        bag_proximity_px: float = 150.0,
        enable_weapon_alerts: bool = False,
        enable_fire_alerts: bool = True,
        enable_bag_alerts: bool = False,
    ):
        if threat_weights:
            log.warning(
                "event=threat_model_disabled repo=%s "
                "(only %s is used; argument ignored)",
                self.disabled_repo,
                self.repo,
            )
        self.weights = {self.source_name: surveillance_weights}
        self.download_missing = bool(download_missing)
        self.conf = float(conf)
        self.high_conf = float(high_conf)
        self.confirm_frames = max(1, int(confirm_frames))
        self.imgsz = int(imgsz)
        self.device = device
        self.bag_proximity_px = float(bag_proximity_px)
        self.enable_weapon_alerts = bool(enable_weapon_alerts)
        self.enable_fire_alerts = bool(enable_fire_alerts)
        self.enable_bag_alerts = bool(enable_bag_alerts)
        self.models: Dict[str, Any] = {}
        self.status: Dict[str, Dict[str, Any]] = {}
        self.streaks: Dict[str, int] = {}
        self._load_attempted = False

    def reset(self) -> None:
        self.streaks.clear()

    def _resolve_weight(self) -> Optional[str]:
        source = self.source_name
        configured = self.weights[source]
        if configured:
            path = Path(configured)
            if path.exists():
                return str(path)
            if not self.download_missing:
                return None
        destination = (
            ROOT
            / "models"
            / "surveillance"
            / f"{self.revision}_{Path(self.filename).name}"
        )
        if destination.is_file() and file_sha256(destination) == self.expected_sha256:
            return str(destination)
        if not self.download_missing:
            return None
        try:
            url = f"https://huggingface.co/{self.repo}/resolve/{self.revision}/{quote(self.filename, safe='/')}?download=true"
            return download_verified(url, destination, self.expected_sha256)
        except Exception as exc:
            self.status[source] = {"ready": False, "error": type(exc).__name__}
            log.warning(
                "event=threat_model_download_failed source=%s error=%s",
                source,
                type(exc).__name__,
            )
            return None

    def _load_models(self) -> None:
        if self._load_attempted:
            return
        self._load_attempted = True
        for source in self.weights:
            path = self._resolve_weight()
            if path is None:
                self.status.setdefault(
                    source,
                    {
                        "ready": False,
                        "error": "weights unavailable",
                    },
                )
                continue
            try:
                self.models[source] = load_yolo(path)
                self.status[source] = {"ready": True, "path": path}
                log.info("event=threat_model_ready source=%s path=%s", source, path)
            except Exception as exc:
                self.status[source] = {"ready": False, "error": type(exc).__name__}
                log.warning(
                    "event=threat_model_load_failed source=%s error=%r", source, exc
                )

    @staticmethod
    def _label(names: Any, index: int) -> str:
        if isinstance(names, dict):
            return str(names.get(index, index)).strip().lower()
        if isinstance(names, (list, tuple)) and 0 <= index < len(names):
            return str(names[index]).strip().lower()
        return str(index)

    def _category(self, label: str) -> Optional[str]:
        normalized = label.replace("_", "-").replace(" ", "-")
        if normalized in self.WEAPONS:
            return "weapon"
        if normalized in self.FIRE:
            return "fire"
        if normalized in self.SMOKE:
            return "smoke"
        if normalized in self.BAGS:
            return "bag"
        return None

    def _predict(self, frame) -> List[ThreatDetection]:
        model = self.models.get(self.source_name)
        if model is None:
            return []
        kwargs = {"conf": self.conf, "imgsz": self.imgsz, "verbose": False}
        if self.device != "auto":
            kwargs["device"] = self.device
        results = model.predict(frame, **kwargs)
        if not results:
            return []
        result = results[0]
        if result.boxes is None or len(result.boxes) == 0:
            return []
        classes = result.boxes.cls.int().cpu().tolist()
        confidences = result.boxes.conf.cpu().tolist()
        boxes = result.boxes.xyxy.cpu().tolist()
        out = []
        for class_id, confidence, bbox in zip(classes, confidences, boxes):
            label = self._label(result.names, int(class_id))
            category = self._category(label)
            if category is not None:
                out.append(
                    ThreatDetection(
                        source=self.source_name,
                        label=label,
                        category=category,
                        confidence=float(confidence),
                        bbox=list(bbox),
                    )
                )
        return out

    @staticmethod
    def _best(detections: Iterable[ThreatDetection], category: str):
        candidates = [d for d in detections if d.category == category]
        return max(candidates, key=lambda d: d.confidence, default=None)

    def _bag_is_separated(self, detection: ThreatDetection, persons) -> bool:
        if not persons:
            return True
        bx = (detection.bbox[0] + detection.bbox[2]) / 2.0
        by = (detection.bbox[1] + detection.bbox[3]) / 2.0
        nearest = min(
            (
                (bx - (p.bbox[0] + p.bbox[2]) / 2.0) ** 2
                + (by - (p.bbox[1] + p.bbox[3]) / 2.0) ** 2
            )
            ** 0.5
            for p in persons
        )
        return nearest > self.bag_proximity_px

    def detect(self, frame, persons=None, timestamp: float = 0.0) -> ThreatFrame:
        self._load_models()
        detections = []
        for source in self.models:
            try:
                detections.extend(self._predict(frame))
                self.status[source] = {
                    "ready": True,
                    "path": self.status.get(source, {}).get("path"),
                }
            except Exception as exc:
                self.status[source] = {"ready": False, "error": type(exc).__name__}
                log.warning(
                    "event=threat_inference_failed source=%s error=%r", source, exc
                )

        best = {
            category: self._best(detections, category)
            for category in ("weapon", "fire", "smoke", "bag")
        }
        for category, detection in best.items():
            if detection is not None and detection.confidence >= self.high_conf:
                self.streaks[category] = self.streaks.get(category, 0) + 1
            else:
                self.streaks[category] = 0

        alerts: Dict[str, Dict[str, Any]] = {}
        weapon = best["weapon"]
        if (
            self.enable_weapon_alerts
            and weapon is not None
            and weapon.confidence >= self.high_conf
            and self.streaks["weapon"] >= self.confirm_frames
        ):
            alerts["Weapon Threat"] = {
                "alert": True,
                "category": "weapon",
                "confidence": round(float(weapon.confidence), 3),
                "evidence": [weapon.to_dict()],
                "message": (
                    "Weapon candidate persisted across frames "
                    "(single surveillance model, no cross-model "
                    "agreement)."
                ),
            }
        for category, title in (("fire", "Fire Threat"), ("smoke", "Smoke Threat")):
            detection = best[category]
            if (
                self.enable_fire_alerts
                and detection is not None
                and self.streaks[category] >= self.confirm_frames
            ):
                alerts[title] = {
                    "alert": True,
                    "category": category,
                    "confidence": round(float(detection.confidence), 3),
                    "message": f"{category.title()} candidate persisted across frames.",
                }
        bag = best["bag"]
        if (
            self.enable_bag_alerts
            and bag is not None
            and self._bag_is_separated(bag, persons or [])
            and self.streaks["bag"] >= self.confirm_frames
        ):
            alerts["Unattended Bag Candidate"] = {
                "alert": True,
                "category": "bag",
                "confidence": round(float(bag.confidence), 3),
                "message": "Bag candidate remains separated from tracked people.",
            }
        return ThreatFrame(
            detections=detections, alerts=alerts, models=dict(self.status)
        )


# The two-model era name. Kept so existing imports/callers keep working;
# behaviour is the single surveillance model above.
MultiModelThreatDetector = SurveillanceThreatDetector
