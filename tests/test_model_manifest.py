"""The model gate must validate this checkout even when launched elsewhere."""

import hashlib
import json

import pytest

from tools import verify_models


@pytest.mark.parametrize("canonical_exists", [False, True])
def test_model_verification_uses_only_its_own_checkout(
    tmp_path, monkeypatch, canonical_exists
):
    project = tmp_path / "project"
    other = tmp_path / "other"
    (project / "models").mkdir(parents=True)
    (other / "models").mkdir(parents=True)
    data = b"approved artifact"
    (project / "models/manifest.json").write_text(
        json.dumps({"models/test.pth": {"sha256": hashlib.sha256(data).hexdigest()}})
    )
    (other / "models/test.pth").write_bytes(b"wrong" if canonical_exists else data)
    if canonical_exists:
        (project / "models/test.pth").write_bytes(data)
    monkeypatch.setattr(verify_models, "ROOT", project)
    monkeypatch.chdir(other)
    if canonical_exists:
        assert verify_models.main([]) == 0
    else:
        with pytest.raises(RuntimeError, match="missing or mismatched model"):
            verify_models.main([])


@pytest.mark.parametrize("contents", [b"approved", b"wrong", None])
def test_auxiliary_artifact_check_needs_no_inference(tmp_path, monkeypatch, contents):
    from core.threat_detection import SurveillanceThreatDetector

    (tmp_path / "models").mkdir()
    (tmp_path / "models/manifest.json").write_text("{}")
    monkeypatch.setattr(verify_models, "ROOT", tmp_path)
    path = tmp_path / "auxiliary.pt"
    if contents is not None:
        path.write_bytes(contents)
    monkeypatch.setattr(
        SurveillanceThreatDetector,
        "_resolve_weight",
        lambda self: str(path) if contents is not None else None,
    )
    monkeypatch.setattr(
        SurveillanceThreatDetector,
        "expected_sha256",
        hashlib.sha256(b"approved").hexdigest(),
    )

    def reject_inference(*args, **kwargs):
        pytest.fail("hash-only verification must not load or run models")

    monkeypatch.setattr(SurveillanceThreatDetector, "detect", reject_inference)
    if contents == b"approved":
        assert verify_models.main(["--auxiliary"]) == 0
    else:
        with pytest.raises(RuntimeError, match="auxiliary model"):
            verify_models.main(["--auxiliary"])
