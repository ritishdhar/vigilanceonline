"""Hardware-independent checks for the CUDA-only inference contract."""

import sys
from types import SimpleNamespace

import pytest

from config import VigilanceConfig
from core.gpu import require_cuda
from core.stgcn_adapter import STGCNPPAdapter


@pytest.mark.parametrize("device", ["cuda", "auto", "cuda:1"])
def test_missing_cuda_never_falls_back(device, monkeypatch):
    monkeypatch.setitem(
        sys.modules,
        "torch",
        SimpleNamespace(cuda=SimpleNamespace(is_available=lambda: False)),
    )
    with pytest.raises(RuntimeError, match="CUDA unavailable"):
        STGCNPPAdapter(device=device).resolve_device()


def test_indexed_gpu_is_preserved_and_checked(monkeypatch):
    monkeypatch.setitem(
        sys.modules,
        "torch",
        SimpleNamespace(
            cuda=SimpleNamespace(is_available=lambda: True, device_count=lambda: 2)
        ),
    )
    assert require_cuda("cuda:1") == "cuda:1"
    with pytest.raises(RuntimeError, match="CUDA device unavailable"):
        require_cuda("cuda:2")


@pytest.mark.parametrize("device", ["cpu", "mps", "cuda:-1", "cuda:", "cuda:garbage"])
@pytest.mark.parametrize("field", ["detector", "fight"])
def test_invalid_inference_devices_are_rejected(device, field):
    cfg = VigilanceConfig()
    if field == "fight":
        cfg.fight_cfg.model.device = device
    else:
        cfg.device = device
    with pytest.raises(ValueError, match="CUDA required"):
        cfg.validate()


def test_synthetic_replay_skips_installed_models(monkeypatch):
    import replay

    def reject_model_load(*args, **kwargs):
        pytest.fail("synthetic replay tried to load a real checkpoint")

    monkeypatch.setattr(replay.Pipeline, "load_fight_model", reject_model_load)
    assert (
        replay.main(
            ["--synthetic", "--config", "config/default.json", "--max-frames", "1"]
        )
        == 0
    )
