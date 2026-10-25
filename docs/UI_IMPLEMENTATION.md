# RakshaNetra frontend refresh

Implemented 23 October 2026. This implements the frontend phase of the earlier UI audit. Recipients, phone alerts, public deployment, backend code and model behavior remain outside this change.

## What changed

- Rebuilt the navy/cyan workspace with a compact brand/navigation rail, persistent sandbox identity, clearer headings, overview metrics, camera grid, incident inbox and contextual analytics.
- Added working hash-based destinations for Overview, Live monitoring, Incident history, Analytics, Camera health and Preferences. Browser back/refresh retain the selected destination.
- Retained one real processing source. The other three camera tiles explicitly show unconfigured expansion slots and link to camera health. They cannot replace CAM-01. On phones, unused slots are listed in Camera health so the primary feed and inbox stay accessible.
- Added grid/focus layouts, fullscreen fallback, a quick tour, accessible native dialogs, labeled source forms, keyboard tab controls, Escape handling and focus restoration.
- Replaced category-card history with source/time-scoped incident episodes. Equivalent fight scene/detail evidence is merged. New episodes retain independent review decisions; source and playback resets isolate activity and reset the score trend.
- Separated current detections from recent incidents. HTTP failures, expired sessions, stale frames and completed videos no longer retain ACTIVE badges or current alert overlays. Unknown activity is shown as unknown.
- Added incident review with an opportunistic snapshot from the existing snapshot endpoint, source time, camera, score and context. Review decisions are local to this browser session; they do not change live detections. Historical events without captured snapshots are labeled accordingly.
- Added incident search, review filters and client-side CSV export. Uploaded source labels are retained for the current visit. CSV output escapes cells and guards formula-leading labels.
- Added upload progress, size/type validation, cancellation, duplicate-submit prevention and first-frame confirmation. Source acceptance is not immediately presented as a successful capture. Server webcam and configured-stream constraints are explained.
- Added browser-local display preferences: workspace name, primary camera label, clock format and initial camera layout. No account or recipient management was introduced.
- Labeled heatmap source-time bins and model-score axes. Repeated polls of the same source frame do not fabricate new chart observations; missing scores remain gaps.
- Reflowed the shell at phone/tablet widths, retained uncropped source images and added reduced-motion support.

## Boundaries

The existing API endpoints, request authentication and inference/notification configuration are unchanged. The frontend uses `/api/status`, `/stream.mjpg`, `/snapshot.jpg`, `/api/source` and `/api/upload`. No frontend framework, external font/CDN, package installation or build step was added.

The sandbox badge identifies the workspace. It does not claim that every source is a simulation: recorded files, live sources and synthetic inference are labeled independently. No fixture incidents or fabricated model scores ship in the application.

Incident history, snapshots and review decisions are bounded browser-session state; refreshing starts a new browser observation session and can reconstruct the current source's limited timeline. Shared persistent incident storage, reliable backend generation IDs, archived video clips, multi-camera processing and a public hosted service remain later work. Source resets are inferred from available source/time/frame fields.

## Validation

- `node --check web/dashboard.js`: passed.
- `node --test tests/frontend.test.cjs`: 14 frontend regression tests passed.
- `.venv/Scripts/python.exe -m pytest tests/test_ui.py tests/test_http_security.py -q --tb=short`: 32 existing synthetic UI and HTTP tests passed. These tests required normal loopback socket access outside the restricted execution sandbox.
- Browser checks used a separate loopback fixture server outside the repository; it did not run detection or real notifications. Checked 390px phone, 768px tablet, 1366px laptop and 1920px desktop layouts, with no page-level horizontal overflow.
- Interactively checked navigation, native dialog Escape/focus restoration, preference persistence, source tabs, webcam request/first-frame confirmation, video selection/upload, incident review and completed/disconnected detection states. Saved desktop/mobile preview screenshots use fixture data and an existing sample-video frame.
- Protected backend/configuration file hashes and model file size/modification records were compared against the pre-change baseline: unchanged.

Use the existing application launch command; the dashboard assets are served directly. Reload a running dashboard to load the new frontend.
