import numpy as np
import pytest

from core.threat_detection import MultiModelThreatDetector


class _Field:
    def __init__(self, values):
        self.values = values

    def int(self):
        return _Field(np.asarray(self.values, dtype=int))

    def cpu(self):
        return self

    def tolist(self):
        return np.asarray(self.values).tolist()


class _Boxes:
    def __init__(self, classes, confidences, boxes):
        self.cls = _Field(classes)
        self.conf = _Field(confidences)
        self.xyxy = _Field(boxes)

    def __len__(self):
        return len(self.conf.values)


class _Result:
    def __init__(self, names, boxes):
        self.names = names
        self.boxes = boxes


class _Model:
    def __init__(self, result):
        self.result = result

    def predict(self, frame, **kwargs):
        return [self.result]


def _model(label, confidence):
    return _Model(
        _Result(
            {0: label},
            _Boxes([0], [confidence], [[10.0, 10.0, 50.0, 50.0]]),
        )
    )


def _detector(**kwargs):
    detector = MultiModelThreatDetector(
        download_missing=False, confirm_frames=2, **kwargs
    )
    detector._load_attempted = True
    return detector


def test_only_surveillance_model_is_configured():
    detector = _detector()
    assert list(detector.weights) == ["surveillance"]
    assert detector.repo == "xDevine/yolo11s-multiclass-surveillance-detection"
    assert "Subh775" in detector.disabled_repo


def test_threat_weights_argument_is_ignored():
    detector = _detector(threat_weights="weights/best.pt")
    assert "threat" not in detector.weights


@pytest.mark.parametrize("label", ["short-gun", "long-gun", "knife"])
def test_visible_weapon_alert_needs_consecutive_detections(label):
    detector = _detector(enable_weapon_alerts=True)
    detector.models = {"surveillance": _model(label, 0.65)}
    frame = np.zeros((80, 80, 3), dtype=np.uint8)
    assert "Weapon Threat" not in detector.detect(frame).alerts
    alert = detector.detect(frame).alerts["Weapon Threat"]
    assert alert["evidence"][0]["label"] == label
    assert alert["confidence"] == 0.65


def test_weapon_alert_requires_enable_flag():
    detector = _detector()
    detector.models = {"surveillance": _model("short-gun", 0.8)}
    frame = np.zeros((80, 80, 3), dtype=np.uint8)
    detector.detect(frame)
    assert "Weapon Threat" not in detector.detect(frame).alerts


def test_low_confidence_weapon_never_alerts():
    detector = _detector(enable_weapon_alerts=True, conf=0.9)
    detector.models = {"surveillance": _model("short-gun", 0.8)}
    frame = np.zeros((80, 80, 3), dtype=np.uint8)
    for _ in range(5):
        assert "Weapon Threat" not in detector.detect(frame).alerts


def test_weapon_confirmation_resets_after_a_missing_detection():
    detector = _detector(enable_weapon_alerts=True)
    detector.models = {"surveillance": _model("knife", 0.65)}
    frame = np.zeros((80, 80, 3), dtype=np.uint8)
    assert not detector.detect(frame).alerts
    detector.models = {}
    assert not detector.detect(frame).alerts
    detector.models = {"surveillance": _model("knife", 0.65)}
    assert not detector.detect(frame).alerts
    assert "Weapon Threat" in detector.detect(frame).alerts


def test_fire_alert_needs_high_confidence_streak():
    detector = _detector()
    detector.models = {"surveillance": _model("fire", 0.9)}
    frame = np.zeros((80, 80, 3), dtype=np.uint8)
    assert "Fire Threat" not in detector.detect(frame).alerts
    assert "Fire Threat" in detector.detect(frame).alerts


def test_fire_alert_respects_enable_flag():
    detector = _detector(enable_fire_alerts=False)
    detector.models = {"surveillance": _model("fire", 0.8)}
    frame = np.zeros((80, 80, 3), dtype=np.uint8)
    for _ in range(5):
        assert "Fire Threat" not in detector.detect(frame).alerts
        assert "Smoke Threat" not in detector.detect(frame).alerts


def test_bag_alert_dark_by_default():
    detector = _detector()
    detector.models = {"surveillance": _model("bag", 0.8)}
    frame = np.zeros((80, 80, 3), dtype=np.uint8)
    for _ in range(5):
        assert "Unattended Bag Candidate" not in detector.detect(frame).alerts
