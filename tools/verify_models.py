"""Verify approved artifacts and run real inference on the selected device."""

import argparse
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core.model_security import download_verified, file_sha256
from core.paths import resolve_asset


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--download",
        action="store_true",
        help="fetch missing artifacts from configured HTTPS URLs",
    )
    parser.add_argument("--inference", action="store_true")
    parser.add_argument("--device", choices=("cpu", "cuda"), default="cpu")
    parser.add_argument(
        "--auxiliary",
        action="store_true",
        help="download and check the pinned auxiliary threat model",
    )
    args = parser.parse_args(argv)
    manifest = json.loads((ROOT / "models/manifest.json").read_text())
    paths = {}
    for name, item in manifest.items():
        path = Path(resolve_asset(name))
        if not path.is_file() and args.download:
            url = os.environ.get(item["url_env"], "")
            if not url.startswith("https://"):
                raise RuntimeError(
                    f"missing approved model: configure {item['url_env']}"
                )
            path = Path(
                download_verified(
                    url, ROOT / name, item["sha256"], max_bytes=512 * 1024 * 1024
                )
            )
        if not path.is_file() or file_sha256(path) != item["sha256"]:
            raise RuntimeError(f"missing or mismatched model: {name}")
        paths[name] = str(path)
        print(f"verified {name}")
    if args.inference:
        import numpy as np
        import torch
        from core.stgcn_adapter import STGCNPPAdapter, preprocess_pair
        from core.demo import pair_history_fixture
        from hashlib import sha256
        from core.detector import Detector

        torch.set_num_threads(2)
        if args.device == "cuda" and not torch.cuda.is_available():
            raise RuntimeError(
                "CUDA requested but unavailable; check GPU PyTorch and driver"
            )
        adapter = STGCNPPAdapter(device=args.device)
        adapter.load_model(paths["models/stgcnpp/best_fight_stgcnpp_v1.pth"])
        if not adapter.head_trained:
            raise RuntimeError("approved fight model has no trained head")
        tensor = np.zeros((1, 2, 100, 17, 3), dtype=np.float32)
        first = adapter.predict_batch(tensor)
        second = adapter.predict_batch(tensor)
        np.testing.assert_allclose(first, second, rtol=0, atol=1e-6)
        if not all(np.isfinite(p) and 0 <= p <= 1 for p in first):
            raise RuntimeError("invalid model probabilities")
        detector = Detector(
            pose_model_path=paths["models/yolo/yolo26m-pose.pt"],
            det_model_path=paths["models/yolo/yolo26m.pt"],
            device=args.device,
            imgsz=320,
        )
        frame = np.zeros((240, 320, 3), dtype=np.uint8)
        detector.detect(frame, timestamp=0)
        if args.auxiliary:
            from core.threat_detection import SurveillanceThreatDetector

            auxiliary = SurveillanceThreatDetector(
                download_missing=True, device=args.device, imgsz=320
            )
            output = auxiliary.detect(frame)
            if not output.models.get("surveillance", {}).get("ready"):
                raise RuntimeError(f"auxiliary model check failed: {output.models}")
            print(
                f"verified pinned auxiliary model and restricted {args.device} inference"
            )
        detector.reset()
        detector.detect(frame, timestamp=0)
        golden = ROOT / "models/regression.json"
        expected = json.loads(golden.read_text())
        np.testing.assert_allclose(
            first,
            expected["zero_tensor_probabilities"],
            rtol=0,
            atol=expected["absolute_tolerance"],
        )
        pair_tensor = preprocess_pair(pair_history_fixture(), 4, 100, img_hw=(240, 320))
        if sha256(pair_tensor.tobytes()).hexdigest() != expected["pair_tensor_sha256"]:
            raise RuntimeError("pair preprocessing regression")
        pair_scores = adapter.predict_batch(pair_tensor[np.newaxis])
        np.testing.assert_allclose(
            pair_scores,
            expected["pair_probabilities"],
            rtol=0,
            atol=expected["absolute_tolerance"],
        )
        print(
            json.dumps(
                {
                    "inference": "passed",
                    "device": args.device,
                    "zero_tensor_probabilities": first,
                    "torch": torch.__version__,
                }
            )
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
