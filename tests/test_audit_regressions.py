"""Behavioral regressions for the architectural and security audit."""

from copy import deepcopy
from hashlib import sha256
from io import BytesIO
import json
from pathlib import Path
import sys
import threading
from types import SimpleNamespace

import numpy as np
import pytest

from config import VigilanceConfig
from core.demo import ScriptedDetector, black_frames, make_person
from core.graph import SceneCandidate
from core.pipeline import Pipeline
from core.replay import compare_runs, frame_record
from core.source_utils import CaptureClock, authorize_source, redact_source
from core.stgcn_adapter import STGCNPPAdapter, resample_buffer
from core.track_registry import TrackRegistry
from core.video_upload import VideoUploadError, save_video_upload


def pipeline(script=None, scorer=None, **kwargs):
    cfg = VigilanceConfig(enable_bag=False, **kwargs)
    cfg.fight_cfg.min_track_age_frames = 1
    return Pipeline(
        cfg,
        detector=ScriptedDetector(script or [[(1, 50), (2, 100)]]),
        pair_scorer=scorer,
    )


@pytest.mark.parametrize("fps", [23.976, 29.97, 30.0])
def test_fractional_and_jittered_clocks_reach_inference(fps):
    calls = []
    pipe = pipeline(
        scorer=lambda tracks: calls.append(len(tracks)) or [0.95] * len(tracks)
    )
    frame = black_frames(1)[0]
    for i in range(150):
        result = pipe.process(frame, i / fps + (i % 3) * 0.0001)
    assert calls
    assert result.scene.scene == SceneCandidate.FIGHT
    assert result.alerts["Violence/Fight"]["confidence"] == 0.95
    record = frame_record(
        result.frame_index, result.timestamp, result.persons, result.alerts, result
    )
    assert record["pair_scores"] and record["fight_status"] == "ASSESSED"
    assert record["scene"]["scene"] == "FIGHT"


def fallen_person(generation=0):
    person = make_person(1)
    person.generation = generation
    person.keypoints[:, 0] = np.arange(17) * 10
    person.keypoints[:, 1] = 150
    return person


def test_missing_observations_do_not_manufacture_fall():
    person = fallen_person()
    detector = SimpleNamespace(
        detect=lambda frame, timestamp: SimpleNamespace(
            persons=[person] if timestamp == 0 else [], det_results=[]
        )
    )
    pipe = Pipeline(VigilanceConfig(enable_bag=False), detector=detector)
    for ts in [0, 0.2, 0.4, 0.6]:
        result = pipe.process(black_frames(1)[0], ts)
        assert not result.alerts
    assert not result.persons
    assert not pipe.registry.get(1).present


def test_scenario_state_is_isolated_and_generation_aware():
    a, b = pipeline(), pipeline()
    fall_a, fall_b = a._scenarios[0][1], b._scenarios[0][1]
    assert fall_a is not fall_b
    for ts in [0, 0.4, 0.8, 1.2]:
        response = fall_a.update([fallen_person()], ts)
        assert not response["alert"]
    response = fall_a.update([fallen_person()], 1.6)
    assert response["alert"]
    assert not fall_a.update([fallen_person(1)], 1.7)["alert"]
    assert not fall_b.history
    b.reset()
    assert fall_a.history


def test_unavailable_warmup_error_are_not_clear():
    frame = black_frames(1)[0]
    assert pipeline().process(frame).scene.scene == SceneCandidate.UNAVAILABLE
    pipe = pipeline(scorer=lambda tracks: [0.1] * len(tracks))
    assert pipe.process(frame).scene.scene == SceneCandidate.WARMING_UP
    pipe.run_pair_graph = lambda *args: (_ for _ in ()).throw(RuntimeError("failed"))
    result = pipe.process(frame)
    assert result.fight_status == "ERROR" and result.scenario_errors


def test_empty_pair_set_is_not_reported_as_assessed_no_fight():
    frame = black_frames(1)[0]
    pipe = Pipeline(
        VigilanceConfig(enable_bag=False),
        detector=ScriptedDetector([[]]),
        pair_scorer=lambda tracks: [],
    )

    result = pipe.process(frame)

    assert result.scene.scene == SceneCandidate.WARMING_UP
    assert result.fight_status == "WARMING_UP"
    assert not result.pair_scores


def test_configuration_controls_scenarios_and_environment(monkeypatch):
    monkeypatch.setenv("VIGILANCE_FALL_STILLNESS_SEC", "0.8")
    cfg = VigilanceConfig(
        enable_bag=True, fall_angle_deg=70, bag_confirm_sec=3
    ).from_env()
    pipe = Pipeline(cfg, detector=ScriptedDetector([[]]))
    assert pipe._scenarios[0][1].angle_deg == 70
    assert pipe._scenarios[1][1].confirm_sec == 3
    assert pipe._scenarios[0][1].stillness_sec == 0.8


def test_live_clock_uses_elapsed_time_and_file_clock_uses_media():
    values = iter([100, 102, 106])
    clock = CaptureClock(True, 30, monotonic=lambda: next(values))
    assert clock.timestamp(0) == 2
    assert clock.timestamp(1) == 6
    file_clock = CaptureClock(False, 25)
    assert file_clock.timestamp(0, 500) == 0.5
    assert file_clock.timestamp(1, 0) > 0.5


def test_removed_registry_and_pairs_are_evicted():
    reg = TrackRegistry(lost_timeout_sec=0.1, lost_grace_sec=0.2)
    for i in range(200):
        reg.update([make_person(i)], float(i))
        reg.update([], i + 0.5)
    assert not reg._tracks
    pipe = pipeline()
    for i in range(100):
        people = [make_person(i * 2), make_person(i * 2 + 1, 150)]
        pipe.run_pair_graph(people, float(i))
        pipe.run_pair_graph([], i + 0.6)
    pipe.run_pair_graph([], 103.0)
    assert not pipe.pair_manager.tracks


def test_resampling_matches_reference_nearest_neighbor():
    times = [0, 0.03, 0.09, 0.15, 0.21, 0.34]
    buffer = [{"t": t} for t in times]
    actual = resample_buffer(buffer, 0.3, 20)
    grid = [0.04 + (i + 0.5) * 0.3 / 20 for i in range(20)]
    expected = [min(buffer, key=lambda entry: abs(entry["t"] - t)) for t in grid]
    assert actual == expected


def test_vectorized_normalization_matches_individual_frames():
    from core.skeleton import frame_normalize

    rng = np.random.default_rng(42)
    xy = rng.normal(size=(12, 17, 2)).astype(np.float32) * 200
    confidence = rng.uniform(size=(12, 17)).astype(np.float32)
    confidence[:, 3] = 0
    actual = frame_normalize(xy, confidence, (240, 320))
    expected = np.stack(
        [frame_normalize(x, c, (240, 320)) for x, c in zip(xy, confidence)]
    )
    np.testing.assert_array_equal(actual, expected)


def test_checkpoint_rejection_never_retries_unrestricted(monkeypatch, tmp_path):
    calls = []

    def reject(*args, **kwargs):
        calls.append(kwargs["weights_only"])
        raise ValueError("not a safe tensor archive")

    monkeypatch.setitem(sys.modules, "torch", SimpleNamespace(load=reject))
    path = tmp_path / "bad.pth"
    path.write_bytes(b"untrusted")
    with pytest.raises(ValueError, match="restricted"):
        STGCNPPAdapter().load_model(str(path))
    assert calls == [True]


def test_download_verifies_digest_and_replaces_partial_cache(monkeypatch, tmp_path):
    import core.model_security as security

    payload = b"verified artifact"
    destination = tmp_path / "model.pt"
    destination.write_bytes(b"partial")
    monkeypatch.setattr(security, "urlopen", lambda *args, **kwargs: BytesIO(payload))
    security.download_verified(
        "https://example.invalid/model", destination, sha256(payload).hexdigest()
    )
    assert destination.read_bytes() == payload
    with pytest.raises(ValueError, match="checksum"):
        security.download_verified(
            "https://example.invalid/model", destination, "0" * 64
        )
    assert not list(tmp_path.glob("*.part"))
    assert destination.read_bytes() == payload


def test_auxiliary_offline_cache_and_download_url_are_verified(monkeypatch, tmp_path):
    import core.threat_detection as module

    monkeypatch.setattr(module, "ROOT", tmp_path)
    detector = module.SurveillanceThreatDetector(download_missing=True)
    calls = []

    def download(url, destination, expected):
        calls.append(url)
        assert detector.revision in url and "resolve/main" not in url
        assert url.endswith("?download=true")
        destination.parent.mkdir(parents=True)
        destination.write_bytes(b"approved")
        return str(destination)

    detector.expected_sha256 = sha256(b"approved").hexdigest()
    monkeypatch.setattr(module, "download_verified", download)
    path = detector._resolve_weight()
    detector.download_missing = False
    assert detector._resolve_weight() == path
    assert len(calls) == 1


def test_source_authorization_and_credential_redaction(tmp_path):
    secret = "rtsp://alice:password@camera.local/feed?token=secret"
    assert redact_source(secret) == "rtsp://camera.local/feed"
    assert authorize_source("0", tmp_path) == "0"
    with pytest.raises(ValueError):
        authorize_source(secret, tmp_path)
    assert authorize_source(secret, tmp_path, [secret]) == secret


def test_binary_boundary_bytes_are_preserved_and_truncation_rejected(tmp_path):
    payload = b"header--demo--literal-bytes\x00\xff"
    body = (
        b'--demo\r\nContent-Disposition: form-data; name="video"; filename="demo.avi"\r\n\r\n'
        + payload
        + b"\r\n--demo--\r\n"
    )
    path = save_video_upload(
        BytesIO(body), "multipart/form-data; boundary=demo", len(body), tmp_path
    )
    assert path.read_bytes() == payload
    with pytest.raises(VideoUploadError):
        save_video_upload(
            BytesIO(body[:-12]),
            "multipart/form-data; boundary=demo",
            len(body) - 12,
            tmp_path,
        )
    assert list(tmp_path.iterdir()) == [path]


def test_replay_comparison_detects_pose_and_payload_changes():
    a = [
        {
            "frame": 0,
            "persons": [make_person(1).to_dict()],
            "alerts": {"Fall": {"person_id": 1}},
        }
    ]
    b = deepcopy(a)
    b[0]["persons"][0]["keypoints"][0] += 1
    assert not compare_runs(a, b)["identical"]
    b = deepcopy(a)
    b[0]["alerts"]["Fall"]["person_id"] = 99
    assert not compare_runs(a, b)["identical"]


def test_gradual_separation_starts_abandonment_timer():
    from scenarios.bag_logic import BagDetector

    class Tensor:
        def __init__(self, value):
            self.value = np.array(value)

        def cpu(self):
            return self

        def numpy(self):
            return self.value

    detector = BagDetector(confirm_sec=1)
    for ts, distance in [(0, 140), (0.1, 155), (0.2, 170), (2, 250)]:
        boxes = SimpleNamespace(
            xyxy=Tensor([[0, 0, 10, 10], [distance, 0, distance + 10, 10]]),
            cls=Tensor([26, 0]),
            conf=Tensor([0.9, 0.9]),
        )

        class Boxes(SimpleNamespace):
            def __len__(self):
                return 2

        output = detector.update([SimpleNamespace(boxes=Boxes(**vars(boxes)))], ts)
    assert output["alert"]


def test_notification_event_identity_and_failed_attempt_backoff(monkeypatch):
    from core.alert_dispatch import (
        TelegramConfig,
        TelegramNotifier,
        incident_from_state,
    )
    from ui import LiveState

    state = LiveState()
    try:
        state.record_event(1, "FIGHT", {})
        first = state.active_event_id
        state.record_event(70, "FIGHT", {})
        assert first == state.active_event_id
        state.record_event(71, "NO_FIGHT", {})
        state.record_event(72, "FIGHT", {})
        assert first != state.active_event_id
    finally:
        state.uploads.close()
    notifier = TelegramNotifier(
        TelegramConfig(enabled=True, bot_token="test", chat_id="1")
    )
    calls = []

    def fail(*args):
        calls.append(True)
        raise OSError("offline")

    monkeypatch.setattr(notifier, "_send", fail)
    incident = incident_from_state("FIGHT", {}, 1, event_id=first)
    for now in [100, 100.1, 100.2]:
        assert not notifier.maybe_notify(incident, now=now)
    assert len(calls) == 1


def test_background_notification_submission_does_not_wait_for_transport(monkeypatch):
    from core.alert_dispatch import (
        QueuedNotifier,
        TelegramConfig,
        TelegramNotifier,
        incident_from_state,
    )

    entered, release = threading.Event(), threading.Event()
    notifier = TelegramNotifier(
        TelegramConfig(enabled=True, bot_token="test", chat_id="1", cooldown_sec=0)
    )

    def blocking(*args):
        entered.set()
        release.wait(timeout=2)
        return True

    monkeypatch.setattr(notifier, "_send", blocking)
    worker = QueuedNotifier(notifier)
    try:
        incident = incident_from_state("FIGHT", {}, 1, event_id="stable")
        assert worker.maybe_notify(incident)
        assert entered.wait(timeout=1)
        assert not worker.maybe_notify(incident)
    finally:
        release.set()
        worker.close()


@pytest.mark.parametrize(
    "missing,allow_partial", [(False, False), (True, False), (True, True)]
)
def test_evaluator_reports_scored_clips_and_rejects_incomplete_manifests(
    monkeypatch, tmp_path, missing, allow_partial
):
    import eval_fight
    import core.pipeline

    manifest = tmp_path / "manifest.csv"
    manifest.write_text("video_path,label\ngood.avi,1\nmissing.avi,0\n")

    class Cap:
        def __init__(self, path):
            self.path = path

        def isOpened(self):
            return not (missing and "missing" in self.path)

        def get(self, prop):
            return 30

        def release(self):
            pass

    monkeypatch.setitem(
        sys.modules, "cv2", SimpleNamespace(VideoCapture=Cap, CAP_PROP_FPS=5)
    )
    fake = SimpleNamespace(
        pair_adapter=SimpleNamespace(is_ready=True, head_trained=True)
    )
    monkeypatch.setattr(core.pipeline, "Pipeline", lambda **kwargs: fake)
    monkeypatch.setattr(
        eval_fight,
        "score_clip",
        lambda *args: (
            0.95,
            3,
            {"unique_ids": 2, "avg_people_per_frame": 2, "max_pair_span_sec": 4},
        ),
    )
    evidence = str(tmp_path / "eval.csv")
    args = ["--manifest", str(manifest), "--evidence", evidence]
    if allow_partial:
        args.append("--allow-partial")
    result = eval_fight.main(args)
    if missing and not allow_partial:
        assert result == 2
        assert not Path(evidence).exists()
    else:
        assert result == (2 if missing else 0)
        report = json.loads(Path(evidence + ".summary.json").read_text())
        assert report["scored_clips"] == (1 if missing else 2)
        assert report["complete"] == (not missing)


def test_missing_checkpoint_smoke_test_exits_clearly(monkeypatch):
    pytest.importorskip("torch")
    import model_smoke_test

    monkeypatch.setattr(model_smoke_test, "env_report", lambda: None)
    assert (
        model_smoke_test.main(
            ["--checkpoint", "not-a-checkpoint.pth", "--device", "cpu"]
        )
        == 2
    )
