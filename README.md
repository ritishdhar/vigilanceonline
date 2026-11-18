# JammuUniversity · RakshaNetra

Local video surveillance prototype: YOLO pose tracking, pairwise ST-GCN++ fight inference, graph aggregation, and configurable fall, bag, and auxiliary threat detection.

## Run

Use Python 3.12 on Windows with an NVIDIA CUDA GPU. Follow [SETUP.md](SETUP.md) to install GPU-enabled PyTorch first. Real inference also requires the approved model artifacts listed in [models/manifest.json](models/manifest.json) and a camera or video source.

```powershell
python ui.py --config config/default.json --source idle --require-gpu
```

Open the loopback URL printed by the UI and connect a camera or upload an approved clip. Use `--source 0` to open a camera at launch. Inference requires CUDA; `device: auto` also resolves to CUDA and never falls back to host inference. A synthetic demo needs only `requirements-unit.lock`:

```powershell
python ui.py --synthetic
```

Model-weight binaries are excluded from Git. Provision the approved weights separately; without them, real detection is unavailable. The synthetic demo does not load real models, even when weights are installed.

Open **Alert recipients** to enroll phones. The recipient app supports private
incident clips, shared reviews, and handoff to enrolled campus security devices.
See [phone setup](SETUP.md#phone-incident-response) for HTTPS and Web Push.

There is one application at the project root. `core/` owns processing, `scenarios/` owns fall/bag rules, `web/` owns the dashboard, `config/` holds defaults, and `tests/` and `tools/` hold validation and utilities. Model integrity metadata and numerical fixtures live in `models/`.

## Architecture and behavior

- `core/detector.py`: restricted model loading, persistent tracker lifecycle, configurable inference.
- `core/track_registry.py`: identity retention separated from fresh observations; expired state is evicted.
- `core/pipeline.py`: pipeline-owned scenario instances, generation-aware histories, common alerts, model health and complete evidence fields.
- `core/pairs.py`, `core/stgcn_adapter.py`, `core/graph.py`: bounded temporal histories, vectorized resampling/normalization, configurable inference cadence, scene candidates.
- `core/replay.py`: complete canonical evidence and tolerant field-level comparisons; streaming evidence output.
- `core/http_server.py`, `core/video_upload.py`: authenticated local control, bounded connections, streaming uploads and owned-file cleanup.
- `core/phone_push.py`: Web Push delivery through a persistent outbox with retry backoff.
- `web/`: separate dashboard HTML, CSS and JavaScript. Synthetic helpers live in `core/demo.py`.

Fight health is `UNAVAILABLE`, `WARMING_UP`, `ASSESSED`, `ERROR`, or `DISABLED`. Missing models and insufficient observations never imply `NO_FIGHT`. Group labels describe undirected interaction-graph candidates, not established attacker/victim roles.

Config precedence is file, then `VIGILANCE_*` environment, then explicit CLI flags. `fight_cfg.inference_interval_sec` defaults to 0.2 seconds; tracking and acquisition continue between model evaluations. Live inputs use a monotonic acquisition clock; files use media timestamps. `bag_separation_px` is the legacy name for the rapid-separation metadata threshold, now measured in pixels/second; abandonment timing is independent.

## Security and artifacts

The dashboard binds `127.0.0.1`. It checks Host/Origin and uses a per-launch session cookie for private reads and a header token for mutations. Source switching permits camera indices, session-owned uploads and sources explicitly configured with `--source` or `--allow-source`.

Uploads are limited to 256 MiB each and 512 MiB per session. Only application-owned upload files are removed. Connection/read deadlines and bounded concurrency prevent unlimited resource allocation. Camera URLs are redacted before logging or display.

Model deserialization never retries unrestricted pickle. YOLO requires the locked runtime's restricted loader, and runtime package auto-installation is disabled. The auxiliary artifact uses a fixed revision and SHA-256 with atomic verified downloads. `models/manifest.json` identifies the four release artifacts; `models/regression.json` records a deterministic numerical fixture. Artifact integrity and numerical regression are not accuracy certification.

Inference runs locally. Weapon alerts (including knives) and fire detection are enabled by default and use the pinned auxiliary model, downloaded from Hugging Face when needed. Weapons must meet the displayed detection threshold for three consecutive frames before an alert is raised. Fire/smoke retain their stricter confidence threshold. New alerts stay in Active for at least 10 seconds and then move to Recent once detection has stopped. Use `--no-threat-download` for cached weights only, or `--no-threats` to disable all auxiliary detection.

## Validation and evaluation

```powershell
python tools/run_checks.py
python -m ruff check core scenarios config.py main.py ui.py replay.py eval_fight.py model_smoke_test.py tests tools
python tools/verify_models.py --inference --auxiliary --device cuda
python eval_fight.py --manifest evidence/val_manifest.csv
```

Full model tests require CUDA-enabled PyTorch, a visible GPU, and approved checkpoints. Lightweight tests check backend behavior without model libraries and explicitly skip real-model checks. CI runs those checks on pushes/PRs; version tags and manual runs additionally use a self-hosted GPU runner. Missing CUDA or release artifacts fail the model gate. Configure the runner, `GPU_PYTHON`, and four model URL secrets documented in [SETUP.md](SETUP.md).

Evaluation rejects unreadable clips by default. `--allow-partial` writes coverage/failure information and still exits nonzero. Threshold selection must use validation data separate from the final accuracy test set. Real accuracy remains dependent on labeled deployment-representative clips.

See [docs/CODEBASE_AUDIT.md](docs/CODEBASE_AUDIT.md) for this checkout's audit, cleanup, and verification results.

Diagnostics retain at most 4,096 distinct IDs: `unique_ids_capped=true` explicitly signals that `unique_ids` is a capped sample rather than an exhaustive total. Model and lightweight CI lanes enforce 70% and 60% combined coverage floors.
