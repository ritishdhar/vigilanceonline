# Setup and release checks

## GPU laptop (Windows, NVIDIA CUDA)

Copy the entire cleaned project, including `models/`. Install Python 3.12 x64 and a supported NVIDIA driver, then open PowerShell in the project root:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
```

The four artifacts in `models/manifest.json` have been provisioned locally for this checkout. They remain ignored by Git, so a fresh clone still needs those files. Run `python tools/verify_models.py` to verify this project's model files before starting inference. Run `python tools/verify_models.py --auxiliary` to cache and verify the auxiliary fire/smoke model without inference.

Use the official [PyTorch installation selector](https://pytorch.org/get-started/locally/) for Windows/Pip/Python and the CUDA platform compatible with your laptop. Use its official CUDA wheel index to install **torch 2.14.1 and torchvision 0.29.1**, matching this project's dependency pins. Substitute the selector's index URL below (the placeholder is not an executable command):

```text
python -m pip install torch==2.14.1 torchvision==0.29.1 --index-url <official CUDA wheel index from selector>
```

If that release pair is unavailable on your chosen platform, resolve a supported pair and revalidate it before changing the pins. The GPU model and driver determine compatibility; this project requires NVIDIA CUDA for model inference.

```powershell
python -m pip install -r requirements-dev.txt
python -m pip check
python -c "import torch; assert torch.cuda.is_available(), 'CUDA unavailable: check driver and GPU PyTorch'; print(torch.__version__, torch.version.cuda, torch.cuda.get_device_name(0)); print(torch.ones(2, device='cuda') @ torch.ones(2, device='cuda'))"
python tools/verify_models.py --inference --auxiliary --device cuda
python tools/run_checks.py
python ui.py --config config/default.json --source idle --device cuda --require-gpu
```

The CUDA model gate checks the trained fight model, both YOLO detectors and the auxiliary model on the GPU and fails if CUDA is unavailable. The default detector device is `cuda`; the fight model inherits it. `cuda:<index>` selects a specific GPU. The legacy `auto` setting also requires CUDA. GPU inference has not been validated during this audit; run the gate on the destination laptop before deployment. Use `--source 0` for its camera.

## Hardware-independent backend checks

For backend logic, configuration, HTTP, upload, and notification checks without loading neural models, use a separate Python 3.12 environment:

```powershell
python -m pip install -r requirements-unit.lock
python tools/run_checks.py
python tools/verify_models.py
```

Real-model tests skip without CUDA; this is not a GPU validation result. `models/manifest.json` records the approved four model hashes, and the verifier checks files inside this checkout. The auxiliary model revision and hash are pinned in code, and its offline cache is under `models/surveillance/`. Missing models remain explicitly unavailable.

## Dashboard source permissions

The initial CLI/config source is allowed. To switch to additional file or stream sources from the dashboard, list each explicitly:

```powershell
python ui.py --config config/default.json --source 0 --allow-source C:\videos\entrance.mp4
```

The browser obtains a per-launch session at `/`. Mutations require its header token; cross-origin requests are rejected. Restarting the server invalidates old browser sessions; reload the page. The service is designed for loopback use, not direct public hosting.

## Phone incident response

Start the operator dashboard with a site and camera label. The GPU host records
new incidents and shared reviews in `evidence/incidents.sqlite3`. The encoder
saves an H.264 MP4 with up to five seconds before and ten seconds after the event
and available before, at-alert, and after stills in `evidence/incident-media/`. Ending or switching a source
finishes the clip with the frames collected so far. Clips and records are
retained for seven days, with a maximum of 500 incidents.

```powershell
python ui.py --config config/default.json --source idle --require-gpu --site-name "East campus" --camera-name "Main entrance"
```

1. Open **Alert recipients** in the dashboard and choose **Generate QR**. Each
   code can enroll one device and expires after ten minutes.
2. Scan the QR with your phone's camera and wait for the linked confirmation.
   Add RakshaNetra to the Home Screen on iPhone or install it from Chrome on
   Android. Open the installed app, choose **Enable notifications**, and allow
   notifications. If the app asks you to connect, generate a fresh QR and
   scan it from inside the app or paste its invite code.
3. Choose **Send test alert** to check delivery. Test alerts are labeled
   and contain no camera footage. To rehearse video review, run an approved
   source that triggers a detection.
4. On the recipient device, open the incident to play the video and review
   **Incident photos**. If campus responders are enrolled, acknowledge the
   incident and confirm **Forward to campus security**. The operator can
   review the resulting activity and provider acceptance in **Incident history**.
   Existing responder devices retain access. The authenticated
   `POST /api/phone-admin/invite` endpoint accepts `{"role":"responder"}` for
   responder enrollment.

For physical phones, use a trusted HTTPS hostname that the phones can reach.
Run the application on loopback behind a TLS reverse proxy. Configure
`--recipient-origin` with the exact HTTPS origin, and
`VIGILANCE_PUSH_SUBJECT` (or `--push-subject`) with your monitored contact URI.
The VAPID key is generated at first startup and persisted in the incident
database. Changing that database requires devices to subscribe again.

```powershell
$env:VIGILANCE_PUSH_SUBJECT = "mailto:security@example.org"
python ui.py --config config/default.json --source idle --require-gpu --recipient-origin https://alerts.example.org
```

Replace both example values with your own. At the proxy, forward only the
configured recipient hostname and set the upstream `Host` to that hostname.
Publish `/recipient/` and `/api/recipient/`, and optionally redirect `/` to
`/recipient/`. Preserve the browser's `Origin` and allow byte-range requests
for MP4 playback. Keep the operator dashboard, camera stream, and enrollment
administration on loopback or behind separate operator authentication. The
recipient hostname rejects those routes.

For a same-Wi-Fi demo, the model process can serve recipient HTTPS directly.
Use a certificate with the PC's private IPv4 address in its IP Subject
Alternative Name and a matching PEM key. The phone must trust the issuing
certificate and be able to reach the PC on the recipient port.

```powershell
python ui.py --config config/default.json --source 0 --require-gpu --port 8000 `
  --recipient-origin https://192.168.1.20:8788 --recipient-port 8788 `
  --recipient-cert path/to/server.pem --recipient-key path/to/server-key.pem
```

Open **Alert recipients** on port 8000 to issue an invite. Both interfaces
use the same incident database. The phone listener serves the recipient app
and its API, with device enrollment required to open incident evidence.

When using a temporary HTTPS tunnel, point it to a separate loopback port for
the recipient app. Add `--recipient-port 8788` to the command above and
configure the tunnel upstream as `http://127.0.0.1:8788`. Forward the exact
HTTPS recipient hostname in the `Host` header. Refresh the operator dashboard
and create a fresh QR when the tunnel URL changes. Local preview invites
provide a desktop link without a phone QR.

Phone notifications request high-urgency delivery, a vibration pattern, and
persistent display where supported. The operating system controls background
sound, vibration, and pop-up behavior. The connected app opens directly to
the incident inbox. The first tap in the app unlocks foreground ringing and
requests browser notification permission if needed. **Silence**, **Dismiss**,
and **Acknowledge** stop an incoming alert.
The compact connection card shows the workspace and phone name.
**Disconnect** revokes that device session and returns to QR pairing.
New incidents are queued and previously dismissed IDs are remembered on the
device. Notification-display receipts remain available to the backend.
The dashboard's **Send test alert** checks push delivery.
Beside the inbox refresh button, the bell icon previews a sample alert on the
current device. The trash icon clears that device's inbox after confirmation.
Clearing persists when reopening the app, keeps dashboard recordings intact,
and allows new alerts to arrive.

Tap an alert to open the incident and play the clip under **Incident video**.
With `--public-demo-origin` and `--public-demo-directory`, add
`--public-recipient-evidence` to play matching recorded clips and photos directly
inside the public phone app using its existing device session. This makes that
evidence available to anyone who joins through the public QR, without an email
login. Media stays on the same HTTPS origin, supports video seeking, and requires
an active enrolled device. The operator dashboard and live stream stay private.
Without this explicit option, public notices link to the email-protected evidence
origin. **Forward to campus security** sends to enrolled
workspace responders. For a deployment in India, add `--emergency-number 112`
to show a user-initiated call link. Government dispatch requires a separate
agency integration.

For a controlled HTTPS deployment behind a trusted TLS reverse proxy, configure a persistent HTTPS hostname and bind the application only on the private side of that proxy:

```powershell
python ui.py --host 0.0.0.0 --port 8000 --public-origin https://demo.example
```

The reverse proxy must forward the original `Host` and `Origin` and provide HTTPS to users. The app restricts requests to the exact configured origin and marks its session cookie `Secure`; direct public exposure of the application port is not supported. Without `--public-origin`, the application stays loopback-only.

This dashboard is still a single shared inference workspace, not a multi-tenant public service: everyone visiting the same deployment sees the same feed and can manage recipients, and source/upload controls share one processing pipeline. Do not publish it to anonymous judges until dashboard access control, upload/source limits, and isolation are configured for that deployment. Recipient invitations grant access to the phone app. Operator access is managed separately.

## Optional model downloads

The auxiliary model is downloaded only at its recorded immutable revision and digest, unless `--no-threat-download` is used. A verified cache under `models/surveillance/` remains usable offline. It is loaded with the restricted YOLO loader. Download and model initialization can take time at startup.

Weapon alerts (including knives) and fire detection use this auxiliary model by default. A weapon must meet the visible detection confidence threshold (`conf`, 0.25 in the default config) for three consecutive frames to generate an incident and phone alert. Fire/smoke retain their stricter confidence threshold. Alerts stay in Active for at least 10 seconds, then move to Recent once detection stops. `--no-threats` disables the entire auxiliary detector, including fire/smoke; `--no-fire` disables only fire/smoke alerts. Synthetic mode skips all real model loading and works with the lightweight dependencies even when checkpoint files are present.

## Dependency maintenance

`requirements.txt` pins direct runtime dependencies, and `requirements-dev.txt` adds development checks. Install matching CUDA-enabled PyTorch first; these files do not select a CUDA wheel index. `requirements-unit.lock` pins the lightweight backend check environment. The GPU runtime does not yet have a transitive dependency lock validated on the target hardware. After dependency changes, run `pip check`, static/backend checks, and the CUDA artifact/inference gate on that hardware before release.

## CI release configuration

`.github/workflows/ci.yml` runs unit/static checks on pushes and pull requests. Version tags (`v*`) and manual dispatch also run a model job on a self-hosted Windows x64 runner labeled `gpu`. Provision its NVIDIA driver and Python 3.12 virtual environment with the pinned CUDA PyTorch pair outside the checkout. Set repository variable `GPU_PYTHON` to that environment's absolute `python.exe` path. The workflow requires working CUDA before installing the remaining development dependencies and running model checks.

Configure repository secrets `FIGHT_MODEL_URL`, `BACKBONE_MODEL_URL`, `POSE_MODEL_URL`, and `OBJECT_MODEL_URL` with HTTPS download URLs for the exact artifacts in the manifest. The loader verifies their recorded SHA-256 and fails on missing or mismatched artifacts. Avoid replacing the manifest with hashes obtained from an unreviewed download during release.

The workflow is prepared locally; no runner was registered and no remote run was performed during this audit. Without the configured GPU runner the model job will remain queued. Semantic accuracy requires a separate labeled evaluation set; the numerical model fixture only detects loading/preprocessing/runtime regressions. Its recorded historical values remain unchanged and still need verification against the target CUDA runtime.

The release model gate also verifies the pinned public auxiliary artifact using `--auxiliary`. Its source revision and digest are fixed in code; it needs no additional secret.
