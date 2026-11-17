# JammuUniversity codebase audit

Audited on 2026-11-17, starting from commit `7d8f102`.

Scope: this checkout's runtime code, configuration, model loading, backend HTTP/upload handling, notifications, dependencies, tests, launchers, and repository contents. Re-Vigilance was used only as a read-only source of missing model files. UI design and browser acceptance were excluded. A small dashboard callback-order repair made before that clarification remains; no further UI work was performed.

## Corrections

- **CUDA inference only.** Detector defaults, ST-GCN++, auxiliary detection, model verification, and smoke checks now select CUDA. `auto` also requires CUDA; unsupported devices are rejected, and indexed CUDA devices are validated. Both real-model launchers require a GPU. Real-model tests skip without CUDA; the release verification command fails when CUDA is unavailable.
- **Startup configuration.** Explicit fight checkpoints take precedence before pipeline construction, avoiding an attempted load of the old configured checkpoint. Disabling fight detection prevents explicit checkpoint loading. `--require-gpu` cannot be overridden by another device flag.
- **Synthetic execution.** Dashboard-backend and replay synthetic modes bypass real model autoload even with the default config and installed checkpoints. `--no-threats` now disables the auxiliary fire detector as well as optional weapon detection.
- **Telegram enrollment retry.** The polling offset advances only after an update is handled successfully. A temporary database failure no longer acknowledges and loses the enrollment request. Tests use a temporary SQLite database and mocked Telegram calls; no messages were sent.
- **IPv6 loopback.** The HTTP server selects the IPv6 address family for an IPv6 bind address and accepts the bracketed loopback Host/Origin. Startup prints the selected host. Previously `--host ::1` was accepted by configuration but failed at the server boundary.
- **Model verification.** Manifest verification checks this checkout's paths even when invoked from another directory. `--auxiliary` can verify the pinned auxiliary download without loading models. Missing or mismatched artifacts fail verification.
- **CUDA timing.** Pair-inference timing includes completion of the GPU outputs instead of stopping immediately after asynchronous work was submitted.

The CPU dependency input/lock, lock compiler, preprocessing benchmark, inference fallback, and dedicated CPU model-test path were removed. Safe checkpoint staging and tensor-to-NumPy transfers still use host memory where required by GPU processing; they are not alternate inference modes.

## Removed files

25 tracked files were removed, approximately 14.5 MB of obsolete content:

| Files | Reason |
| --- | --- |
| `AUDIT_REMEDIATION.md`, `validation-results.json` | Historical audit/results replaced by this checkout's current evidence |
| `docs/UI_IMPLEMENTATION.md`, `docs/UI_SANDBOX_AUDIT.md` | Stale implementation and UI audit notes |
| `docs/evaluation/README.md`, `val_manifest.csv`, `eval_fight.csv`, `eval_fight_w1.csv`, `eval_fight_w1.5.csv`, `eval_fight_w2.csv` | Old experiments tied to unavailable external clips; not a usable evaluation dataset |
| All five files under `docs/presentations/` | Historical hackathon decks, drafts, and their index; no runtime or validation dependency |
| `models/stgcnpp/training_history.csv`, `training_summary.json` | Historical training outputs unused by inference or verification |
| `web/logo.png`, `web/logo-dark.png` | Unreferenced standalone copies; the used logo is already embedded in the page |
| `tools/browser_smoke.py`, `tools/browser_smoke.cjs` | Obsolete browser scripts targeting removed controls/functions |
| `requirements-cpu.in`, `requirements-cpu.lock`, `tools/compile_lock.py`, `tools/benchmark_preprocess.py` | CPU-specific setup and benchmark work removed as requested |

Runtime modules, optional scenario implementations, current regression/security tests, the supplied demo clip, and model manifests/fixtures were retained. The audit-created incomplete environment, dependency download caches, and temporary validation files were cleaned up. No source outside JammuUniversity was edited.

## Models

Copied the following missing artifacts from Re-Vigilance into this checkout and verified every destination against the existing SHA-256 values in `models/manifest.json`:

- `models/stgcnpp/best_fight_stgcnpp_v1.pth`
- `models/stgcnpp/j.pth`
- `models/yolo/yolo26m-pose.pt`
- `models/yolo/yolo26m.pt`

These binaries remain ignored by Git. They must accompany a copied deployment or be provisioned separately after a clone. The backbone `j.pth` remains useful for compatibility checks and is not used as a trained fight classifier.

The auxiliary fire/smoke model was absent from both projects. Its first pinned download failed integrity verification. A second download timed out after 180 seconds with 2,700,236 of 19,163,418 bytes received. Partial files were removed; no verified auxiliary model is cached. Its revision and required digest were not changed. Provision it on a working connection with `python tools/verify_models.py --auxiliary` before relying on fire detection.

## Verification

- **219 passed, 16 skipped** in the Python backend suite on Python 3.12.14. The skipped cases require real CUDA models. The isolated check environment contained no PyTorch.
- **73.61% combined statement/branch coverage**, above the 60% lightweight gate. This includes runtime modules whose model-dependent branches could not execute here.
- Ruff checks passed; all 57 remaining Python files passed formatting checks.
- Both PowerShell launchers parsed successfully. The CI YAML parsed and its model job targets a self-hosted Windows x64 GPU runner.
- All four manifest model hashes passed. No neural inference or CPU model benchmark was run.
- New regressions cover device enforcement, checkpoint precedence, synthetic model isolation, auxiliary hash failures, Telegram enrollment retry, and authenticated IPv6 HTTP requests.

The final backend command was:

```powershell
python tools/run_checks.py tests -q --tb=short --cov=core --cov=scenarios --cov=config --cov=ui --cov=main --cov=eval_fight --cov-branch --cov-fail-under=60
```

## Remaining validation and deployment constraints

GPU inference, CUDA numerical compatibility, throughput, and GPU memory use have not been measured. Run the commands in `SETUP.md` on the destination GPU. The historical numerical fixture retains its original provenance; it was not regenerated or presented as a new GPU result.

The release workflow requires a self-hosted runner labeled `gpu`, repository variable `GPU_PYTHON`, and the four artifact URL secrets. No runner registration or remote CI execution was performed. Runtime dependencies have direct version pins but no transitive lock validated on the target CUDA installation.

Fire detection needs the pinned auxiliary artifact. A missing or rejected model must remain unavailable; changing its expected hash to accept an unreviewed download would defeat the integrity check. `--no-threats` explicitly disables auxiliary detection while provisioning is unresolved.

Accuracy still requires a separate labeled dataset representative of deployment footage. The supplied sample and numerical regression fixture do not establish detection accuracy. The server also remains a single shared inference workspace; deployment access controls and the existing restrictions in `SETUP.md` apply.
