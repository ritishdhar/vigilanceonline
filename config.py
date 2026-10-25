"""Central configuration for the VIGLANCE / Raksha Netra foundation.

Single source of truth for detector weights, tracker, input source, FPS,
image size, device, scenario toggles, thresholds and evidence output.

No third-party dependency: JSON is supported natively; YAML is supported
opportunistically if ``pyyaml`` happens to be installed (never required).
Environment variables (``VIGILANCE_*``) and CLI flags override file values.
"""

from __future__ import annotations

import json
import os
import math
from numbers import Real
from dataclasses import asdict, dataclass, field


@dataclass
class FightGraphConfig:
    violence_edge_threshold: float = 0.5
    minimum_edges_for_group_assault: int = 2
    minimum_people_for_multi_person_fight: int = 3
    minimum_violent_degree_for_group_assault: int = 2


@dataclass
class FightModelConfig:
    checkpoint: str = "models/stgcnpp/best_fight_stgcnpp_v1.pth"
    num_classes: int = 2  # 0=NO_FIGHT, 1=FIGHT
    freeze_backbone: bool = True  # Prompt 04 default: prove arch before training
    device: str = "auto"  # auto -> cuda if available else cpu
    autoload: bool = False  # load weights during Pipeline init when file exists


@dataclass
class FightConfig:
    pair_max_distance: float = 3.0
    min_person_visibility: float = 0.3
    max_candidate_pairs: int = 16
    window_seconds: float = 4.0
    target_fps: int = 25
    temporal_length: int = 100
    pair_grace_seconds: float = 2.0
    min_track_age_frames: int = 2
    inference_interval_sec: float = 0.2
    model: FightModelConfig = field(default_factory=FightModelConfig)
    graph: FightGraphConfig = field(default_factory=FightGraphConfig)


@dataclass
class VigilanceConfig:
    # --- detector weights (fixed: defaults point at files that exist) ---
    pose_weights: str = "models/yolo/yolo26m-pose.pt"
    det_weights: str = "models/yolo/yolo26m.pt"
    # --- tracker (BoT-SORT primary, ByteTrack fallback) ---
    tracker: str = "botsort.yaml"
    tracker_fallback: str = "bytetrack.yaml"  # engaged once if primary raises
    # --- input ---
    source: str = "0"  # "0" = webcam, else video/image path
    fps: float = 30.0  # fallback when capture FPS is unreadable
    imgsz: int = 640
    device: str = "auto"  # "auto" | "cpu" | "cuda" | "cuda:0" ...
    conf: float = 0.25
    # Safety cap only: the scene is NEVER truncated to 2 people. The
    # registry keeps every identity the detector reports up to this cap.
    max_people: int = 32
    # --- track lifecycle (registry) ---
    lost_timeout_sec: float = 1.0  # gap without sighting before LOST
    lost_grace_sec: float = 5.0  # LOST retention before REMOVED
    # --- scenario enable/disable (violence + fire + fall focus; re-enable others explicitly) ---
    enable_fall: bool = True  # pose-rule fall module (no extra weights)
    enable_bag: bool = False
    enable_fight: bool = True  # ST-GCN++ pair/graph fight path (the active fight path)
    enable_threats: bool = (
        False  # auxiliary YOLO threat model (weapon/fire/bag); opt-in only
    )
    enable_fire: bool = (
        True  # fire/smoke alerts from the threat model; violence + fire focus
    )
    # --- thresholds ---
    fall_angle_deg: float = 45.0
    fall_stillness_sec: float = 1.5
    fall_min_visible_sec: float = 0.5
    bag_proximity_px: float = 150.0
    bag_separation_px: float = 30.0
    bag_confirm_sec: float = 1.5
    # --- pair / graph violence reasoning (ST-GCN++ + interaction graph) ---
    fight_cfg: FightConfig = field(default_factory=FightConfig)
    # --- output / evidence ---
    evidence_dir: str = "evidence"
    strict: bool = (
        False  # True -> re-raise scenario errors (debug); False -> log + continue
    )
    log_level: str = "INFO"
    # --- telegram incident alerts (token via VIGILANCE_TELEGRAM_BOT_TOKEN env) ---
    telegram_enabled: bool = False
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""
    telegram_recipients_db: str = "evidence/telegram_recipients.sqlite3"
    telegram_cooldown_sec: float = 60.0
    telegram_notify_fight: bool = True
    telegram_notify_fall: bool = True
    telegram_notify_fire: bool = True
    telegram_notify_bag: bool = True
    telegram_notify_weapon: bool = True

    # ------------------------------------------------------------------
    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "VigilanceConfig":
        if not isinstance(data, dict):
            raise ValueError("configuration must be an object")
        known = {f for f in cls.__dataclass_fields__}
        filtered = {k: v for k, v in data.items() if k in known}
        cfg = data.get("fight_cfg") or data.get("fight") or {}
        if not isinstance(cfg, dict):
            raise ValueError("fight_cfg must be an object")
        if isinstance(cfg, dict):
            graph = cfg.get("graph", {})
            model_cfg = cfg.get("model", {})
            if not isinstance(graph, dict) or not isinstance(model_cfg, dict):
                raise ValueError("fight graph and model must be objects")
            base = {k: v for k, v in cfg.items() if k not in ("graph", "model")}
            fight = FightConfig(
                **{
                    k: v
                    for k, v in base.items()
                    if k in FightConfig.__dataclass_fields__
                }
            )
            if isinstance(graph, dict) and graph:
                fight.graph = FightGraphConfig(
                    **{
                        k: v
                        for k, v in graph.items()
                        if k in FightGraphConfig.__dataclass_fields__
                    }
                )
            if isinstance(model_cfg, dict) and model_cfg:
                fight.model = FightModelConfig(
                    **{
                        k: v
                        for k, v in model_cfg.items()
                        if k in FightModelConfig.__dataclass_fields__
                    }
                )
            filtered["fight_cfg"] = fight
        return cls(**filtered)

    @classmethod
    def from_json_file(cls, path: str) -> "VigilanceConfig":
        with open(path, "r", encoding="utf-8") as fh:
            return cls.from_dict(json.load(fh, parse_constant=cls._invalid_constant))

    @staticmethod
    def _invalid_constant(value):
        raise ValueError(f"non-finite JSON constant: {value}")

    @classmethod
    def from_file(cls, path: str) -> "VigilanceConfig":
        """Load JSON natively; YAML only if pyyaml is available."""
        from core.paths import resolve_asset

        path = resolve_asset(str(path))
        if path.endswith((".yaml", ".yml")):
            try:
                import yaml  # type: ignore
            except ImportError as exc:
                raise ImportError(
                    f"Config file {path} is YAML but pyyaml is not installed. "
                    "Use a JSON config or install pyyaml."
                ) from exc
            with open(path, "r", encoding="utf-8") as fh:
                return cls.from_dict(yaml.safe_load(fh) or {})
        return cls.from_json_file(path)

    def from_env(self) -> "VigilanceConfig":
        """Override fields from VIGILANCE_* environment variables."""

        def _get(name: str, cast):
            val = os.environ.get(f"VIGILANCE_{name.upper()}")
            if val is None:
                return getattr(self, name)
            if cast is bool:
                return val.strip().lower() in ("1", "true", "yes", "on")
            return cast(val)

        for name, cast in [
            ("pose_weights", str),
            ("det_weights", str),
            ("tracker", str),
            ("tracker_fallback", str),
            ("source", str),
            ("fps", float),
            ("imgsz", int),
            ("device", str),
            ("conf", float),
            ("max_people", int),
            ("lost_timeout_sec", float),
            ("lost_grace_sec", float),
            ("enable_fall", bool),
            ("enable_bag", bool),
            ("enable_fight", bool),
            ("enable_threats", bool),
            ("enable_fire", bool),
            ("fall_angle_deg", float),
            ("fall_stillness_sec", float),
            ("fall_min_visible_sec", float),
            ("bag_proximity_px", float),
            ("bag_separation_px", float),
            ("bag_confirm_sec", float),
            ("evidence_dir", str),
            ("strict", bool),
            ("log_level", str),
            ("telegram_enabled", bool),
            ("telegram_bot_token", str),
            ("telegram_chat_id", str),
            ("telegram_recipients_db", str),
            ("telegram_cooldown_sec", float),
            ("telegram_notify_fight", bool),
            ("telegram_notify_fall", bool),
            ("telegram_notify_fire", bool),
            ("telegram_notify_bag", bool),
            ("telegram_notify_weapon", bool),
            ("fight_pair_max_distance", float),
            ("fight_min_person_visibility", float),
            ("fight_max_candidate_pairs", int),
            ("fight_window_seconds", float),
            ("fight_target_fps", int),
            ("fight_temporal_length", int),
            ("fight_pair_grace_seconds", float),
            ("fight_min_track_age_frames", int),
            ("fight_violence_edge_threshold", float),
            ("fight_minimum_edges_for_group_assault", int),
            ("fight_minimum_people_for_multi_person_fight", int),
            ("fight_minimum_violent_degree_for_group_assault", int),
        ]:
            if name.startswith("fight_") and name not in self.__dataclass_fields__:
                continue
            setattr(self, name, _get(name, cast))
        _fight_env = {
            "fight_pair_max_distance": ("pair_max_distance", False, float),
            "fight_min_person_visibility": ("min_person_visibility", False, float),
            "fight_max_candidate_pairs": ("max_candidate_pairs", False, int),
            "fight_window_seconds": ("window_seconds", False, float),
            "fight_target_fps": ("target_fps", False, int),
            "fight_temporal_length": ("temporal_length", False, int),
            "fight_pair_grace_seconds": ("pair_grace_seconds", False, float),
            "fight_min_track_age_frames": ("min_track_age_frames", False, int),
            "fight_inference_interval_sec": ("inference_interval_sec", False, float),
            "fight_violence_edge_threshold": ("violence_edge_threshold", True, float),
            "fight_minimum_edges_for_group_assault": (
                "minimum_edges_for_group_assault",
                True,
                int,
            ),
            "fight_minimum_people_for_multi_person_fight": (
                "minimum_people_for_multi_person_fight",
                True,
                int,
            ),
            "fight_minimum_violent_degree_for_group_assault": (
                "minimum_violent_degree_for_group_assault",
                True,
                int,
            ),
        }
        _model_env = {
            "fight_model_checkpoint": ("checkpoint", str),
            "fight_model_num_classes": ("num_classes", int),
            "fight_model_freeze_backbone": ("freeze_backbone", bool),
            "fight_model_device": ("device", str),
            "fight_model_autoload": ("autoload", bool),
        }
        for env_name, (attr, is_graph, cast) in _fight_env.items():
            val = os.environ.get(f"VIGILANCE_{env_name.upper()}")
            if val is None:
                continue
            target = self.fight_cfg.graph if is_graph else self.fight_cfg
            if cast is bool:
                setattr(target, attr, val.strip().lower() in ("1", "true", "yes", "on"))
            else:
                setattr(target, attr, cast(val))
        for env_name, (attr, cast) in _model_env.items():
            val = os.environ.get(f"VIGILANCE_{env_name.upper()}")
            if val is None:
                continue
            if cast is bool:
                setattr(
                    self.fight_cfg.model,
                    attr,
                    val.strip().lower() in ("1", "true", "yes", "on"),
                )
            else:
                setattr(self.fight_cfg.model, attr, cast(val))
        return self

    def apply_overrides(self, **kwargs) -> "VigilanceConfig":
        known = set(self.__dataclass_fields__)
        for key, value in kwargs.items():
            if value is None:
                continue
            if key not in known:
                raise KeyError(f"Unknown config key: {key}")
            setattr(self, key, value)
        return self

    def validate(self) -> None:
        if (
            not isinstance(self.fight_cfg, FightConfig)
            or not isinstance(self.fight_cfg.model, FightModelConfig)
            or not isinstance(self.fight_cfg.graph, FightGraphConfig)
        ):
            raise ValueError("invalid nested fight configuration")
        for obj in (self, self.fight_cfg, self.fight_cfg.model, self.fight_cfg.graph):
            for name, spec in obj.__dataclass_fields__.items():
                value = getattr(obj, name)
                if spec.type in ("float", "int", float, int):
                    if (
                        isinstance(value, bool)
                        or not isinstance(value, Real)
                        or not math.isfinite(value)
                    ):
                        raise ValueError(f"{name} must be a finite number")
                    if spec.type in ("int", int) and not isinstance(value, int):
                        raise ValueError(f"{name} must be an integer")
                elif spec.type in ("bool", bool) and not isinstance(value, bool):
                    raise ValueError(f"{name} must be a boolean")
        if self.imgsz < 32 or self.imgsz % 32:
            raise ValueError("imgsz must be a positive multiple of 32")
        if self.device not in ("auto", "cpu", "cuda") and not self.device.startswith(
            "cuda:"
        ):
            raise ValueError("invalid detector device")
        if not 0 <= self.fall_angle_deg <= 90:
            raise ValueError("fall angle must be in [0,90]")
        for name in (
            "fall_stillness_sec",
            "fall_min_visible_sec",
            "bag_confirm_sec",
            "telegram_cooldown_sec",
        ):
            if getattr(self, name) < 0:
                raise ValueError(f"{name} must be >= 0")
        if self.bag_proximity_px <= 0 or self.bag_separation_px < 0:
            raise ValueError("invalid bag thresholds")
        if self.fps <= 0:
            raise ValueError("fps must be > 0")
        if self.max_people < 1:
            raise ValueError("max_people must be >= 1")
        if self.lost_timeout_sec <= 0 or self.lost_grace_sec <= 0:
            raise ValueError("lost timeouts must be > 0")
        if self.lost_grace_sec < self.lost_timeout_sec:
            raise ValueError("lost_grace_sec must be >= lost_timeout_sec")
        if not 0.0 < self.conf < 1.0:
            raise ValueError("conf must be in (0, 1)")
        fc = self.fight_cfg
        if fc.inference_interval_sec < 0:
            raise ValueError("fight inference_interval_sec must be >= 0")
        if fc.pair_max_distance <= 0:
            raise ValueError("fight.pair_max_distance must be > 0")
        if not 0.0 <= fc.min_person_visibility <= 1.0:
            raise ValueError("fight.min_person_visibility must be in [0, 1]")
        if fc.max_candidate_pairs < 0:
            raise ValueError("fight.max_candidate_pairs must be >= 0")
        if fc.window_seconds <= 0 or fc.target_fps <= 0 or fc.temporal_length < 2:
            raise ValueError("fight window/target_fps/temporal_length invalid")
        if fc.pair_grace_seconds < 0 or fc.min_track_age_frames < 0:
            raise ValueError("fight pair grace / min track age must be >= 0")
        if not 0.0 < fc.graph.violence_edge_threshold < 1.0:
            raise ValueError("fight.graph.violence_edge_threshold must be in (0, 1)")
        mc = fc.model
        if (
            fc.graph.minimum_edges_for_group_assault < 1
            or fc.graph.minimum_people_for_multi_person_fight < 2
            or fc.graph.minimum_violent_degree_for_group_assault < 1
        ):
            raise ValueError("invalid graph count thresholds")
        if mc.num_classes != 2:
            raise ValueError("fight.model.num_classes must be 2 (NO_FIGHT/FIGHT)")
        if mc.device not in ("auto", "cpu", "cuda"):
            raise ValueError("fight.model.device must be auto/cpu/cuda")


DEFAULT_CONFIG = VigilanceConfig()


def default_config_dict() -> dict:
    return DEFAULT_CONFIG.to_dict()
