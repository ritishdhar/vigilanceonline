# Audit remediation and cross-check

Date: 22 October 2026. The workspace root is the single implementation. The nested duplicate project and compatibility launchers were deleted after moving models to `models/`, the sample video to `samples/`, and final presentations and historical evaluation records to `docs/`. Unused utilities, global scenario wrappers, legacy dictionary converters and the checkpoint-less BiLSTM path were removed. Generated presentation intermediates, incomplete downloads and temporary test dependencies were removed.

## Validation

- Final locked CPU environment: **174 passed, 1 skipped**. The skip is CUDA, which is unavailable on this host.
- Measured application coverage: **80.79% of lines, 67.13% of branches** (77.70% combined statement/branch report). Pipeline combined coverage is 87%; pairs 94%; registry 93%.
- Real CPU checks passed for the approved ST-GCN++ fight checkpoint, both YOLO detector weights, tracker reset, repeated inference and numerical/preprocessing fixtures. Four release artifact SHA-256 checks passed. The upstream representation checkpoint is also exercised by the full model tests.
- Headless Edge rendered the actual dashboard with no JavaScript errors; authenticated mutation returned HTTP 200. Incident activity was verified against timeline input, and unavailable monitoring is prominently displayed.
- Python static checks and formatting, dashboard JavaScript syntax checks and duplicate-file inventory passed.
- Preprocessing numerical equivalence passed. Median local 16-pair preprocessing fell from 166.399 ms to 21.872 ms (about 7.6 times faster); neural inference is excluded from this microbenchmark.
- JUnit, coverage and browser artifacts are generated under `.runtime-tests/` when checks run. Temporary outputs were cleaned after validation; `validation-results.json` retains the measured summary.

The pinned auxiliary artifact also passed its published SHA-256 and restricted CPU inference checks; offline cache reuse and its explicit immutable download URL are regression-tested. The lightweight CI simulation passed **157 tests with 18 expected ML skips**, with 70.93% combined coverage. No cameras were opened and no Telegram notifications were sent during verification.

## Finding-to-fix map

| Original finding | Resolution | Cross-check |
|---|---|---|
| 1. Fight window never ready | Deque retains the sample preceding the cutoff; integer, fractional and jittered timestamps are supported. | `test_fractional_and_jittered_clocks_reach_inference`, existing pair-window tests. |
| 2. Stale poses create observations | Missing sightings immediately become absent for classifiers while identity grace is retained. | `test_missing_observations_do_not_manufacture_fall`. |
| 3. Fight verdict lost | Positive graph verdicts emit common alerts; evidence includes scene, scores, identities, model status and errors. | Fractional-clock end-to-end regression and replay tests. |
| 4. Unavailable means clear | Explicit unavailable/warm-up/error/disabled health; UI banner, stalled-frame watchdog and CLI overlays. | `test_unavailable_warmup_error_are_not_clear`, stalled-capture HTTP test and browser check. |
| 5. Evaluator NameError | Counts scored clips with a bound comprehension. | Evaluator scored/unscored/partial-report regressions. |
| 6. Unsafe deserialization retry | Restricted loading rejection is terminal; no unrestricted fallback. | `test_checkpoint_rejection_never_retries_unrestricted`; real checkpoint loads passed. |
| 7. Mutable/unverified model downloads | Auxiliary revision and SHA-256 pinned; streamed temporary download is verified before atomic replacement. YOLO restricted loading is required and runtime package auto-installation disabled. | Digest/partial-cache regression; real restricted YOLO loads; optional auxiliary live probe. |
| 8. Notification blocks capture | Bounded background delivery worker; independent retry/backoff and delivery health. | Blocking fake transport regression verifies nonblocking submission; failure attempts are throttled. |
| 9. Shared/generation-blind state | Pipeline-owned scenario instances; generation-aware, pruned fall histories. | State-isolation and returning-identity regressions. |
| 10. Dead configuration | Active scenario thresholds and environment settings are wired; unused BiLSTM configuration removed; notification preferences respected. | Configuration behavior and CLI precedence tests. |
| 11. Wrong live clock | Monotonic live capture time and media-time file clock; finite FPS fallback. | Live/file clock regression and video replay test. |
| 12. Tracker reset missing | Explicit detector reset is canonical and failures propagate; source boundaries reset the pipeline. | Stateful detector unit seams, resolution-change tests and real YOLO reset/inference. |
| 13. Historical state accumulates | Expired registry records and closed pairs evicted; generation allocator, capped diagnostics and timeline. | Eviction regression, density/lifecycle tests and timeline cap test. |
| 14. Preprocessing bottleneck | Vectorized nearest-neighbor lookup and frame normalization; inference cadence separated from capture. | Scalar/vector equivalence and reproducible benchmark tool. |
| 15. Local API lacks protection | Per-launch session token; strict Host/Origin checks; private reads authenticated; writes require header token; source allowlist. | Real loopback foreign-origin, rebinding, cookie-only mutation and source authorization tests; browser integration. |
| 16. Unbounded requests/uploads | Bounded connections/streams, validated endpoint lengths, total body deadlines, disk quota and owned upload cleanup. | Invalid-length, stream-cap and owned-file cleanup tests; streaming parser checks. |
| 17. Multipart corruption | Standards-compliant streaming multipart parser with header/part limits and incomplete-upload cleanup. | Embedded-boundary binary preservation and truncation tests. |
| 18. URL credentials leak | Central source redaction removes userinfo/query secrets from application status and capture logging. | Credential-redaction regression. |
| 19. Frame-level deduplication | Source/event IDs remain stable during an incident; bounded queued/delivered identity retention. | Continuous-event identity and notifier deduplication tests. |
| 20. Replay comparison false equality | Complete canonical record comparison, explicit numeric tolerance and field-level divergence paths. | Changed-keypoint and changed-alert-recipient regression. |
| 21. Gradual bag departure missed | Association/separation timer independent of a single-frame jump; speed metadata uses elapsed time. | Gradual separation regression. |
| 22. Bad clips silently excluded | Incomplete opens/decodes fail by default; explicit partial mode reports failures and exits nonzero. | Evaluator incomplete-manifest and partial-coverage tests. |
| 23. Divergent source copies | Newer behavior reconciled into the root; nested launchers and the duplicate project directory removed. Frontend and demo helpers separated from orchestration/tests. | Canonical test configuration, duplicate-source inventory and browser render. |
| 24. No enforced release path | Full CPU and lightweight locks, static/format/coverage CI, mandatory model artifact/inference gate on release tags/manual runs. | Exact locked environment tested; model integrity/numerical gate passed. |
| 25. Smoke-test fake fallback | Missing checkpoint exits 2; no claim of random-model continuation. | Missing-checkpoint smoke regression. |

## Reproduce

```powershell
python tools/run_checks.py tests -q --cov=core --cov=scenarios --cov=config --cov=ui --cov=main --cov=eval_fight --cov-branch --cov-report=term
python tools/verify_models.py --inference
python tools/benchmark_preprocess.py
python -m ruff check core scenarios config.py main.py ui.py replay.py eval_fight.py model_smoke_test.py tests tools
python -m ruff format --check core scenarios config.py main.py ui.py replay.py eval_fight.py model_smoke_test.py tests tools
node --check web/dashboard.js
```

Install the committed lockfile in a normal Python 3.12 environment first. The check tools also recognize workspace-local dependency directories used by the Codex embedded Python runtime.

## Remaining external validation

CUDA was not available. No physical-camera run or representative labeled deployment evaluation was performed, so real-world accuracy and hardware-specific behavior have not been certified. GitHub Actions configuration is prepared locally; remote release checks require the repository and four approved model URL secrets documented in SETUP.md. Coverage is measured, not claimed to be exhaustive; GUI entrypoint branches remain less covered.

Machine-readable final results: [validation-results.json](validation-results.json). The model and lightweight CI coverage floors are 70% and 60%, respectively, and both local checks passed.

## Clean project and GPU transfer

Copy the entire project including `models/` and `samples/`. `requirements.txt` uses hardware-neutral PyTorch version pins; install the matching CUDA wheels first as described in `SETUP.md`. `requirements-cpu.lock` is reserved for the validated CPU/CI environment. Run `python tools/verify_models.py --inference --auxiliary --device cuda` on the GPU laptop; it fails if CUDA is unavailable. There are no duplicate nonempty project files by SHA-256. The project no longer contains temporary installed dependencies or the failed virtual environment.
