"""CLI/config precedence tests: unset flags must never clobber file values."""

import json

import pytest

from config import VigilanceConfig


def _write_config(tmp_path, **overrides):
    cfg = {"source": "video.mp4", "log_level": "DEBUG"}
    cfg.update(overrides)
    path = tmp_path / "vig.json"
    path.write_text(json.dumps(cfg))
    return str(path)


def test_config_file_source_survives_empty_cli(tmp_path):
    from main import build_config, parse_args

    args = parse_args(["--config", _write_config(tmp_path)])
    cfg = build_config(args)
    assert cfg.source == "video.mp4"
    assert cfg.log_level == "DEBUG"


def test_explicit_cli_still_wins(tmp_path):
    from main import build_config, parse_args

    args = parse_args(
        [
            "--config",
            _write_config(tmp_path),
            "--source",
            "other.mp4",
            "--log-level",
            "ERROR",
        ]
    )
    cfg = build_config(args)
    assert cfg.source == "other.mp4"
    assert cfg.log_level == "ERROR"


def test_no_config_falls_back_to_defaults():
    from main import build_config, parse_args

    cfg = build_config(parse_args([]))
    assert cfg.source == VigilanceConfig().source == "0"
    assert cfg.log_level == "INFO"


@pytest.mark.parametrize("path", ["config/default.json"])
def test_default_config_enables_weapons_fights_fire_and_falls(path):
    cfg = VigilanceConfig.from_file(path)
    cfg.validate()
    assert cfg.enable_fight is True
    assert cfg.enable_fire is True
    assert cfg.enable_fall is True
    assert cfg.enable_bag is False
    assert cfg.enable_threats is True


def test_violence_fire_fall_pipeline_registers_no_other_scenarios():
    from core.pipeline import Pipeline
    from tests.conftest import FakeDetector

    pipe = Pipeline(config=VigilanceConfig(), detector=FakeDetector([[]]))
    assert sorted(name for name, _, _ in pipe._scenarios) == ["Fall Detection"]


def test_fire_enabled_by_default_with_env_override(monkeypatch):
    from config import VigilanceConfig

    assert VigilanceConfig().enable_fire is True
    monkeypatch.setenv("VIGILANCE_ENABLE_FIRE", "0")
    assert VigilanceConfig().from_env().enable_fire is False


@pytest.mark.parametrize("entrypoint", ["main", "ui"])
def test_explicit_checkpoint_prevents_loading_configured_model_first(
    tmp_path, entrypoint
):
    import importlib

    cli = importlib.import_module(entrypoint)
    config_path = _write_config(
        tmp_path,
        fight_cfg={"model": {"checkpoint": "old.pth", "autoload": True}},
    )
    cfg = cli.build_config(
        cli.parse_args(["--config", config_path, "--fight-checkpoint", "new.pth"])
    )
    assert cfg.fight_cfg.model.checkpoint == "new.pth"
    assert cfg.fight_cfg.model.autoload is False


def test_no_threats_disables_fire_and_opted_in_auxiliary_model():
    from ui import build_config, parse_args

    cfg = build_config(parse_args(["--threats", "--no-threats"]))
    assert cfg.enable_fire is False
    assert cfg.enable_threats is False


@pytest.mark.parametrize("flags,enabled", [([], True), (["--threats"], True),
    (["--enable-weapon-alerts"], True), (["--no-threats"], False)])
def test_ui_auxiliary_model_uses_effective_weapon_settings(flags, enabled):
    from ui import build_config, build_threat_detector, parse_args

    args = parse_args(flags + ["--no-threat-download"])
    cfg = build_config(args)
    detector = build_threat_detector(cfg, args)
    if not enabled:
        assert detector is None
    else:
        assert detector.enable_weapon_alerts is True
        assert detector.conf == cfg.conf


def test_gpu_requirement_cannot_be_overridden_by_cpu():
    from ui import build_config, parse_args

    with pytest.raises(SystemExit):
        build_config(parse_args(["--require-gpu", "--device", "cpu"]))


def test_gpu_requirement_selects_cuda_for_both_models(monkeypatch):
    import sys
    from types import SimpleNamespace
    from ui import build_config, parse_args

    monkeypatch.setitem(
        sys.modules,
        "torch",
        SimpleNamespace(cuda=SimpleNamespace(is_available=lambda: True)),
    )
    cfg = build_config(parse_args(["--require-gpu", "--device", "auto"]))
    assert cfg.device == cfg.fight_cfg.model.device == "cuda"
