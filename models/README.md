# Model artifacts

The canonical release manifest is [manifest.json](manifest.json). Default weights live locally in `stgcnpp/`, `yolo/` and `surveillance/` and are excluded from Git. The verifier always checks this checkout; explicit runtime weight overrides remain configurable.

`best_fight_stgcnpp_v1.pth` contains the binary fight head. `j.pth` is an upstream representation checkpoint and must not be treated as a trained fight classifier. The two YOLO files provide pose and object detections. Run `python tools/verify_models.py` for a hash-only check. On the GPU machine, run `python tools/verify_models.py --inference --auxiliary --device cuda` to check restricted loading, detector reset, and numerical fixtures as well. Real inference requires CUDA and never falls back to host inference.

`regression.json` anchors deterministic zero-tensor and moving-pair preprocessing/inference checks. These are runtime regressions, not semantic accuracy claims.

The auxiliary surveillance artifact's immutable Hugging Face revision and expected SHA-256 are recorded in `core/threat_detection.py`. It is atomically downloaded and verified before restricted loading. `python tools/verify_models.py --auxiliary` can provision and hash-check it without loading neural models. Fire detection is enabled by default and needs this artifact; `--no-threats` disables the auxiliary detector.
