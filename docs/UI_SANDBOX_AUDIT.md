# RakshaNetra sandbox: UI audit and implementation brief

Audit date: 23 October 2026.

This report records the pre-implementation state. The subsequent frontend changes and verification are documented in [UI_IMPLEMENTATION.md](UI_IMPLEMENTATION.md). Recipient and phone-alert proposals in this audit remain deferred.

## Outcome and scope

The dark navy/cyan control-room aesthetic is worth preserving. The main problem is interaction completeness and truthful state: several controls promise functions that do not exist, four camera tiles represent one processing source, and alert presentation can contradict monitoring status. The current UI is not ready for judges to explore independently.

This pass audits the frontend and specifies the sandbox and recipient experience. It does not implement those features. No application frontend, backend, configuration, model, or weights were changed, and no notification was sent. Backend code was read only where needed to verify what the UI can truthfully offer.

Evidence: the supplied screenshot, all three `web/dashboard.*` files, existing UI tests, and relevant HTTP/status/notification contracts. Alert state defects were checked with deterministic JavaScript fixtures, without running inference. A separate local frontend preview with fixture responses was attempted; the browser bridge timed out for both loopback URLs. Consequently, live browser clicks, responsive screenshots, and actual phone delivery are **not verified** in this audit. Layout/accessibility findings below are based on source and the supplied screenshot.

Priorities below mean: **Launch blocker** = fix before a public judge walkthrough; **High** = needed for a coherent usable sandbox; **Polish** = clarity/finish after core flows work. These are hackathon release priorities, not security vulnerability ratings.

## Existing control inventory

| Control | Current behavior | Required sandbox behavior |
|---|---|---|
| Dashboard | Static selected item, no navigation handler | Overview view; selected state follows navigation |
| Live Monitoring | Opens source modal for CAM-01 | Actual monitoring view with source identity and playback/state controls |
| Incident History | Opens Technical Diagnostics | Searchable/filterable incident list and details |
| Analytics | Opens Technical Diagnostics | Real contextual charts, or remove this navigation item until available |
| Camera Health | Opens Technical Diagnostics | Plain-language source/connection status |
| Settings | Opens Technical Diagnostics | Clearly scoped sandbox preferences |
| Admin dropdown | Opens Technical Diagnostics | Explicit Sandbox visitor identity and appropriate menu |
| CAM-01 feed click | Opens source modal | Select/open the feed; source replacement gets its own labeled action |
| Fullscreen | Uses fullscreen API; failures swallowed | Supported fullscreen/focused view with an error/fallback if unavailable |
| Add Source, CAM-02/03/04 | Changes modal title; submits to the single global source | Honest placeholder status, or explicitly replace the primary source |
| Chart ellipsis buttons | Both open Technical Diagnostics | Relevant labeled action, or remove unnecessary menu |
| Alert: View Live | Scrolls/highlights CAM-01 | Focus current source; separately offer incident evidence review |
| Alert: Dismiss | Hides category for 30 seconds | Label Snooze 30s, or implement explicit acknowledgement |
| Modal tabs | Switch forms by mouse | Accessible tab controls with valid fields and useful guidance |
| Video submit | Uploads to global pipeline | Pending/progress, first-frame confirmation, actionable error/retry |
| Webcam submit | Requests camera index on server hardware | Hide in public sandbox, or clearly identify server-attached hardware |
| RTSP submit | Requests an allowlisted source | Hide from visitors or offer only configured sources |
| Modal/drawer close | Works by mouse | Keyboard close, accessible name, focus restoration |
| Activity cells | Hover tooltip | Visible scale/time range and accessible equivalent |
| Alert recipients | Absent | Dedicated recipient list, add/edit/pause/remove and test preview |
| Start/replay/reset sandbox | Absent | Reproducible first-visit scenario and visitor-local reset |

Navigation/control evidence: `web/dashboard.html:27`, `:36`, `:43`, `:50`, `:58`, `:64`, `:98`, `:124`, `:147`, `:168`, `:189`, `:205`, `:214`, `:256`; `web/dashboard.js:98`, `:112`, `:121`, `:138`, `:147`, `:165`, `:186`, `:208`, `:263`.

## Findings and fixes

### 1. Launch blocker: no sandbox identity or first-visit journey

There is no persistent sandbox badge, explanation, curated scenario, guided start, replay or reset. The page opens on whichever source the local application is running. “Admin” implies an account/role that this frontend does not implement. The title also mixes RakshaNetra and VIGLANCE.

Keep the existing shell, add **RakshaNetra Sandbox**, identify the visitor as **Sandbox visitor**, explain the demonstration in one sentence, and provide **Start demo**, **Choose scenario**, and **Reset sandbox**. Always show whether the video is sample footage, live processing, recorded model evidence, or a scripted simulation. These are separate claims.

Evidence: `web/dashboard.html:7`, `:22`, `:77`, `:105`, `:115`; `web/dashboard.js:21` already distinguishes synthetic inference in its health message and that honesty should be preserved.

### 2. Launch blocker: navigation destinations do not match their names

History, Analytics, Camera Health, Settings, Admin and both chart menus all toggle the same raw Technical Diagnostics drawer. Dashboard stays selected even after these interactions. A judge cannot explore the advertised product.

Implement real frontend views from existing data, with active navigation and browser back/refresh behavior. Give Diagnostics its own explicit advanced control. Remove a product destination until it has useful content rather than redirecting it to unrelated data.

Evidence: `web/dashboard.html:27`, `:36`, `:43`, `:50`, `:58`, `:64`, `:98`, `:205`, `:214`; `web/dashboard.js:235`.

### 3. Launch blocker: camera-slot configuration silently targets the primary source

`targetSlot` changes only the modal title. All three forms submit to global `/api/source` or `/api/upload`; all feed/alert rendering still targets CAM-01. Choosing CAM-03 can replace CAM-01 while CAM-03 remains empty.

With the backend unchanged, keep the grid but mark spare slots **Not configured / expansion slot**. Offer one explicit **Replace primary feed** action, or use clearly labeled frontend sample previews. Do not imply four independent live inference streams. Actual per-camera capture and processing would require backend support later.

Evidence: `web/dashboard.js:137`, `:140`, `:174`, `:192`, `:218`, `:274`, `:343`, `:350`; `core/http_server.py:246` accepts a single source request.

### 4. Launch blocker: monitoring can look current after failure or completion

Non-2xx status responses return without updating UI health. Network exceptions change health/camera count but retain existing ACTIVE badges, overlays, graph and last image. The alert renderer also treats final detections as active when `running:false` or `ended:true`. Camera clocks continue ticking regardless.

Use explicit **Connecting / Monitoring / Observing / Possible incident / Video complete / Disconnected / Error** states. Show last successful status/frame update. On disconnect, mark previous evidence **Last received** and current alert status **Unknown**; at end-of-video, place detections in recorded history. Clear current overlays or identify them as retained evidence. Handle HTTP failures, session expiry and image/stream failures with useful retry/reload actions. Poll sequentially with a timeout so slow requests do not overlap and overwrite newer state.

Evidence: `web/dashboard.js:54`, `:327`, `:437`, `:469`, `:474`; `web/dashboard.html:126`; retained final status in `ui.py:408`.

### 5. Launch blocker: sharing the existing local URL is not a complete sandbox hosting plan

The current server binds to loopback and deliberately checks loopback Host/Origin. It owns one shared source and timeline. A public static copy of the present HTML will also lack its `/api/status` and stream routes. Public source-switch controls could interfere with everyone using a single running instance.

Recommended path under the no-backend-change constraint: a separate static frontend sandbox with packaged sample video and synchronized recorded model evidence, using visitor-local state. Clearly label replay evidence and any scripted demonstration. Preserve the current local app for the team's real inference demonstration. A public live inference service, visitor isolation and independent uploads are a later deployment/integration workstream; they are not solved by UI styling or exposing the current process.

Evidence: `ui.py:796`; `core/http_server.py:97`; `SETUP.md:52`; global request functions in `web/dashboard.js:174`, `:192`, `:218`.

### 6. High: one fight can appear as two incidents

The scene verdict creates `dyn-fight`; the `Violence/Fight` alert detail creates another card. One fixture incident produces two cards and an active count of two, matching the duplicated presentation in the supplied screenshot.

Merge equivalent scene/detail evidence into one incident presentation. Show the strongest relevant score and supporting explanation within that card. Preserve distinct unrelated event types.

Evidence: `web/dashboard.js:340`, `:346`.

### 7. High: “Active Alerts” mixes current and historical events

Inactive red cards remain under Active Alerts indefinitely, while the count correctly drops to zero. That explains the screenshot's apparent contradiction without implying the model itself is wrong.

Split **Active alerts** and **Recent incidents**. Display source/event time, status, and the last observation. Use a neutral historical style with readable controls. Provide evidence details for a past event instead of implying View Live will reveal it again.

Evidence: `web/dashboard.js:353`, `:367`, `:378`; `web/dashboard.css:563`.

### 8. High: dismissal semantics and counts disagree

Dismiss snoozes a category for 30 seconds; it does not acknowledge or resolve an incident. Its immediate recount includes visible stale cards, whereas normal rendering counts active entries. Counts can briefly increase incorrectly until the next successful poll.

Use one count derived from state. Either name the existing action **Snooze 30s**, or introduce explicit local sandbox **Acknowledge**, **Resolve**, and **Mark false positive** states. Explain what each changes. Keep human review decisions separate from model assessment.

Evidence: `web/dashboard.js:93`, `:98`, `:326`, `:375`, `:376`.

### 9. High: client state is keyed by category and leaks across sources

All fights reuse an ID and each alert type has one ID. New incidents overwrite previous ones; a prior snooze can hide a new incident. Client alert/snooze/trend state is not cleared when the backend resets for a new source, and refresh loses the retained cards.

Build sandbox incidents around scenario/event IDs and timestamps, and reset state at source/replay boundaries. Use the timeline for history. For the existing live adapter, a source/time/frame reset can inform a best-effort session boundary; reliable same-source generations and durable incident identity require an API contract later. The backend already has an internal event ID, but it is not exposed in status/timeline.

Evidence: `web/dashboard.js:108`, `:324`, `:342`, `:350`, `:409`; `ui.py:83`, `:106`, `:107`, `:344`, `:485`, `:496`.

### 10. High: no meaningful evidence-review or operator workflow

View Live only highlights CAM-01. There is no incident detail, event-linked footage, acknowledgement, review outcome, or notification summary.

Add an incident detail panel: location, event time, sample/live source identity, original event title and score, evidence image/clip if available, concise explanation and review actions. Replays can seek packaged video locally. The existing live API does not provide an archived incident clip: do not imply that it does.

Evidence: `web/dashboard.js:112`, `:263`, `:274`; endpoint inventory in `core/http_server.py:128`.

### 11. High: source forms announce success before capture succeeds

HTTP acceptance means only “source requested,” but the frontend immediately announces “connected/streaming” and closes. The forms leave submit enabled, use generic errors, omit visible upload limits, and provide no timeout/cancel/progress. A later capture failure appears elsewhere.

Show **Uploading → Source requested → Waiting for first frame → Monitoring**, deriving success from status. Disable duplicate submissions, preserve useful sanitized server error details, provide file limits/type guidance and retry. For public sandbox mode use a curated sample selector; personal uploads require a deliberately supported processing path.

Evidence: `web/dashboard.js:165`, `:175`, `:186`, `:197`, `:208`, `:223`; `core/http_server.py:246`; `ui.py:363`.

### 12. High: webcam and stream controls mislead public visitors

Computer Webcam requests an integer hardware index on the machine running Python, not the visiting browser's camera. RTSP accepts only configured/allowed sources, despite the open URL field.

Hide these controls in public replay mode. In team/local mode label **Server-attached webcam** and offer configured sources with useful error explanation. Capturing a visitor's phone camera requires a browser capture and transport workflow, beyond this audit's frontend repair scope.

Evidence: `web/dashboard.html:265`, `:278`, `:285`; `web/dashboard.js:192`, `:218`; `core/source_utils.py:25`.

### 13. High: no responsive layout

The fixed 220px navigation plus 320px alert rail consume 540px before camera content. The camera and analytics grids always have two columns; body/main/content hide overflow. There are no media queries; the drawer has a fixed 420px width.

At phone widths, use a compact navigation control, stacked feed/alerts and normal vertical page scrolling. At tablet widths, collapse the left navigation and place alerts below or in an accessible panel. Keep the desktop grid. Let metrics wrap and constrain modal/drawer width and height to the viewport. Verify at 390, 768, 1366 and 1920px, plus browser zoom.

Evidence: `web/dashboard.css:23`, `:40`, `:138`, `:196`, `:215`, `:396`, `:464`, `:705`.

### 14. High: keyboard, form and dialog accessibility are incomplete

Navigation, account and video actions are clickable divs. Inputs lack associated labels. Modal/drawer lack appropriate semantics, focus entry/containment/restoration and Escape handling. Close/ellipsis controls lack useful accessible names. The entire alert list is replaced every 500ms, removing a keyboard-focused button.

Use buttons/links, labeled inputs, a correctly named dialog pattern, visible focus, Escape close and focus restoration. Update stable incident DOM elements rather than replacing them on every poll. Expose meaningful alert updates accessibly without announcing every score change.

Evidence: `web/dashboard.html:26`, `:98`, `:124`, `:205`, `:256`, `:260`, `:272`, `:279`, `:286`, `:295`, `:298`; `web/dashboard.js:138`, `:385`, `:474`.

### 15. High: alert recipients and notification state are missing

There is no contact-management UI, channel onboarding, test preview or visible delivery status. The backend already exposes aggregate Telegram enabled/ready/sent/error/dropped state, but the frontend ignores it. Aggregate counts cannot establish per-recipient delivery.

Add the recipient view specified below. In the live/local view, show only the Telegram integration status the existing API actually supplies. In replay mode show explicitly simulated notifications.

Evidence: `web/dashboard.html:26`; `ui.py:506`; frontend polling at `web/dashboard.js:434`.

### 16. Polish: analytics are insufficiently explained

The heatmap represents 72 bins over five minutes of source time, without visible range, axes or legend. The trend represents 40 polling samples, repeats unchanged frames, has no score/time scale and appears blank before scores exist. Previous-source points can remain.

Add a labeled source-time range and legend, a score axis, a meaningful no-score state, and accessible summaries. Add a threshold only if available from the actual scenario/API. Plot new observations against their source timestamps and reset between sources; do not invent missing model scores or interpret them as zero.

Evidence: `web/dashboard.js:66`, `:88`, `:408`, `:414`, `:453`; `web/dashboard.html:199`.

### 17. Polish: event score labels and main-screen language need correction

Every event confidence is labeled Fight model score, including fire/fall/bag/weapon events. “Pair history” and “violent links” expose internal reasoning terminology. Group Fight/Assault labels can also read as established facts rather than reviewable candidates.

Use **Fight model score** for fight evidence and a type-appropriate **Detection score** elsewhere. Avoid treating raw model scores as calibrated probability. Prefer **Observing activity**, **Possible incident — review required**, and a short operator explanation; retain raw diagnostics in Advanced.

Evidence: `web/dashboard.js:41`, `:241`, `:263`, `:280`.

### 18. Polish: video, time and metric presentation reduce trust

`object-fit:cover` can crop evidence at frame edges. Camera clocks show viewer wall time even for prerecorded, inactive or failed feeds, with duplicate header clocks. Camera count assumes online unless `running` is explicitly false. Frame interval is derived from reported processing FPS and remains stale when FPS is absent; it is not end-to-end alert latency.

Contain the evidence image, show source/playback time and last frame age separately from the current clock, use real source connection states, clear unavailable metrics, and rename/explain processing interval. Remove repeated “Lane” and consolidate brand naming.

Evidence: `web/dashboard.css:273`; `web/dashboard.html:120`, `:122`, `:139`, `:141`, `:160`; `web/dashboard.js:54`, `:441`, `:446`; `ui.py:499`.

### 19. Polish: contrast and motion need attention

White text on the primary `#0ea5e9` action background has approximately 2.77:1 contrast; the brighter hover background is worse. Stale-card opacity also dims buttons. ACTIVE badges pulse continuously without a reduced-motion variant.

Use a darker blue background or dark text with suitable contrast, keep inactive-card actions readable, provide reduced-motion styling and make compact controls comfortable to tap.

Evidence: `web/dashboard.css:546`, `:559`, `:563`, `:580`, `:592`.

## Target judge journey

1. Open the link directly into **RakshaNetra Sandbox**. Show a meaningful sample preview immediately and a concise explanation: “Explore how RakshaNetra flags possible incidents for human review.”
2. Select **Start demo**. Show sample/source type, progress and a visible source clock. Offer a normal-activity scenario as well as an incident scenario so the UI demonstrates both outcomes.
3. Present one possible incident with location, event time, model evidence and a clear **Review incident** action. Keep active detections and recent history distinct.
4. Open evidence, then acknowledge, resolve or mark a false positive. Update the incident list, badges and counts consistently. Clearly label these as local sandbox actions.
5. Open **Alert recipients**, add a sample recipient, select event categories, preview the phone message and trigger a clearly simulated test. Show what a real Telegram connection would involve.
6. Use **Replay** or **Reset sandbox** to restore a known state. Reset only this visitor's local data. Provide an inline confirmation/undo for clearing their added recipients and review decisions.

Recommended navigation: **Overview · Monitoring · Incidents · Alert recipients · System status**. Keep analytics as overview content until it merits a full view. Put raw diagnostics behind **Advanced diagnostics**.

## Alert recipients specification

Use a dedicated view and an Add recipient dialog. Call these recipients rather than accounts: adding an alert destination must not imply granting application access.

| Field/action | Behavior |
|---|---|
| Name and optional role | Example: Demo responder / Security volunteer |
| Delivery channel | Telegram first; show other channels only with truthful availability |
| Contact details | Channel-specific destination; phone with country code only where appropriate |
| Location/camera scope | Selected sources or all configured sources; sandbox selection stays local |
| Incident categories | Fight and any other categories actually enabled/represented |
| Enabled/paused | Separate from whether a channel is connected |
| Connection state | Saved in sandbox / Awaiting connection / Connected / Connection failed |
| Actions | Edit, pause/resume, remove with undo, preview alert, test notification |
| Privacy | Use fictional defaults, mask private destination details in list, explain browser-local storage |
| Empty state | Explain who receives alerts and provide Add recipient |

Saving a phone number does not connect notification delivery. Telegram uses a chat ID, not a phone number. A future **Connect Telegram** QR/deep-link flow can bind a recipient to a bot conversation using a one-time enrollment token; it needs backend handling before the UI can verify connection. [Telegram enrollment and deep linking](https://core.telegram.org/bots/features#deep-linking).

For frontend-only replay mode, use browser-local example recipients and **Simulate test alert**. Store no provider secrets and show **Simulated — no message sent**. Real connection verification, test sends, individual routing and delivery history are later integration features. The current service exposes no recipient CRUD, enrollment or test-notification endpoint.

A notification preview should contain **RakshaNetra**, **Possible fight — review required**, camera/location, event time with timezone or explicit sample-video offset, evidence when available, and an incident link only when that evidence route exists. These are target contents; the existing notifier currently uses VIGLANCE, a relative video timestamp and incident title/categories.

## Phone alert recommendation

**Use the existing Telegram integration for the team's live hackathon demonstration.** It already supports text/JPEG sends, incident deduplication, cooldown, a bounded worker queue and retries. It is disabled by default and configured with one chat target. A shared demonstration group allows several people to receive that single-target alert without implementing recipient fan-out.

The intended setup is: create/use the team's bot, add it to a dedicated demonstration group, configure that group's chat ID and the bot token through the existing server environment, then run an explicitly authorized test. For a private chat, the recipient first opens the bot and starts the conversation. A bot cannot initiate a new private conversation just from a phone number. No integration was enabled or tested against a real phone in this audit. [Telegram bot behavior](https://core.telegram.org/bots).

Current implementation evidence: `config.py:95`; `core/alert_dispatch.py:38`, `:118`, `:158`, `:189`; `SETUP.md:56`. Existing transport tests mock Telegram and do not establish real delivery.

Use **Sent to Telegram** for API success. The current code checks Telegram's `ok` response; it does not prove that a phone displayed a notification or that the user read it. Phone notification settings also affect the visible demonstration. [Telegram send API](https://core.telegram.org/bots/api#sendmessage).

| Option | Suitability |
|---|---|
| Telegram | Recommended first: existing server integration; shared group matches current one-target design. Individual verified recipients need later backend work. |
| WhatsApp | Familiar experience, but requires another provider integration and onboarding. Recipient opt-in and approved templates for business-initiated/outside-window messages add preparation. [WhatsApp policy](https://whatsappbusiness.com/policy/) |
| Web push | Suitable later for a mobile web app; needs device subscriptions, service worker and server-side sending. On iPhone, the documented flow uses a Home Screen web app and a user-initiated permission request. [Push overview](https://web.dev/articles/push-notifications-overview), [WebKit requirements](https://webkit.org/blog/13878/web-push-for-web-apps-on-ios-and-ipados/) |
| SMS | Potential fallback later; provider, route and India sender/template onboarding must be checked. Domestic and international routes have different requirements. [Twilio India guidance](https://www.twilio.com/en-us/guidelines/in/sms) |

Keep the public replay sandbox's simulated recipient list distinct from the team's real Telegram group. Do not give arbitrary public visitors control of the live notifier or claim that local sample contacts have been enrolled.

## Implementation sequence and boundaries

### First: frontend foundations

- Add explicit sandbox identity and a frontend replay/live data adapter boundary.
- Package sample footage and actual recorded model evidence, or clearly label scripted simulations.
- Implement real navigation and responsive shell while preserving existing visual identity.
- Make source count/slots and connection state truthful.

### Second: coherent incident experience

- Deduplicate scene/detail cards, separate active/recent, fix ended/disconnected status and counts.
- Add incident details and local review actions, stable incident elements and accessible focus behavior.
- Add source-time playback, replay/reset and contextual analytics/empty states.

### Third: recipients and presentation

- Implement browser-local recipient management, message preview and simulated test states.
- Surface existing aggregate Telegram health in local/live mode without claiming per-contact delivery.
- Finish copy, contrast, keyboard/modal behavior and all error/recovery states.

### Separate later integration/deployment work

- Public live inference hosting and isolation, multiple active cameras, visitor browser-camera capture.
- Persisted shared incidents/recipient accounts, verified enrollment, multi-recipient delivery and provider receipts.
- New WhatsApp, SMS or push integrations and any notification payload enhancements.

These later items are not prerequisites for an honest static replay sandbox, and no backend or model changes are proposed for the frontend phase.

## Acceptance criteria before submitting the link

- First visit identifies the sandbox, sample source and next action without setup instructions from the team.
- Every visible button/menu has a relevant outcome; navigation selection, back/refresh and empty views work.
- One fight produces one incident, correct active count and an evidence view.
- Warm-up, no eligible pair, missing score, ended video, unavailable model, HTTP error and disconnect have distinct truthful UI states.
- Last detections never masquerade as current monitoring after EOF/disconnect.
- Snooze/acknowledge/resolve/false-positive actions are explicit and consistent across views.
- Changing/replaying a source resets relevant history, trend and dismissal state without suppressing a new incident.
- Recipients can be added, validated, edited, paused and removed; refresh/reset behavior is explained.
- Simulated and real delivery are visibly different; API acceptance is not labeled phone delivery/read.
- No extra camera slot can silently replace the active source.
- Phone, tablet and laptop layouts remain usable at 390/768/1366px and 200% zoom.
- Keyboard-only operation works through navigation, incidents, forms and dialogs; focus is retained and restored.
- Video evidence is not cropped; charts explain time, units and missing observations.
- The published sandbox loads its own assets and never depends on a judge's localhost.
- A separate authorized team test confirms any advertised real Telegram notification on an actual phone.

Existing `tests/test_ui.py:183` largely checks served asset strings. Browser-level interaction coverage is still needed for this checklist; those tests cannot establish navigation, focus, state or layout correctness.

## Validation performed

`node --check web/dashboard.js` passed. A Node VM ran the existing alert functions against a minimal DOM stub; no application code was replaced or executed against the real backend. The results were:

| Fixture | Observed result |
|---|---|
| One fight present in both scene verdict and alert detail | Two cards, active count 2 |
| Same final fight payload with running false and ended true | Two ACTIVE badges, active count 2 |
| Next payload has no current fight | Active count 0; two retained historical cards |
| Generic recount used by Dismiss on those retained cards | Active count incorrectly becomes 2 |

The DOM stub validates the function logic only. It does not validate rendering, keyboard behavior, network timing or browser APIs. The source/modal/navigation and layout findings were inspected directly in the current files. No full backend/model test suite was run because no runtime sources were changed and inference is outside this audit.
