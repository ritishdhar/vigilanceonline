"""Scenario orchestration. Timestamp-first, no prints, no swallowed errors.

Fixes vs legacy ``core/pipeline.py``:
- ``print(track_ids)`` per-frame debug output removed (debug log instead).
- Broad ``except Exception: print(...)`` replaced with logged traceback;
  ``config.strict=True`` re-raises for debugging, otherwise other scenarios
  still run (failure isolation, not silence: everything is logged).
- Track/keypoint pairing moved to ``core.tracked_person.pair_detections``.
- ``Detector`` is constructed from config (paths/tracker/imgsz/device are
  configurable) and is injectable for tests (``Pipeline(detector=fake)``).
- ``process(frame, timestamp)`` makes timestamps explicit; when omitted the
  pipeline clock (``frame_index / fps``) provides them -- never frame counts
  as event timing, never ``time.time()`` inside scenario logic.
- ``reset()`` clears frame clock + all scenario state for deterministic replay.
- Multi-person: detections flow through ``TrackRegistry`` (stable identities,
  explicit LOST/REAPPEARED transitions, ``recently_lost`` window) and every
  frame updates ``TrackerDiagnostics``. Query API: ``all_people``,
  ``active_people``, ``recently_lost``, ``person_by_id``.
"""

from __future__ import annotations

import traceback
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import numpy as np

import scenarios.bag_logic as bag_logic
import scenarios.fall_logic as fall_logic
from config import VigilanceConfig
from core.diagnostics import TrackerDiagnostics
from core.graph import InteractionGraph, SceneCandidate, SceneVerdict, reason_scene
from core.paths import resolve_asset
from core.logging_utils import get_logger
from core.pairs import PairManager, generate_candidate_pairs
from core.skeleton import normalize_person
from core.stgcn_adapter import STGCNPPAdapter, is_buffer_ready
from core.track_registry import TrackEvent, TrackRegistry
from core.tracked_person import TrackedPerson

log = get_logger("pipeline")


@dataclass
class PipelineResult:
    persons: List[TrackedPerson] = field(default_factory=list)
    alerts: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    timestamp: float = 0.0
    frame_index: int = 0
    det_results: Any = None
    transitions: List[TrackEvent] = field(default_factory=list)
    diagnostics: Dict[str, Any] = field(default_factory=dict)
    pair_keys: List[tuple] = field(default_factory=list)
    scene: Optional[SceneVerdict] = None
    fight_status: str = "UNAVAILABLE"
    scenario_errors: Dict[str, str] = field(default_factory=dict)
    pair_scores: Dict[str, float] = field(default_factory=dict)
    model_status: Dict[str, Any] = field(default_factory=dict)
    threat_detections: list = field(default_factory=list)
    threat_models: dict = field(default_factory=dict)


class Pipeline:
    def __init__(
        self,
        config: Optional[VigilanceConfig] = None,
        detector=None,
        pair_scorer=None,
        threat_detector=None,
    ):
        self.config = config or VigilanceConfig()
        self.config.validate()
        self.threat_detector = threat_detector
        # Injected detectors are test/custom integrations; their auxiliary
        # detector is injectable too. Normal entrypoints share core ownership.
        if (
            detector is None
            and self.threat_detector is None
            and (self.config.enable_fire or self.config.enable_threats)
        ):
            from core.threat_detection import SurveillanceThreatDetector

            self.threat_detector = SurveillanceThreatDetector(
                conf=self.config.conf,
                imgsz=self.config.imgsz,
                device=self.config.device,
                enable_fire_alerts=self.config.enable_fire,
                enable_weapon_alerts=self.config.enable_threats,
                enable_bag_alerts=False,
            )
        if detector is not None:
            self.detector = detector
        else:
            from core.detector import Detector

            self.detector = Detector(
                pose_model_path=self.config.pose_weights,
                det_model_path=self.config.det_weights,
                tracker=self.config.tracker,
                fallback_tracker=self.config.tracker_fallback,
                conf=self.config.conf,
                imgsz=self.config.imgsz,
                device=self.config.device,
                max_people=self.config.max_people,
                load_object_model=self.config.enable_bag,
            )
        self.registry = TrackRegistry(
            lost_timeout_sec=self.config.lost_timeout_sec,
            lost_grace_sec=self.config.lost_grace_sec,
        )
        self.track_diagnostics = TrackerDiagnostics()
        fc = self.config.fight_cfg
        self.pair_manager = PairManager(
            pair_grace_seconds=fc.pair_grace_seconds,
            history_seconds=fc.window_seconds,
        )
        self.graph = InteractionGraph()
        self.pair_adapter = STGCNPPAdapter(
            temporal_length=fc.temporal_length,
            window_seconds=fc.window_seconds,
            scorer=None,
            device=self.config.device if fc.model.device == "auto" else fc.model.device,
            freeze_backbone=fc.model.freeze_backbone,
            num_classes=fc.model.num_classes,
        )
        self._pair_score_fn = pair_scorer
        if fc.model.autoload and self.config.enable_fight:
            self.load_fight_model()
        self.frame_index = 0
        self._detector_epoch = None
        self.fight_status = "UNAVAILABLE" if self.config.enable_fight else "DISABLED"
        self._scenarios: List[tuple] = []
        if self.config.enable_fall:
            self._scenarios.append(
                (
                    "Fall Detection",
                    fall_logic.FallDetector(
                        angle_deg=self.config.fall_angle_deg,
                        stillness_sec=self.config.fall_stillness_sec,
                        min_visible_sec=self.config.fall_min_visible_sec,
                    ),
                    "persons",
                )
            )
        if self.config.enable_bag:
            self._scenarios.append(
                (
                    "Abandoned Bag",
                    bag_logic.BagDetector(
                        proximity_threshold=self.config.bag_proximity_px,
                        speed_threshold=self.config.bag_separation_px,
                        confirm_sec=self.config.bag_confirm_sec,
                    ),
                    "det",
                )
            )
        log.info(
            "event=pipeline_ready scenarios=%s fps=%.2f fight_path=%s",
            [name for name, _, _ in self._scenarios],
            self.config.fps,
            "stgcn" if self.config.enable_fight else "off",
        )

    # ------------------------------------------------------------------
    def reset(self) -> None:
        """Clear frame clock + registry + diagnostics + pair/graph + scenario state."""
        self.frame_index = 0
        self._reset_observations()
        self._detector_epoch = None
        if hasattr(self.detector, "reset") and callable(self.detector.reset):
            self.detector.reset()
        log.info("event=pipeline_reset")

    def _reset_observations(self):
        """Reset temporal state without restarting the acquisition clock."""
        self.registry.reset()
        self.track_diagnostics.reset()
        self.pair_manager.reset()
        self.graph.reset()
        self.fight_status = "UNAVAILABLE" if self.config.enable_fight else "DISABLED"
        for _, module, _ in self._scenarios:
            reset = getattr(module, "reset", None)
            if callable(reset):
                reset()
        if self.threat_detector is not None:
            self.threat_detector.reset()

    @property
    def timestamp(self) -> float:
        return self.frame_index / float(self.config.fps)

    def load_fight_model(self, path: Optional[str] = None):
        """Load the ST-GCN++ fight model; explicit and loud about absence."""
        import os

        fc = self.config.fight_cfg
        ckpt = resolve_asset(
            path
            or os.environ.get("VIGILANCE_FIGHT_MODEL_CHECKPOINT")
            or os.environ.get("VIGILANCE_FIGHT_CHECKPOINT")
            or fc.model.checkpoint
        )
        if not os.path.exists(ckpt):
            if path is not None:
                raise FileNotFoundError(
                    f"Requested fight checkpoint does not exist: {ckpt}"
                )
            log.warning("event=fight_model_absent path=%s (repo stays usable)", ckpt)
            return None
        return self.pair_adapter.load_model(
            ckpt,
            num_classes=fc.model.num_classes,
            freeze_backbone=fc.model.freeze_backbone,
        )

    # ------------------------------------------------------------------
    def process(
        self, frame: np.ndarray, timestamp: Optional[float] = None
    ) -> PipelineResult:
        ts = float(self.timestamp if timestamp is None else timestamp)
        if not np.isfinite(ts):
            raise ValueError("timestamp must be finite")
        detection = self.detector.detect(frame, timestamp=ts)
        epoch = getattr(detection, "track_epoch", 0)
        if self._detector_epoch is not None and epoch != self._detector_epoch:
            self._reset_observations()
        self._detector_epoch = epoch
        transitions = self.registry.update(detection.persons, ts)
        self.registry.drain_transitions()
        persons = self.registry.active_people()
        self.track_diagnostics.update(
            len(persons),
            [p.track_id for p in persons],
            transitions,
        )
        log.debug(
            "event=frame frame=%d ts=%.3f persons=%s",
            self.frame_index,
            ts,
            [p.track_id for p in persons],
        )

        alerts: Dict[str, Dict[str, Any]] = {}
        scenario_errors = {}
        threat_frame = None
        if self.threat_detector is not None:
            try:
                threat_frame = self.threat_detector.detect(frame, persons, ts)
                alerts.update(threat_frame.alerts)
                if any(
                    not model.get("ready") for model in threat_frame.models.values()
                ):
                    scenario_errors["Auxiliary Threats"] = "model unavailable"
            except Exception as exc:
                scenario_errors["Auxiliary Threats"] = type(exc).__name__
                log.exception("event=auxiliary_error")
                if self.config.strict:
                    raise
        for name, module, kind in self._scenarios:
            try:
                if kind == "det":
                    res = module.update(detection.det_results, ts)
                else:
                    res = module.update(persons, ts)
                if res and res.get("alert", False):
                    alerts[name] = res
                    log.info("event=alert scenario=%s ts=%.3f data=%s", name, ts, res)
            except Exception as exc:
                scenario_errors[name] = type(exc).__name__
                # Isolated + LOUD: logged with traceback, never silently swallowed.
                log.error(
                    "event=scenario_error scenario=%s frame=%d ts=%.3f error=%r\n%s",
                    name,
                    self.frame_index,
                    ts,
                    exc,
                    traceback.format_exc(),
                )
                if self.config.strict:
                    raise

        try:
            if not self.config.enable_fight:
                self.graph.reset()
                self.pair_manager.reset()
                verdict, live_pairs = None, []
                self.fight_status = "DISABLED"
            else:
                h, w = (
                    (int(frame.shape[0]), int(frame.shape[1]))
                    if frame is not None and getattr(frame, "ndim", 0) >= 2
                    else (1080, 1920)
                )
                self.pair_adapter.img_hw = (float(h), float(w))
                verdict = self.run_pair_graph(persons, ts)
                live_pairs = self.pair_manager.open_tracks()
        except Exception as exc:
            log.error("event=pair_graph_error frame=%d error=%r", self.frame_index, exc)
            if self.config.strict:
                raise
            self.fight_status = "ERROR"
            self.graph.reset()
            scenario_errors["Violence/Fight"] = type(exc).__name__
            verdict = SceneVerdict(SceneCandidate.ERROR)
            live_pairs = []

        if verdict is not None and verdict.scene in (
            SceneCandidate.FIGHT,
            SceneCandidate.MULTI_PERSON_FIGHT,
            SceneCandidate.GROUP_ASSAULT,
        ):
            alerts["Violence/Fight"] = {
                "alert": True,
                "scene": verdict.scene.value,
                "confidence": max(
                    (e.score for e in self.graph.edges.values() if e.score is not None),
                    default=0.0,
                ),
                "members": sorted(
                    {p for incident in verdict.incidents for p in incident.members}
                ),
                "message": "Possible violent interaction detected; review the evidence.",
                "incidents": [
                    {
                        "members": incident.members,
                        "scene": incident.candidate.value,
                        "identities": [
                            (p.track_id, p.generation)
                            for p in persons
                            if p.track_id in incident.members
                        ],
                    }
                    for incident in verdict.incidents
                ],
            }

        result = PipelineResult(
            persons=persons,
            alerts=alerts,
            timestamp=ts,
            frame_index=self.frame_index,
            det_results=detection.det_results,
            transitions=transitions,
            diagnostics=self.track_diagnostics.summary(),
            pair_keys=[t.key for t in live_pairs],
            scene=verdict,
            fight_status=self.fight_status,
            scenario_errors=scenario_errors,
            pair_scores={
                str(key): edge.score
                for key, edge in self.graph.edges.items()
                if edge.score is not None
            },
            model_status={
                "ready": self.pair_adapter.is_ready,
                "head_trained": self.pair_adapter.head_trained,
                "checkpoint": self.pair_adapter.checkpoint.path,
            },
            threat_detections=threat_frame.detections if threat_frame else [],
            threat_models=threat_frame.models if threat_frame else {},
        )
        self.frame_index += 1
        return result

    def run_pair_graph(
        self, persons: List[TrackedPerson], timestamp: float
    ) -> SceneVerdict:
        fc = self.config.fight_cfg
        skeletons = {p.track_id: normalize_person(p) for p in persons}
        eligible = [p for p in persons if p.age_frames >= fc.min_track_age_frames]
        candidates = generate_candidate_pairs(
            eligible,
            skeletons,
            fc.pair_max_distance,
            fc.min_person_visibility,
            fc.max_candidate_pairs,
        )
        live_pairs = self.pair_manager.update(candidates, skeletons, timestamp)
        self.graph.reset()
        self.graph.ingest_tracks(live_pairs, timestamp)
        ready = [
            t
            for t in live_pairs
            if is_buffer_ready(t, fc.window_seconds)
            and timestamp - t.last_scored_at + 1e-9 >= fc.inference_interval_sec
        ]
        if self._pair_score_fn is not None:
            if ready:
                self._store_scores(ready, self._pair_score_fn(ready), timestamp)
        elif self.pair_adapter.is_ready and self.pair_adapter.head_trained:
            if ready:
                batch = np.stack(
                    [self.pair_adapter.preprocess_pair(t) for t in ready], axis=0
                )
                self._store_scores(
                    ready, self.pair_adapter.predict_batch(batch), timestamp
                )
        else:
            self.fight_status = "UNAVAILABLE"
            return SceneVerdict(SceneCandidate.UNAVAILABLE)
        for track in live_pairs:
            if track.last_score is not None:
                self.graph.set_score(track.key, track.last_score)
        verdict = reason_scene(self.graph, fc.graph)
        self.fight_status = (
            "ASSESSED"
            if live_pairs
            and all(t.last_score is not None for t in live_pairs)
            and not (len(persons) >= 2 and len(eligible) < 2)
            else "WARMING_UP"
        )
        if verdict.scene == SceneCandidate.NO_FIGHT and self.fight_status != "ASSESSED":
            return SceneVerdict(SceneCandidate.WARMING_UP)
        return verdict

    @staticmethod
    def _store_scores(tracks, scores, timestamp):
        scores = list(scores)
        if len(scores) != len(tracks) or any(
            not np.isfinite(s) or not 0 <= s <= 1 for s in scores
        ):
            raise ValueError("scorer must return one finite probability per pair")
        for track, score in zip(tracks, scores):
            track.last_score = float(score)
            track.last_scored_at = timestamp

    def all_people(self) -> List[TrackedPerson]:
        return self.registry.all_people()

    def active_people(self) -> List[TrackedPerson]:
        return self.registry.active_people()

    def recently_lost(self) -> List[TrackedPerson]:
        return self.registry.recently_lost()

    def person_by_id(self, track_id: int) -> Optional[TrackedPerson]:
        return self.registry.get(track_id)
