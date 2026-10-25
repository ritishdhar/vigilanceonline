# Setup and release checks

## GPU laptop (Windows, NVIDIA CUDA)

Copy the entire cleaned project, including `models/` and `samples/`. Install Python 3.12 x64 and a supported NVIDIA driver, then open PowerShell in the project root:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
```

Use the official [PyTorch installation selector](https://pytorch.org/get-started/locally/) for Windows/Pip/Python and the CUDA platform compatible with your laptop. Use its official CUDA wheel index to install **torch 2.14.1 and torchvision 0.29.1**, matching this project's tested release pair. For example, substitute the selector's index URL below (the placeholder is not an executable command):

```text
python -m pip install torch==2.14.1 torchvision==0.29.1 --index-url <official CUDA wheel index from selector>
```

If that release pair is unavailable on your chosen platform, resolve a supported pair and revalidate it before changing the pins. GPU vendor, model and driver determine compatibility; CUDA instructions apply to NVIDIA hardware. Do not use the CPU lock on the GPU laptop.

```powershell
python -m pip install -r requirements-dev.txt
python -m pip check
python -c "import torch; assert torch.cuda.is_available(), 'CUDA unavailable: check driver and GPU PyTorch'; print(torch.__version__, torch.version.cuda, torch.cuda.get_device_name(0)); print(torch.ones(2, device='cuda') @ torch.ones(2, device='cuda'))"
python tools/verify_models.py --inference --auxiliary --device cuda
python tools/run_checks.py
python ui.py --config config/default.json --source samples/test1.mp4 --device cuda
```

The CUDA model gate verifies the trained fight model, both YOLO detectors and the auxiliary model on the GPU and fails if CUDA is unavailable. `device: auto` in the default config also selects CUDA automatically. This machine's validation used CPU; run the GPU gate on the destination laptop before deployment. Use `--source 0` for its camera.

## Reproducible CPU environment

For CPU use or CI, create/activate the same Python 3.12 virtual environment, then:

```powershell
python -m pip install -r requirements-cpu.lock
python tools/verify_models.py --inference --auxiliary
python tools/run_checks.py
```

`models/manifest.json` records the approved four model hashes. The auxiliary model revision and hash are pinned in code, and its offline cache is under `models/surveillance/`. No model is loaded from a duplicate project directory. Missing models remain explicitly unavailable.

## Dashboard source permissions

The initial CLI/config source is allowed. To switch to additional file or stream sources from the dashboard, list each explicitly:

```powershell
python ui.py --config config/default.json --source 0 --allow-source C:\videos\entrance.mp4
```

The browser obtains a per-launch session at `/`. Mutations require its header token; cross-origin requests are rejected. Restarting the server invalidates old browser sessions; reload the page. The service is designed for loopback use, not direct public hosting.

## Telegram recipient QR setup

Create a bot with Telegram's official BotFather and keep its token on the server. The app uses Telegram long polling for opt-in enrollment, so the same bot must not be running a webhook or another `getUpdates` poller.

For this Windows project, the local setup launcher handles credentials and startup:

1. Open [@BotFather](https://t.me/BotFather) in Telegram, send `/newbot`, and choose a name and a unique username ending in `bot`. Keep the returned token private.
2. Stop any already-running dashboard. From the project root, run `./tools/start_telegram.ps1`. Enter the bot token in its hidden prompt.
3. The launcher checks the bot and webhook state, generates an owner sign-in token, and saves both credentials encrypted for your Windows account in ignored `evidence/telegram_credentials.clixml`. It displays the owner token in your local terminal once; keep it private.
4. Open `http://127.0.0.1:8000/#recipients`, sign in with the owner token, and click **Create opt-in QR**. Scan it on the recipient's phone and press **Start** in Telegram. The recipient list updates automatically. **Send test alert** becomes available after someone enrolls.

Subsequent runs reuse the encrypted credentials. Normal `python ui.py` launches also load them automatically on Windows when no explicit bot or owner credentials are configured. Use `./tools/start_telegram.ps1 -ShowOwnerToken` in your own terminal to retrieve your owner token, `-Reconfigure` to replace credentials, `-ConfigureOnly` to save/check them without launching inference, or `-Source samples/test1.mp4 -Port 8001` to choose a source/port. The launcher uses this project's Python virtual environment and restores its process environment on exit. The encrypted credential file is tied to your Windows account and should not be copied to a hosting service.

**Local live enrollment does not require a public website, inbound webhook or HTTPS proxy.** The phone opens a Telegram bot link, and the running app receives opt-ins using outbound HTTPS polling. Keep the app running with internet access. Only remote dashboard access needs the controlled HTTPS deployment described below. A browser refresh alone cannot load new server credentials: restart the application through the launcher.

For manual environment-based startup instead of the Windows launcher:

Set these server environment variables:

```powershell
$env:VIGILANCE_TELEGRAM_BOT_TOKEN = "<server-side bot token>"
$env:VIGILANCE_TELEGRAM_ADMIN_TOKEN = "<random secret with at least 32 characters>"
```

Start the app normally. Recipient setup is enabled automatically when both values are present. Open **Alert recipients**, sign in with the admin token, and create an opt-in QR. The QR opens the bot with a one-hour invite; the recipient must press **Start** to enroll. The owner can revoke an invite, remove enrolled destinations, and send a clearly labeled test message. Recipients can send `/stop` to unsubscribe. Up to 100 recipients may be active. Enrolled destinations receive text alerts only; camera frames are not sent. Phone notification sounds are controlled by Telegram and the phone's notification/Do Not Disturb settings, not by the dashboard.

Recipient records are stored in `evidence/telegram_recipients.sqlite3`; set `VIGILANCE_TELEGRAM_RECIPIENTS_DB` to a persistent path for hosted deployments. Back up this database securely: it contains Telegram destination identifiers. Never put the bot token in the browser, source control, or public logs. Enter the owner token only in the first-party owner sign-in form; it is not stored in browser local storage. Anyone holding an active QR can opt in, so share it only with the intended responders.

For a controlled HTTPS deployment behind a trusted TLS reverse proxy, configure a persistent HTTPS hostname and bind the application only on the private side of that proxy:

```powershell
python ui.py --host 0.0.0.0 --port 8000 --public-origin https://demo.example
```

The reverse proxy must forward the original `Host` and `Origin` and provide HTTPS to users. The app restricts requests to the exact configured origin and marks its owner cookie `Secure`; direct public exposure of the application port is not supported. Keep the recipient admin token secret and provision it only through the host's secret manager. Without `--public-origin`, the application stays loopback-only.

This dashboard is still a single shared inference workspace, not a multi-tenant public service: everyone visiting the same deployment sees the same feed, and source/upload controls share one processing pipeline. Do not publish it to anonymous judges until access control, upload/source limits, and isolation are configured for that deployment. A QR invite is an opt-in for Telegram notifications; it does not grant dashboard access.

## Optional notifications and downloads

Legacy single-chat Telegram alerts remain available: set `VIGILANCE_TELEGRAM_BOT_TOKEN` and `VIGILANCE_TELEGRAM_CHAT_ID`, then pass `--telegram`. Enabled incident categories follow configuration. Delivery runs in a bounded worker, retries failed attempts with backoff, reports delivery errors and may drop new queued events when capacity is reached. Frame images leave the machine only for this legacy single-chat integration; enrolled QR recipients receive text only.

The auxiliary model is downloaded only at its recorded immutable revision and digest, unless `--no-threat-download` is used. A verified cache under `models/surveillance/` remains usable offline. It is loaded with the restricted YOLO loader. Download and model initialization can take time at startup.

## Dependency maintenance

`requirements.txt` defines the portable runtime, and `requirements-dev.txt` adds development checks. `requirements-cpu.in` defines the CPU baseline. `requirements-cpu.lock` pins the complete resolved Windows/Python 3.12 CPU graph; `requirements-unit.lock` defines the lightweight test lane. Re-resolve intentionally using `pip-tools` (`python -m pip install pip-tools`, then `python tools/compile_lock.py`) and rerun unit, HTTP, real-model and artifact checks before committing changes.

## CI release configuration

`.github/workflows/ci.yml` runs unit/static checks on pushes and pull requests. Version tags (`v*`) and manual dispatch also run a mandatory model job. Configure repository secrets `FIGHT_MODEL_URL`, `BACKBONE_MODEL_URL`, `POSE_MODEL_URL`, and `OBJECT_MODEL_URL` with HTTPS download URLs for the exact artifacts in the manifest. The loader verifies their recorded SHA-256 and fails on missing or mismatched artifacts. Avoid replacing the manifest with hashes obtained from an unreviewed download during release.

The workflow is prepared locally; remote execution requires a GitHub repository with Actions and these secrets. Semantic accuracy requires a separate labeled evaluation set; the numerical model fixture only detects loading/preprocessing/runtime regressions.

The release model gate also verifies the pinned public auxiliary artifact using `--auxiliary`. Its source revision and digest are fixed in code; it needs no additional secret.
