# Re-Vigilance

Local video surveillance prototype: YOLO pose tracking, pairwise ST-GCN++ fight inference, graph aggregation, and configurable fall, bag, and auxiliary threat detection.

## Run

Use Python 3.12 on Windows. For the GPU laptop, follow [SETUP.md](SETUP.md) to install GPU-enabled PyTorch first; runtime requirements do not force CPU wheels. Real inference also requires the approved model artifacts listed in [models/manifest.json](models/manifest.json) and a camera or video source.

```powershell
python ui.py --config config/default.json --source samples/test1.mp4
```

Open the loopback URL printed by the UI. Use `--source 0` for a camera. `device: auto` selects CUDA when available. A synthetic demo needs only `requirements-unit.lock`:

```powershell
python ui.py --synthetic
```

The public Git repository intentionally excludes model-weight binaries and video clips. Provision approved weights and any shareable test video separately; without the model artifacts, fight detection remains explicitly unavailable. The synthetic demo does not run real model inference.

There is one application at the project root. `core/` owns processing, `scenarios/` owns fall/bag rules, `web/` owns the dashboard, `config/` holds defaults, and `tests/` and `tools/` hold validation and utilities. Model metadata lives in `models/`; provisioned weights and sample videos are not committed. The unused BiLSTM path, compatibility launchers and duplicate nested project have been removed.

## Architecture and behavior

- `core/detector.py`: restricted model loading, persistent tracker lifecycle, configurable inference.
- `core/track_registry.py`: identity retention separated from fresh observations; expired state is evicted.
- `core/pipeline.py`: pipeline-owned scenario instances, generation-aware histories, common alerts, model health and complete evidence fields.
- `core/pairs.py`, `core/stgcn_adapter.py`, `core/graph.py`: bounded temporal histories, vectorized resampling/normalization, configurable inference cadence, scene candidates.
- `core/replay.py`: complete canonical evidence and tolerant field-level comparisons; streaming evidence output.
- `core/http_server.py`, `core/video_upload.py`: authenticated local control, bounded connections, streaming uploads and owned-file cleanup.
- `core/alert_dispatch.py`: opt-in Telegram delivery through a bounded background queue with stable incident IDs and retry backoff.
- `web/`: separate dashboard HTML, CSS and JavaScript. Synthetic helpers live in `core/demo.py`.

Fight health is `UNAVAILABLE`, `WARMING_UP`, `ASSESSED`, `ERROR`, or `DISABLED`. Missing models and insufficient observations never imply `NO_FIGHT`. Group labels describe undirected interaction-graph candidates, not established attacker/victim roles.

Config precedence is file, then `VIGILANCE_*` environment, then explicit CLI flags. `fight_cfg.inference_interval_sec` defaults to 0.2 seconds; tracking and acquisition continue between model evaluations. Live inputs use a monotonic acquisition clock; files use media timestamps. `bag_separation_px` is the legacy name for the rapid-separation metadata threshold, now measured in pixels/second; abandonment timing is independent.

## Security and artifacts

The dashboard binds `127.0.0.1`. It checks Host/Origin and uses a per-launch session cookie for private reads and a header token for mutations. Source switching permits camera indices, session-owned uploads and sources explicitly configured with `--source` or `--allow-source`.

Uploads are limited to 256 MiB each and 512 MiB per session. Only application-owned upload files are removed. Connection/read deadlines and bounded concurrency prevent unlimited resource allocation. Camera URLs are redacted before logging or display.

Model deserialization never retries unrestricted pickle. YOLO requires the locked runtime's restricted loader, and runtime package auto-installation is disabled. The auxiliary artifact uses a fixed revision and SHA-256 with atomic verified downloads. `models/manifest.json` identifies the four release artifacts; `models/regression.json` records a deterministic numerical fixture. Artifact integrity and numerical regression are not accuracy certification.

Inference runs locally. Optional auxiliary downloads contact Hugging Face. Enabling Telegram sends incident text and frame images to Telegram; it is disabled by default.

## Validation and evaluation

```powershell
python tools/run_checks.py
python -m ruff check core scenarios config.py main.py ui.py replay.py eval_fight.py model_smoke_test.py tests tools
python tools/verify_models.py --inference
python eval_fight.py --manifest evidence/val_manifest.csv
```

Full model tests require Torch and approved checkpoints. Lightweight tests explicitly skip these checks. CI runs the lightweight lane on pushes/PRs; version tags and manual runs additionally require the model integrity/inference gate. Missing release artifacts fail that gate. Configure the four model URL secrets documented in [SETUP.md](SETUP.md).

Evaluation rejects unreadable clips by default. `--allow-partial` writes coverage/failure information and still exits nonzero. Threshold selection must use validation data separate from the final accuracy test set. Real accuracy remains dependent on labeled deployment-representative clips.

See [AUDIT_REMEDIATION.md](AUDIT_REMEDIATION.md) for the finding-to-fix cross-check.

Diagnostics retain at most 4,096 distinct IDs: `unique_ids_capped=true` explicitly signals that `unique_ids` is a capped sample rather than an exhaustive total. Model and lightweight CI lanes enforce 70% and 60% combined coverage floors.
