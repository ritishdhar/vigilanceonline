# Model artifacts

The canonical release manifest is [manifest.json](manifest.json). Weights live locally in `stgcnpp/`, `yolo/` and `surveillance/` and are excluded from Git; all runtime paths use this canonical directory.

`best_fight_stgcnpp_v1.pth` contains the binary fight head. `j.pth` is an upstream representation checkpoint and must not be treated as a trained fight classifier. The two YOLO files provide pose and object detections. Run `python tools/verify_models.py --inference` from the workspace root to verify all four hashes, restricted loading, detector reset and numerical fixtures.

`regression.json` anchors deterministic zero-tensor and moving-pair preprocessing/inference checks. These are runtime regressions, not semantic accuracy claims.

The optional surveillance artifact's immutable Hugging Face revision and expected SHA-256 are recorded in `core/threat_detection.py`. It is atomically downloaded and verified before restricted loading. `--auxiliary` on the model verification tool checks that path explicitly.
