/* RakshaNetra frontend. All processing remains behind the existing API. */
"use strict";

const RakshaUI = (() => {
  const FIGHT_SCENES = ["FIGHT", "MULTI_PERSON_FIGHT", "GROUP_ASSAULT"];
  const FIGHT_TITLES = {
    FIGHT: "Possible fight detected",
    MULTI_PERSON_FIGHT: "Possible group fight",
    GROUP_ASSAULT: "Possible group assault"
  };
  const MAX_STORED_ALERTS = 100;
  const MIN_ACTIVE_ALERT_MS = 10000;
  function isActiveAlert(entry, now = Date.now()) {
    return entry.active || now < (entry.activeUntil || 0);
  }
  const words = {
    "Fall Detection": "Possible fall",
    "Abandoned Bag": "Unattended bag",
    "Unattended Bag Candidate": "Unattended bag",
    "Weapon Threat": "Possible weapon",
    "Fire Threat": "Possible fire",
    "Smoke Threat": "Possible smoke"
  };
  function escapeHTML(value) {
    return String(value ?? "").replace(/[&<>"']/g, c => ({
      "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
    })[c]);
  }
  function score(value) {
    return typeof value === "number" && Number.isFinite(value)
      ? Math.max(0, Math.min(1, value)) : null;
  }
  function topPairScore(s) {
    const values = (s.pairs || []).map(p => score(p.score)).filter(v => v !== null);
    return values.length ? Math.max(...values) : null;
  }
  function sourceLabel(source) {
    if (source === null || source === undefined || source === "" || source === "idle") return "No source connected";
    if (source === "browser-webcam") return "Your webcam";
    if (/^\d+$/.test(String(source))) return "Server webcam " + source;
    if (source === "synthetic") return "Synthetic demonstration";
    if (/^[a-z][a-z\d+.-]*:\/\//i.test(source)) {
      try { const u = new URL(source); return u.hostname + u.pathname; }
      catch (_) { return "Configured stream"; }
    }
    return String(source).split(/[\\/]/).pop() || "Video source";
  }
  function sourceMode(s) {
    if (s.inference_mode === "synthetic") return "Synthetic demo";
    if (!s.source || s.source === "idle") return "No active source";
    if (s.source === "browser-webcam") return "Your webcam";
    return /^\d+$/.test(String(s.source)) || /^(rtsp|rtsps|https?):\/\//i.test(s.source)
      ? "Live source" : "Recorded video";
  }
  function sourceTime(value) {
    const seconds = Math.max(0, Math.floor(Number(value) || 0));
    const hh = Math.floor(seconds / 3600), mm = Math.floor(seconds / 60) % 60, ss = seconds % 60;
    return (hh ? String(hh).padStart(2, "0") + ":" : "") +
      String(mm).padStart(2, "0") + ":" + String(ss).padStart(2, "0");
  }
  function isCapturing(s) {
    return s.running === true && !s.ended && !s.error;
  }
  function category(name) {
    return /violence.*fight|^fight$|^multi_person_fight$|^group_assault$/i.test(name)
      ? "fight" : (/^(Abandoned Bag|Unattended Bag Candidate)$/.test(name) ? "bag" : name);
  }
  function savedKey(id, kind) { return "incident:" + id + ":" + kind; }
  function titleFor(kind, scene) {
    return kind === "fight" ? FIGHT_TITLES[scene] || FIGHT_TITLES.FIGHT
      : kind === "bag" ? "Unattended bag" : words[kind] || String(kind).replace(/_/g, " ");
  }
  function currentDetections(s) {
    const details = s.alert_details || {}, merged = new Map();
    const verdict = s.scene?.verdict;
    if (FIGHT_SCENES.includes(verdict)) {
      const people = Math.max(0, ...(s.scene.incidents || []).map(i => (i.members || []).length));
      merged.set("fight", {
        kind: "fight", title: titleFor("fight", verdict), score: topPairScore(s),
        reason: people ? "Possible violent interaction involving " + people + " people. Review the evidence."
          : "Possible violent interaction. Review the evidence before acting."
      });
    }
    for (const [name, detail] of Object.entries(details)) {
      const kind = category(name), previous = merged.get(kind);
      const value = score(detail?.confidence);
      merged.set(kind, {
        kind, title: previous?.title || titleFor(kind, verdict),
        score: value ?? previous?.score ?? null,
        reason: previous?.reason || detail?.message || "Review the observed activity."
      });
    }
    return [...merged.values()];
  }
  function health(s, connected, stale = false) {
    if (!connected) return {
      tone: "error", label: "Disconnected", title: "Connection interrupted",
      detail: "Current activity is unknown. Previous incidents remain available for review."
    };
    if (s.error) return {tone: "error", label: "Capture error", title: "Source needs attention", detail: String(s.error)};
    if (s.ended) return {
      tone: "neutral", label: "Video complete", title: "Video processing complete",
      detail: "Review recorded incidents or connect another source to continue."
    };
    if (stale) return {
      tone: "warning", label: "Waiting for frames", title: "No recent frames",
      detail: "The last received image may be out of date. Current detection status is unknown."
    };
    if (!isCapturing(s)) return {
      tone: "neutral", label: "Awaiting source", title: "Connect your first source",
      detail: "Open a webcam, connect a camera, or try a test video."
    };
    if (s.inference_mode === "synthetic") return {
      tone: "warning", label: "Synthetic demo", title: "Scripted demonstration",
      detail: s.model?.head_trained ? "Model running on scripted skeletons. Select model mode for real video." : "Scripted skeletons only. Real model detection is disabled."
    };
    const failures = Object.keys(s.scenario_errors || {}).length;
    const optionalOffline = Object.values(s.threat_models || {}).some(m => !m.ready);
    if (failures || optionalOffline) return {
      tone: "warning", label: "Partial monitoring", title: "Some assessments unavailable",
      detail: "The source is connected, but an assessment needs attention. See camera health or diagnostics."
    };
    switch (s.fight_status) {
      case "ASSESSED": return {tone: "good", label: "Monitoring active", title: "Your source is being assessed", detail: "Possible incidents will appear here for human review."};
      case "WARMING_UP": return {tone: "neutral", label: "Observing activity", title: "Building a clearer picture", detail: "Collecting enough person-pair observations to assess possible fights."};
      case "DISABLED": return {tone: "warning", label: "Detection disabled", title: "Fight detection is disabled", detail: "The feed is available; no fight assessment is being made."};
      case "ERROR": return {tone: "error", label: "Assessment error", title: "Assessment needs attention", detail: "The feed may be available, but fight assessment encountered an error."};
      default: return {tone: "warning", label: "Assessment unavailable", title: "Fight assessment unavailable", detail: "A connected feed does not establish that fight detection is available."};
    }
  }

  /* Prefer backend event IDs and source generations; retain compatibility with
     older servers that expose only timeline starts. */
  function createSession() {
    const alertStore = new Map(), episodes = new Map(), samples = [];
    let epoch = 0, sequence = 0, previous = null, frameKey = null;
    function consume(s, now = Date.now(), fresh = true, camera = "Main gate", displaySource = sourceLabel(s.source)) {
      const changed = previous && (
        (s.source_generation !== undefined && previous.generation !== undefined && s.source_generation !== previous.generation) ||
        (s.source && previous.source && s.source !== previous.source) ||
        (Number.isFinite(s.ts) && Number.isFinite(previous.ts) && s.ts < previous.ts - 0.25) ||
        (Number.isFinite(s.frame) && Number.isFinite(previous.frame) && s.frame < previous.frame)
      );
      if (changed) { epoch++; episodes.clear(); samples.length = 0; frameKey = null; }
      for (const entry of alertStore.values()) {
        entry.uncertain = !fresh && (entry.active || entry.uncertain);
        entry.active = false;
      }
      const timeline = Array.isArray(s.timeline) ? s.timeline : [];
      const created = [], evicted = [], capturing = isCapturing(s) && fresh;
      function upsert(kind, start, end, scene, backendId, observedAt) {
        const persistent = /^[0-9a-f]{32}$/.test(backendId || "");
        const id = persistent ? savedKey(backendId, kind) : epoch + ":" + (backendId ?? start) + ":" + kind;
        let entry = alertStore.get(id);
        if (!entry) {
          entry = {
            id, serial: ++sequence, kind, title: titleFor(kind, scene),
            start, end, source: displaySource, camera, epoch,
            score: null, reason: "Recorded activity. Review the available context.",
            review: "unreviewed", active: false, uncertain: false,
            savedId: persistent ? backendId : null,
            firstSeen: Number.isFinite(observedAt) ? Math.min(now, observedAt * 1000) : now,
            lastSeen: null, snapshot: null, synthetic: s.inference_mode === "synthetic"
          };
          entry.activeUntil = entry.firstSeen + MIN_ACTIVE_ALERT_MS;
          alertStore.set(id, entry); created.push(entry);
        }
        entry.end = Math.max(entry.end, end);
        return entry;
      }
      for (const event of timeline) {
        if (!Number.isFinite(event.start) || !Number.isFinite(event.end)) continue;
        const kinds = new Set((event.alerts || []).map(category));
        if (FIGHT_SCENES.includes(event.scene)) kinds.add("fight");
        for (const kind of kinds) {
          const entry = upsert(kind, event.start, event.end, event.scene, event.id, event.observed_at);
          if (score(event.confidence) !== null) entry.score = score(event.confidence);
          if (event.id && Array.isArray(s.active_event_ids)) {
            entry.active = capturing && s.active_event_ids.includes(event.id);
            if (entry.active) { entry.lastSeen = now; entry.uncertain = false; }
          }
        }
      }
      const detections = capturing ? currentDetections(s) : [];
      const firing = new Set();
      for (const detection of detections) {
        if (Array.isArray(s.active_event_ids) && timeline.some(e => e.id && s.active_event_ids.includes(e.id) &&
          ((e.alerts || []).some(a => category(a) === detection.kind) || (detection.kind === "fight" && FIGHT_SCENES.includes(e.scene))))) continue;
        firing.add(detection.kind);
        const candidates = timeline.filter(e =>
          Number.isFinite(e.start) && Number.isFinite(e.end) &&
          e.start <= Number(s.ts) + 0.1 && e.end >= Number(s.ts) - 1 &&
          ((e.alerts || []).some(a => category(a) === detection.kind) ||
            (detection.kind === "fight" && FIGHT_SCENES.includes(e.scene)))
        );
        const event = candidates[candidates.length - 1];
        if (!episodes.has(detection.kind)) episodes.set(detection.kind, Number(s.ts) || 0);
        const start = event ? event.start : episodes.get(detection.kind);
        const entry = upsert(detection.kind, start, Number(s.ts) || 0, s.scene?.verdict, event?.id, event?.observed_at);
        Object.assign(entry, detection, {active: true, lastSeen: now});
      }
      for (const kind of episodes.keys()) if (!firing.has(kind)) episodes.delete(kind);
      while (alertStore.size > MAX_STORED_ALERTS) {
        const oldest = [...alertStore.values()].find(entry => !isActiveAlert(entry, now));
        if (!oldest) break;
        alertStore.delete(oldest.id); evicted.push(oldest);
      }
      const nextKey = epoch + ":" + s.frame + ":" + s.ts;
      if (capturing && Number.isFinite(s.ts) && nextKey !== frameKey) {
        samples.push({time: s.ts, score: topPairScore(s)});
        if (samples.length > 80) samples.shift();
        frameKey = nextKey;
      }
      previous = {source: s.source, ts: s.ts, frame: s.frame, generation: s.source_generation};
      return {created, evicted, changed};
    }
    function disconnect() {
      for (const entry of alertStore.values()) {
        if (entry.active) entry.uncertain = true;
        entry.active = false;
      }
    }
    return {alertStore, samples, consume, disconnect, get epoch() { return epoch; }};
  }
  function csvCell(value) {
    // Prevent spreadsheet formula interpretation when exporting user labels.
    const text = String(value ?? "");
    return '"' + (/^[=+\-@\t\r]/.test(text) ? "'" : "") + text.replace(/"/g, '""') + '"';
  }
  return {escapeHTML, score, topPairScore, sourceLabel, sourceMode, sourceTime,
    isCapturing, currentDetections, health, createSession, csvCell, category, savedKey, MAX_STORED_ALERTS,
    isActiveAlert, MIN_ACTIVE_ALERT_MS};
})();
if (typeof module !== "undefined" && module.exports) module.exports = RakshaUI;

if (typeof document !== "undefined") (() => {
  const {escapeHTML: esc, score, topPairScore, sourceLabel, sourceMode, sourceTime, isCapturing, health} = RakshaUI;
  const $ = id => document.getElementById(id);
  let sessionToken = document.querySelector('meta[name="vigilance-token"]').content;
  let sessionBootstrap = null;
  const apiBase = String(window.VIGILANCE_CONFIG?.apiBaseUrl || "").replace(/\/$/, "");
  if (apiBase && new URL(apiBase).protocol !== "https:") throw new Error("Remote API must use HTTPS");
  const endpoint = path => apiBase + path;
  async function ensureSession() {
    if (sessionToken && sessionToken !== "__VIGILANCE_TOKEN__") return sessionToken;
    if (!sessionBootstrap) sessionBootstrap = (async () => {
      const response = await fetch(endpoint("/api/session"), {cache: "no-store", signal: AbortSignal.timeout(7000)});
      if (!response.ok) throw new Error("Cannot establish the backend session");
      const data = await response.json();
      if (typeof data.token !== "string" || data.token.length < 32) throw new Error("Invalid backend session");
      sessionToken = data.token;
      return sessionToken;
    })().finally(() => { sessionBootstrap = null; });
    return sessionBootstrap;
  }
  if (apiBase) $("liveStream").src = endpoint("/stream.mjpg");
  const session = RakshaUI.createSession(), alertStore = session.alertStore;
  const clearedRecentKey = "rakshanetra.cleared-recent." + (apiBase || location.origin);
  const clearedRecent = new Set();
  try {
    const stored = JSON.parse(localStorage.getItem(clearedRecentKey) || "[]");
    if (Array.isArray(stored)) for (const id of stored.slice(-1000)) {
      if (typeof id === "string" && id.length <= 160) clearedRecent.add(id);
    }
  } catch (_) {}
  let pendingRecentClear = [];
  const defaults = {workspace: "Judge workspace", camera: "Active feed", clock: "12", layout: "grid"};
  const reviewLabels = {unreviewed: "Needs review", acknowledged: "Acknowledged", resolved: "Resolved", "false-positive": "False positive"};
  const views = {
    overview: ["Overview", "Session overview", "Detection and incident totals for this session."],
    monitoring: ["Live monitoring", "Live monitoring", "Connect a camera. Detect activity. Review incidents."],
    incidents: ["Incident history", "Incident history", "Search and review recorded detections."],
    analytics: ["Analytics", "Analytics", "Observed activity and model scores."],
    health: ["Camera health", "Camera health", "Capture, models, and connection status."],
    recipients: ["Alert recipients", "Alert recipients", "Link devices and check notification delivery."],
    settings: ["Preferences", "Preferences", "Saved on this device."]
  };
  const recipientView = document.createElement("section");
  recipientView.className = "view-section";
  recipientView.dataset.views = "recipients";
  recipientView.hidden = true;
  recipientView.setAttribute("aria-labelledby", "recipientsHeading");
  recipientView.innerHTML = `
    <article id="phonePanel" class="panel recipient-panel" hidden>
      <p id="phoneWorkspace" class="small muted" hidden></p>
      <label id="phoneWorkspacePicker" hidden>Recipient workspace
        <select id="phoneWorkspaceSelect"><option value="demo">Public phone app</option><option value="live" selected>Protected live alerts</option></select>
      </label>
      <div class="recipient-actions">
        <button id="phoneInviteButton" class="button primary" type="button" disabled>Generate QR</button>
        <button id="phoneTestButton" class="button secondary" type="button" disabled>Send test alert</button>
        <p id="phonePushState" class="small muted" hidden></p>
      </div>
      <div id="phoneInvite" class="recipient-invite phone-invite" hidden>
        <img id="phoneInviteQR" alt="One-time phone enrollment QR">
        <div class="phone-invite-copy">
          <div class="phone-invite-state"><span class="phone-wait-spinner" aria-hidden="true"></span><strong id="phoneInviteStatus" role="status">Waiting for phone</strong></div>
          <p class="muted small" id="phoneInviteHelp">Scan with your phone camera to link RakshaNetra.</p>
          <p id="phoneInviteAddress" class="phone-invite-address muted small"></p>
          <p id="phoneInviteExpiry" class="muted small"></p>
          <details id="phoneInviteManual">
            <summary>Use an invite code</summary>
            <label for="phoneInviteCode">Manual invite code</label>
            <input id="phoneInviteCode" readonly autocomplete="off" spellcheck="false">
            <a id="phoneInviteLink" target="_blank" rel="noopener noreferrer">Open invitation</a>
          </details>
          <button id="phoneInviteCancel" class="button secondary small-button" type="button">Cancel QR</button>
        </div>
      </div>
      <div id="phoneLinked" class="phone-linked" role="status" hidden><span class="phone-linked-icon" aria-hidden="true">✓</span><div><strong id="phoneLinkedName"></strong><p>Finish setup by enabling notifications in the installed app.</p></div></div>
      <div class="recipient-list-heading"><h2 id="recipientsHeading">Linked devices <span id="phoneDeviceCount" class="recipient-total">0</span></h2></div>
      <div id="phoneDeviceRows" class="recipient-rows"></div>
      <p id="phoneMessage" class="form-message" role="status"></p>
    </article>`;
  document.querySelector(".primary-column").append(recipientView);
  let preferences = {...defaults};
  try {
    const stored = JSON.parse(localStorage.getItem("rakshanetra.preferences") || "{}");
    for (const key of ["workspace", "camera"]) if (typeof stored[key] === "string" && stored[key].trim()) preferences[key] = stored[key].trim().slice(0, 40);
    if (["12", "24"].includes(stored.clock)) preferences.clock = stored.clock;
    if (["grid", "focus"].includes(stored.layout)) preferences.layout = stored.layout;
  } catch (_) { /* Private browsing can disable storage. */ }
  let status = {}, connected = false, stale = false, lastUpdate = 0, lastFrameAt = 0;
  let lastFrameIdentity = "", currentView = "", inboxTab = "active", streamFailed = false;
  let selectedIncident = null, pendingSource = null, sourceBusy = false, uploadRequest = null;
  let polling = false, pollTimer = null, toastTimer = null, expiredSession = false;
  let capabilities = null, webcamStream = null, webcamToken = "", webcamTimer = null, webcamAttempt = 0;
  let pendingCautionAction = null, cautionBypassedThisSession = false;
  try { cautionBypassedThisSession = sessionStorage.getItem("rakshanetra.caution_bypassed") === "true"; } catch (_) {}
  let notificationsEnabled = false, previousNotice = "";
  try { notificationsEnabled = localStorage.getItem("rakshanetra.notifications") === "true"; } catch (_) {}
  const cardNodes = new Map(), rowNodes = new Map(), dialogOpeners = new Map(), sourceAliases = new Map();
  const displaySource = () => sourceAliases.get(status.source) || sourceLabel(status.source);
  const icon = name => '<svg class="icon" aria-hidden="true"><use href="#i-' + name + '"/></svg>';
  function text(id, value) {
    const el = $(id), next = String(value ?? "");
    if (el && el.textContent !== next) el.textContent = next;
  }
  function setMessage(message, tone = "") {
    text("modalMessage", message); $("modalMessage").className = "form-message " + tone;
  }
  function toast(message, tone = "info") {
    $("toast").dataset.tone = tone;
    text("toastMessage", message); $("toast").hidden = false;
    clearTimeout(toastTimer); toastTimer = setTimeout(() => { $("toast").hidden = true; }, 4000);
  }
  function showDialog(id) {
    const dialog = $(id);
    if (!dialog.open) {
      dialogOpeners.set(id, document.activeElement);
      closeNavigation(); dialog.showModal();
    }
  }
  function closeDialog(id) { if ($(id).open) $(id).close(); }
  document.querySelectorAll("[data-close]").forEach(button =>
    button.addEventListener("click", () => closeDialog(button.dataset.close)));
  document.querySelectorAll("dialog").forEach(dialog => {
    dialog.addEventListener("close", () => {
      if (dialog.id === "recipientCautionDialog") pendingCautionAction = null;
      if (dialog.id === "sourceModal" && uploadRequest) uploadRequest.abort();
      if (dialog.id === "sourceModal" && !webcamToken) { stopBrowserCamera(false); setSourceBusy(false); }
      const opener = dialogOpeners.get(dialog.id);
      if (opener?.isConnected && !opener.closest("[hidden],[inert]")) opener.focus({preventScroll: true});
      else if (window.matchMedia("(max-width: 760px)").matches) $("menuButton").focus({preventScroll: true});
      else $("main-content").focus({preventScroll: true});
    });
    dialog.addEventListener("click", event => {
      if (event.target !== dialog) return;
      const rect = dialog.getBoundingClientRect();
      if (event.clientX < rect.left || event.clientX > rect.right || event.clientY < rect.top || event.clientY > rect.bottom) closeDialog(dialog.id);
    });
  });
  function closeNavigation() {
    document.body.classList.remove("nav-open"); $("navBackdrop").hidden = true;
    $("main-wrapper").inert = false;
    $("menuButton").setAttribute("aria-expanded", "false");
    if (window.matchMedia("(max-width: 760px)").matches) $("sidebar").inert = true;
  }
  function navigate() {
    const requested = window.location.hash.slice(1);
    if (requested === "main-content") { $("main-content").focus(); return; }
    const next = Object.hasOwn(views, requested) ? requested : "monitoring";
    const changed = currentView !== next, hadView = Boolean(currentView);
    currentView = next; document.body.dataset.view = next;
    document.querySelectorAll("a[data-view]").forEach(link => {
      const active = link.dataset.view === next;
      link.classList.toggle("active", active);
      if (active) link.setAttribute("aria-current", "page"); else link.removeAttribute("aria-current");
    });
    document.querySelectorAll("[data-views]").forEach(section => {
      section.hidden = !section.dataset.views.split(" ").includes(next);
    });
    const [label, title, description] = views[next];
    text("breadcrumbTitle", label);
    text("pageTitle", title);
    text("pageDescription", description);
    const showInbox = ["overview", "monitoring"].includes(next);
    $("alerts-sidebar").hidden = !showInbox; $("workspaceGrid").classList.toggle("full-width", !showInbox);
    $("globalStats").hidden = next !== "overview";
    $("connectButton").hidden = next === "settings" || next === "recipients";
    document.title = "RakshaNetra · " + label;
    closeNavigation(); render();
    if (next === "recipients") loadPhoneDevices();
    if (changed) {
      if (hadView) $("main-content").focus({preventScroll: true});
      window.scrollTo({top: 0, behavior: "instant"});
    }
  }
  window.addEventListener("hashchange", navigate);
  document.querySelectorAll('a[href^="#"]').forEach(link => link.addEventListener("click", () => {
    if (link.getAttribute("href") === "#" + currentView) {
      closeNavigation(); $("main-content").focus({preventScroll: true});
    }
  }));
  $("menuButton").addEventListener("click", () => {
    const open = !document.body.classList.contains("nav-open");
    document.body.classList.toggle("nav-open", open); $("navBackdrop").hidden = !open;
    $("main-wrapper").inert = open;
    $("sidebar").inert = !open; $("menuButton").setAttribute("aria-expanded", String(open));
    if (open) $("sidebar").querySelector('[aria-current="page"]')?.focus();
  });
  $("navBackdrop").addEventListener("click", () => { closeNavigation(); $("menuButton").focus(); });
  document.addEventListener("keydown", e => {
    if (e.key === "Escape" && document.body.classList.contains("nav-open")) {
      closeNavigation(); $("menuButton").focus();
    }
  });
  const mobileQuery = window.matchMedia("(max-width: 760px)");
  mobileQuery.addEventListener("change", e => { if (!e.matches) { $("sidebar").inert = false; closeNavigation(); } else closeNavigation(); });

  const slots = [
    ["02", "Your webcam", "Use the camera on this device", "webcam", "Open webcam"],
    ["03", "IP camera", "Connect a configured RTSP / HTTP camera", "rtsp", "Connect camera"],
    ["04", "Video upload", "Test detection with a recorded clip", "video", "Upload video"]
  ];
  for (const [number, name, caption, tab, action] of slots) {
    const article = document.createElement("article"); article.className = "camera-card standby-camera";
    article.innerHTML = '<div class="camera-header"><div class="camera-name"><span class="camera-code">' + number +
      '</span><strong>' + name + '</strong></div><span class="camera-state">Source option</span></div><div class="cam-placeholder"><span class="placeholder-icon">' +
      icon("camera") + '</span><strong>Add camera source</strong><p>' + caption + '</p><button type="button" class="text-button add-camera-source">' + action + ' ' +
      icon("arrow") + '</button></div><div class="camera-footer"><span>Opens in the active feed</span><span class="slot-mark">' + number + '</span></div>';
    $("cameraGrid").append(article);
    const triggerSource = () => {
      if (tab === "video" || tab === "webcam") {
        verifyRecipientBeforeAction(() => openSource(tab));
      } else {
        openSource(tab);
      }
    };
    article.querySelector(".add-camera-source").addEventListener("click", triggerSource);
    const reserve = document.createElement("div");
    reserve.innerHTML = '<span class="camera-code">' + number + '</span><strong>' + name + '</strong><button class="text-button">' + action + '</button>';
    reserve.querySelector("button").addEventListener("click", triggerSource);
    $("reserveSlots").append(reserve);
  }
  function setLayout(layout) {
    $("cameraGrid").classList.toggle("focus-layout", layout === "focus");
    for (const [id, value] of [["gridButton", "grid"], ["focusButton", "focus"]]) {
      $(id).classList.toggle("selected", layout === value); $(id).setAttribute("aria-pressed", String(layout === value));
    }
  }
  function updateClock() {
    text("wallClock", new Date().toLocaleTimeString([], {hour: "2-digit", minute: "2-digit", hour12: preferences.clock === "12"}));
  }
  function applyPreferences() {
    text("cameraName", preferences.camera); text("healthCameraName", preferences.camera);
    $("workspaceInput").value = preferences.workspace; $("cameraInput").value = preferences.camera;
    $("clockFormat").value = preferences.clock; $("layoutPreference").value = preferences.layout;
    setLayout(preferences.layout); updateClock();
  }
  $("gridButton").addEventListener("click", () => setLayout("grid"));
  $("focusButton").addEventListener("click", () => setLayout("focus"));
  $("preferencesForm").addEventListener("submit", event => {
    event.preventDefault();
    const workspace = $("workspaceInput").value.trim(), camera = $("cameraInput").value.trim();
    if (!workspace || !camera) { text("preferencesMessage", "Names cannot be blank."); return; }
    preferences = {workspace, camera, clock: $("clockFormat").value, layout: $("layoutPreference").value};
    let saved = true;
    try { localStorage.setItem("rakshanetra.preferences", JSON.stringify(preferences)); } catch (_) { saved = false; }
    applyPreferences();
    text("preferencesMessage", saved ? "Preferences saved in this browser." : "Applied for this visit. Browser storage is unavailable.");
    toast(saved ? "Workspace preferences saved." : "Preferences applied for this visit.");
  });
  $("tourButton").addEventListener("click", () => showDialog("guideDialog"));
  $("tourStartButton").addEventListener("click", () => { closeDialog("guideDialog"); window.location.hash = "monitoring"; });
  $("diagnosticsButton").addEventListener("click", () => showDialog("techDrawer"));
  $("fullscreenButton").addEventListener("click", async () => {
    try {
      if (document.fullscreenElement) await document.exitFullscreen();
      else if ($("cardCam1").requestFullscreen) await $("cardCam1").requestFullscreen();
      else { setLayout("focus"); toast("Your browser does not support fullscreen. Camera focus is enabled."); }
    } catch (_) { setLayout("focus"); toast("Fullscreen is unavailable. Camera focus is enabled."); }
  });
  document.addEventListener("fullscreenchange", () => {
    $("fullscreenButton").setAttribute("aria-label", document.fullscreenElement ? "Exit camera fullscreen" : "Open camera fullscreen");
  });

  async function apiFetch(url, options = {}, timeout = 7000) {
    await ensureSession();
    const controller = new AbortController(), timer = setTimeout(() => controller.abort(), timeout);
    const headers = new Headers(options.headers || {}); headers.set("X-Vigilance-Token", sessionToken);
    try { return await fetch(endpoint(url), {...options, headers, credentials: "include", signal: controller.signal, cache: "no-store"}); }
    finally { clearTimeout(timer); }
  }
  let phoneData = null, phoneLoading = false, phoneLoadedAt = -Infinity, phoneInviteExpires = 0;
  let activePhoneInvite = null, activePhoneOrigin = null, phoneInviteChecking = false, sendingPhoneTest = false;
  let phoneWorkspaceReady = false;
  const postJSON = value => ({method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(value)});
  async function phoneAdmin(path = "", payload) {
    const base = $("phoneWorkspaceSelect").value === "demo" ? "/api/phone-demo-admin" : "/api/phone-admin";
    return readResponse(await apiFetch(base + path, payload === undefined ? {} : postJSON(payload)));
  }
  $("phoneWorkspaceSelect").addEventListener("change", () => {
    $("phoneLinked").hidden = true;
    $("phoneDeviceRows").replaceChildren();
    delete $("phoneDeviceRows").dataset.devices;
    text("phoneMessage", "");
    loadPhoneDevices(true);
  });
  function renderPhoneDevices(devices) {
    const rows = $("phoneDeviceRows");
    text("phoneDeviceCount", devices.length);
    const signature = JSON.stringify(devices);
    if (rows.dataset.devices === signature) return;
    rows.dataset.devices = signature;
    rows.replaceChildren();
    if (!devices.length) {
      const empty = document.createElement("p");
      empty.className = "recipient-empty";
      empty.textContent = "No linked devices yet. Generate a QR to add one.";
      rows.append(empty);
      return;
    }
    for (const device of devices) {
      const row = document.createElement("div"), details = document.createElement("div");
      row.className = "recipient-row"; details.className = "recipient-identity";
      const name = document.createElement("strong"), state = document.createElement("span");
      name.textContent = device.name;
      state.textContent = device.role === "responder" ? "Campus security" : "Alert recipient";
      const delivery = document.createElement("span");
      delivery.className = "recipient-status " + (device.subscribed ? "is-active" : "is-paused");
      delivery.textContent = device.subscribed ? "Notifications on" : "Notifications off";
      const revoke = document.createElement("button");
      revoke.type = "button"; revoke.className = "button secondary small-button"; revoke.textContent = "Unlink";
      revoke.setAttribute("aria-label", "Unlink " + device.name);
      revoke.addEventListener("click", async () => {
        if (!window.confirm("Unlink " + device.name + "?")) return;
        revoke.disabled = true;
        try {
          await phoneAdmin("/revoke", {id: device.id});
          if ($("phoneLinked").dataset.deviceId === device.id) $("phoneLinked").hidden = true;
          await loadPhoneDevices(true);
          text("phoneMessage", "Device unlinked.");
        }
        catch (error) { text("phoneMessage", error.message); revoke.disabled = false; }
      });
      const avatar = document.createElement("span");
      avatar.className = "recipient-avatar";
      avatar.setAttribute("aria-hidden", "true");
      avatar.textContent = device.name.trim().charAt(0).toUpperCase();
      details.append(name);
      if (device.role === "responder") details.append(state);
      row.append(avatar, details, delivery, revoke); rows.append(row);
    }
  }
  async function loadPhoneDevices(force = false) {
    if (!status.incident_history || phoneLoading || (!force && Date.now() - phoneLoadedAt < 3500)) return;
    $("phonePanel").hidden = false;
    phoneLoading = true; phoneLoadedAt = Date.now();
    const workspaceSelect = $("phoneWorkspaceSelect");
    workspaceSelect.disabled = true;
    try {
      let data = await phoneAdmin();
      if (!phoneWorkspaceReady && data.demo_available) {
        workspaceSelect.value = "demo";
        data = await phoneAdmin();
      }
      if (workspaceSelect.value === "demo") $("phoneWorkspacePicker").hidden = false;
      phoneData = data;
      phoneWorkspaceReady = true;
      if (phoneData.demo_available) $("phoneWorkspacePicker").hidden = false;
      text("phoneWorkspace", workspaceSelect.value === "demo" || status.inference_mode === "synthetic" ?
        "Public phone app · Detection notices" : "Protected phone app · Camera alerts");
      $("phoneWorkspace").hidden = false;
      if (activePhoneInvite && phoneData.origin && activePhoneOrigin !== phoneData.origin) {
        clearPhoneInvite();
        text("phoneMessage", "The phone address changed. Generate a new QR.");
      }
      text("phoneInviteButton", phoneData.phone_https ? "Generate QR" : "Create local invite");
      $("phoneTestButton").disabled = sendingPhoneTest || !phoneData.devices.some(device => device.role === "recipient");
      const notice = !phoneData.phone_https ? "Configure recipient HTTPS on this server to generate a phone QR." :
        phoneData.phone_https && !phoneData.push ? "Background notifications are unavailable for this workspace." : "";
      text("phonePushState", notice);
      $("phonePushState").hidden = !notice;
      renderPhoneDevices(phoneData.devices);
      const confirmation = $("phoneLinked");
      if (!confirmation.hidden) {
        const device = phoneData.devices.find(device => device.id === confirmation.dataset.deviceId);
        confirmation.hidden = !device;
        confirmation.querySelector("p").hidden = !!device?.subscribed;
      }
    } catch (error) { text("phoneMessage", error.message); }
    finally {
      phoneLoading = false;
      workspaceSelect.disabled = !!activePhoneInvite || sendingPhoneTest;
      $("phoneInviteButton").disabled = !phoneWorkspaceReady || !!activePhoneInvite;
    }
  }
  function clearPhoneInvite() {
    activePhoneInvite = null;
    activePhoneOrigin = null;
    phoneInviteExpires = 0;
    $("phoneInvite").hidden = true;
    $("phoneInviteQR").removeAttribute("src");
    text("phoneInviteAddress", "");
    $("phoneInviteCode").value = "";
    $("phoneInviteLink").removeAttribute("href");
    $("phoneInviteManual").open = false;
    $("phoneInviteButton").disabled = false;
    $("phoneWorkspaceSelect").disabled = phoneLoading || sendingPhoneTest;
  }
  function showPhoneInviteResult(result) {
    if (result.status === "waiting") {
      text("phoneInviteStatus", "Waiting for phone");
      return;
    }
    clearPhoneInvite();
    if (result.status === "linked") {
      text("phoneMessage", "");
      text("phoneLinkedName", result.device.name + " linked");
      $("phoneLinked").dataset.deviceId = result.device.id;
      $("phoneLinked").querySelector("p").hidden = result.device.subscribed;
      $("phoneLinked").hidden = false;
      loadPhoneDevices(true);
      return;
    }
    text("phoneMessage", result.status === "canceled" ? "Invite canceled. You can generate a new QR." :
      "The invite expired. Generate a new QR.");
  }
  async function checkPhoneInvite() {
    if (!activePhoneInvite || phoneInviteChecking || $("phoneInviteCancel").disabled) return;
    const code = activePhoneInvite;
    phoneInviteChecking = true;
    try {
      const result = await phoneAdmin("/invite/progress", {code});
      if (activePhoneInvite === code && !$("phoneInviteCancel").disabled) showPhoneInviteResult(result);
    } catch (_) {
      if (activePhoneInvite === code && !$("phoneInviteCancel").disabled) text("phoneInviteStatus", "Reconnecting to check invite…");
    } finally { phoneInviteChecking = false; }
  }
  $("phoneInviteButton").addEventListener("click", async event => {
    if (!phoneWorkspaceReady || phoneLoading || activePhoneInvite) return;
    const button = event.currentTarget; button.disabled = true;
    $("phoneWorkspaceSelect").disabled = true;
    try {
      const invite = await phoneAdmin("/invite", {role: "recipient"});
      $("phoneLinked").hidden = true;
      const qr = $("phoneInviteQR");
      const inviteUrl = new URL(invite.url);
      const scannable = invite.qr && inviteUrl.protocol === "https:";
      qr.hidden = !scannable;
      if (scannable) qr.src = invite.qr; else qr.removeAttribute("src");
      $("phoneInviteLink").href = invite.url; $("phoneInviteCode").value = invite.code;
      text("phoneInviteLink", scannable ? "Open invitation" : "Open local preview invitation");
      phoneInviteExpires = invite.expires * 1000;
      activePhoneInvite = invite.code;
      activePhoneOrigin = inviteUrl.origin;
      $("phoneInvite").hidden = false;
      $("phoneInviteManual").open = !scannable;
      text("phoneInviteAddress", inviteUrl.host);
      text("phoneInviteHelp", scannable ? (inviteUrl.hostname.endsWith(".trycloudflare.com") ?
        "Sign in with the Cloudflare email code on your phone, then scan this QR." : "Scan with your phone camera to link RakshaNetra.") :
        "Use this invite on this computer.");
      text("phoneInviteStatus", "Waiting for phone");
      text("phoneMessage", scannable ? "" :
        "This invite is for this computer. Configure a reachable HTTPS address to scan an invite from a phone.");
      updatePhoneInvite();
    } catch (error) { text("phoneMessage", error.message); }
    finally { button.disabled = !!activePhoneInvite; $("phoneWorkspaceSelect").disabled = phoneLoading || !!activePhoneInvite || sendingPhoneTest; }
  });
  $("phoneInviteCancel").addEventListener("click", async event => {
    if (!activePhoneInvite) return;
    const button = event.currentTarget;
    button.disabled = true;
    text("phoneInviteStatus", "Canceling invite…");
    try {
      const result = await phoneAdmin("/invite/cancel", {code: activePhoneInvite});
      showPhoneInviteResult(result);
      $("phoneInviteButton").focus();
    } catch (error) {
      text("phoneInviteStatus", "Checking invite…");
      text("phoneMessage", error.message);
    } finally { button.disabled = false; }
  });
  function updatePhoneInvite() {
    if (!activePhoneInvite) return;
    const seconds = Math.max(0, Math.ceil((phoneInviteExpires - Date.now()) / 1000));
    text("phoneInviteExpiry", seconds ? "Expires in " + Math.floor(seconds / 60) + ":" +
      String(seconds % 60).padStart(2, "0") : "Checking final invite status…");
    checkPhoneInvite();
  }
  $("phoneInviteCode").addEventListener("focus", event => event.currentTarget.select());
  $("phoneTestButton").addEventListener("click", async event => {
    if (sendingPhoneTest) return;
    sendingPhoneTest = true;
    $("phoneWorkspaceSelect").disabled = true;
    const button = event.currentTarget; button.disabled = true;
    try {
      const result = await phoneAdmin("/test", {});
      const count = (result.delivery.queued || 0) + (result.delivery.accepted || 0);
      text("phoneMessage", count ? "Test alert queued for " + count + (count === 1 ? " device." : " devices.") :
        "Test added to the app inbox. Enable notifications in the app to receive it in the background.");
      historyRequestedAt = -Infinity; refreshSavedIncidents();
    } catch (error) { text("phoneMessage", error.message); }
    finally {
      sendingPhoneTest = false;
      $("phoneWorkspaceSelect").disabled = phoneLoading || !!activePhoneInvite;
      button.disabled = !phoneData?.devices.some(device => device.role === "recipient");
    }
  });
  async function verifyRecipientBeforeAction(actionCallback) {
    let isSetup = false;
    try {
      if (status.incident_history && !cautionBypassedThisSession) await loadPhoneDevices();
      isSetup = cautionBypassedThisSession || !!(phoneData?.push && phoneData.devices.some(device => device.role === "recipient" && device.subscribed));
    } catch (_) {
      isSetup = false;
    }
    if (isSetup) {
      actionCallback();
      return;
    }
    pendingCautionAction = actionCallback;
    showDialog("recipientCautionDialog");
  }

  if ($("cautionSetupRecipient")) {
    $("cautionSetupRecipient").addEventListener("click", () => {
      pendingCautionAction = null;
      closeDialog("recipientCautionDialog");
      closeDialog("sourceModal");
      window.location.hash = "#recipients";
      toast("Create a phone invite below.", "info");
      setTimeout(() => {
        const createButton = $("phoneInviteButton");
          if (createButton && !createButton.disabled && createButton.offsetParent !== null) {
            createButton.focus();
        }
      }, 250);
    });
  }

  if ($("cautionSkipRecipient")) {
    $("cautionSkipRecipient").addEventListener("click", () => {
      cautionBypassedThisSession = true;
      if ($("cautionRememberChoice")?.checked) {
        try {
          sessionStorage.setItem("rakshanetra.caution_bypassed", "true");
        } catch (_) {}
      }
      const action = pendingCautionAction;
      pendingCautionAction = null;
      closeDialog("recipientCautionDialog");
      if (typeof action === "function") {
        action();
      }
    });
  }

  function reconnectFeed() {
    if (expiredSession) { window.location.reload(); return; }
    streamFailed = false;
    $("liveStream").removeAttribute("src");
    $("liveStream").src = endpoint("/stream.mjpg");
    text("feedStateTitle", "Reconnecting to your source");
    renderFeed(); pollBackend();
  }
  for (const id of ["retryStreamButton", "reconnectButton"]) $(id).addEventListener("click", reconnectFeed);
  $("liveStream").addEventListener("error", () => { streamFailed = true; renderFeed(); });
  $("liveStream").addEventListener("load", () => { streamFailed = false; renderFeed(); });

  function openSource(tab = "video") {
    if (!sourceBusy && !pendingSource) setMessage("");
    setModalTab(tab); showDialog("sourceModal");
  }
  function canAnalyze() {
    if ((status.inference_mode || capabilities?.inference_mode) === "synthetic") {
      setMessage("This server is in scripted demo mode. Restart with: python ui.py --config config/default.json --source idle --judge-mode", "error");
      toast("Real sources need model mode. See the source dialog.", "error");
      return false;
    }
    return true;
  }
  async function loadCapabilities() {
    try {
      capabilities = await readResponse(await apiFetch("/api/capabilities"));
      $("configuredCamera").replaceChildren(new Option("Enter a stream URL", ""));
      for (const stream of capabilities.streams || []) $("configuredCamera").add(new Option(stream.label, stream.id));
    } catch (_) { /* Source selection also works without a capability response. */ }
  }

  function renderNotificationPreference() {
    const supported = "Notification" in window && window.isSecureContext;
    const granted = supported && Notification.permission === "granted";
    $("notificationButton").setAttribute("aria-pressed", String(granted && notificationsEnabled));
    text("notificationLabel", granted && notificationsEnabled ? "Alerts on" : "Enable alerts");
    $("notificationButton").title = !supported ? "Browser notifications unavailable; in-app alerts remain active" : "Toggle browser incident notifications";
  }
  $("notificationButton").addEventListener("click", async () => {
    if (!("Notification" in window) || !window.isSecureContext) { toast("This browser cannot show desktop notifications. In-app alerts are active."); return; }
    if (notificationsEnabled && Notification.permission === "granted") notificationsEnabled = false;
    else {
      try { notificationsEnabled = (await Notification.requestPermission()) === "granted"; }
      catch (_) { notificationsEnabled = false; }
    }
    try { localStorage.setItem("rakshanetra.notifications", String(notificationsEnabled)); } catch (_) {}
    renderNotificationPreference();
    toast(notificationsEnabled ? "Browser incident alerts enabled." : Notification.permission === "denied" ? "Notifications blocked by browser settings. In-app alerts are still active." : "Browser alerts off. In-app alerts remain active.");
  });
  function notifyIncidents(created) {
    const active = created.filter(entry => RakshaUI.isActiveAlert(entry) && !entry.synthetic);
    if (!active.length) return;
    toast(active.length === 1 ? active[0].title + " · Open the incident inbox to review." : active.length + " new incidents · Open the inbox to review.", "incident");
    if (!notificationsEnabled || !("Notification" in window) || Notification.permission !== "granted") return;
    const entry = active[0];
    try {
      const notice = new Notification(active.length === 1 ? entry.title : active.length + " new incidents", {
        body: entry.camera + " · " + sourceTime(entry.start) + " · Review the incident before acting.",
        tag: "rakshanetra:" + entry.id
      });
      notice.onclick = () => { window.focus(); window.location.hash = "incidents"; notice.close(); };
      setTimeout(() => notice.close(), 12000);
    } catch (_) { toast("In-app incident alert received. Desktop notifications are unavailable in this browser.", "incident"); }
  }

  async function stopBrowserCamera(tellServer = true) {
    webcamAttempt++; clearTimeout(webcamTimer);
    const token = webcamToken; webcamToken = "";
    const stream = webcamStream; webcamStream = null;
    if (stream) stream.getTracks().forEach(track => track.stop());
    $("webcamPreview").srcObject = null; $("webcamPreview").hidden = true; $("stopWebcamButton").hidden = true;
    if (tellServer && token) {
      try { await apiFetch("/api/webcam/stop", {method: "POST", headers: {"Content-Type": "application/json", "X-Vigilance-Camera": token}, body: "{}"}); }
      catch (_) { toast("Camera stopped on this device; the server will time out the last frame."); }
    }
  }
  $("stopWebcamButton").addEventListener("click", async () => {
    await stopBrowserCamera(); pendingSource = null; setSourceBusy(false); pollBackend(); toast("Webcam stopped.");
  });
  async function stopMonitoringVideo() {
    if (webcamStream || webcamToken) {
      await stopBrowserCamera();
      pendingSource = null;
      setSourceBusy(false);
      pollBackend();
      toast("Webcam stopped.");
      return;
    }
    if (sourceBusy) return;
    setSourceBusy(true);
    setMessage("Stopping video monitoring…");
    try {
      const response = await apiFetch("/api/source", {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({source: "idle"})
      }, 10000);
      const data = await readResponse(response);
      sourceRequested(data, "idle");
      toast("Video monitoring stopped.");
    } catch (e) {
      setSourceBusy(false);
      setMessage(e.message, "error");
      toast("Could not stop video: " + e.message, "error");
    }
  }
  for (const id of ["stopMonitoringButton", "feedStopFloatingButton"]) {
    const el = $(id);
    if (el) el.addEventListener("click", stopMonitoringVideo);
  }
  async function sendWebcamFrame() {
    const token = webcamToken;
    if (!token || !webcamStream) return;
    try {
      const video = $("webcamPreview");
      if (!video.videoWidth) throw new Error("Webcam is not providing frames");
      const scale = Math.min(1, 960 / video.videoWidth, 540 / video.videoHeight);
      const canvas = document.createElement("canvas"); canvas.width = Math.max(1, Math.round(video.videoWidth * scale)); canvas.height = Math.max(1, Math.round(video.videoHeight * scale));
      canvas.getContext("2d").drawImage(video, 0, 0, canvas.width, canvas.height);
      const blob = await new Promise(resolve => canvas.toBlob(resolve, "image/jpeg", .8));
      if (!blob) throw new Error("Could not encode the webcam image");
      await readResponse(await apiFetch("/api/webcam/frame", {method: "POST", headers: {"Content-Type": "image/jpeg", "X-Vigilance-Camera": token}, body: blob}));
      if (token === webcamToken) webcamTimer = setTimeout(sendWebcamFrame, 100);
    } catch (error) {
      if (token !== webcamToken) return;
      await stopBrowserCamera(); pendingSource = null; setSourceBusy(false);
      setMessage(error.message, "error"); toast("Webcam stopped: " + error.message, "error");
    }
  }

  function setModalTab(tab, focus = false) {
    const tabs = {video: ["tabVideo", "uploadVideoForm"], webcam: ["tabWebcam", "webcamForm"], rtsp: ["tabRtsp", "rtspForm"]};
    for (const [kind, [button, panel]] of Object.entries(tabs)) {
      const selected = kind === tab;
      $(button).classList.toggle("active", selected); $(button).setAttribute("aria-selected", String(selected));
      $(button).tabIndex = selected ? 0 : -1; $(panel).hidden = !selected;
    }
    if (focus) $(tabs[tab][0]).focus();
  }
  document.querySelectorAll("[data-source-tab]").forEach(button => {
    button.addEventListener("click", () => setModalTab(button.dataset.sourceTab));
    button.addEventListener("keydown", event => {
      const order = ["video", "webcam", "rtsp"], index = order.indexOf(button.dataset.sourceTab);
      if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
      event.preventDefault();
      const next = event.key === "Home" ? 0 : event.key === "End" ? 2 : (index + (event.key === "ArrowRight" ? 1 : 2)) % 3;
      setModalTab(order[next], true);
    });
  });
  for (const id of ["connectButton", "changeSourceButton", "feedConnectButton"]) {
    const el = $(id);
    if (el) el.addEventListener("click", () => verifyRecipientBeforeAction(() => openSource("video")));
  }
  $("videoFileInput").addEventListener("change", () => text("uploadLabel", $("videoFileInput").files[0]?.name || "Choose a video to observe"));
  function setSourceBusy(busy) {
    sourceBusy = busy;
    document.querySelectorAll(".modal-form input,.modal-form button[type=submit]").forEach(input => { input.disabled = busy; });
  }
  function sourceRequested(data, alias) {
    if (data.source !== "browser-webcam") stopBrowserCamera(false);
    if (data.source && alias) sourceAliases.set(data.source, alias);
    pendingSource = {
      expected: data.source, previousSource: status.source, previousFrame: status.frame,
      requestedAt: Date.now(), sawStopped: false, generation: data.source_generation
    };
    setMessage("Source accepted. Waiting for the first processed frame…");
    setSourceBusy(true); $("cancelUploadButton").hidden = true; $("uploadProgress").hidden = true;
    pollBackend();
  }
  async function readResponse(response) {
    let data;
    try { data = await response.json(); } catch (_) { throw new Error("The server returned an unreadable response. Try again."); }
    if (!response.ok) throw new Error(response.status === 403 ? "Your session has expired. Reload the workspace and try again." : data.error || "This source could not be accepted.");
    return data;
  }
  async function performVideoUpload(file) {
    setSourceBusy(true);
    try { await ensureSession(); }
    catch (_) { setSourceBusy(false); setMessage("Cannot connect to the detection server. Try again.", "error"); return; }
    if (!$("sourceModal").open) { setSourceBusy(false); return; }
    const form = new FormData(); form.append("video", file, file.name);
    const request = new XMLHttpRequest(); uploadRequest = request;
    setSourceBusy(true); setMessage("Uploading video…"); $("uploadProgress").hidden = false; $("uploadProgress").value = 0;
    $("cancelUploadButton").hidden = false;
    request.open("POST", endpoint("/api/upload")); request.timeout = 95000; request.setRequestHeader("X-Vigilance-Token", sessionToken);
    request.upload.addEventListener("progress", e => {
      if (e.lengthComputable) { $("uploadProgress").value = e.loaded / e.total * 100; setMessage("Uploading video · " + Math.round(e.loaded / e.total * 100) + "%"); }
    });
    const failed = message => { uploadRequest = null; setSourceBusy(false); $("uploadProgress").hidden = true; $("cancelUploadButton").hidden = true; setMessage(message, "error"); };
    request.addEventListener("load", () => {
      uploadRequest = null;
      try {
        const data = JSON.parse(request.responseText);
        if (request.status < 200 || request.status >= 300) throw new Error(request.status === 403 ? "Session expired. Reload this workspace." : data.error || "Video upload failed.");
        sourceRequested(data, file.name);
        setLayout("focus");
        preferences.layout = "focus";
        try { localStorage.setItem("rakshanetra.preferences", JSON.stringify(preferences)); } catch (_) {}
      } catch (e) { failed(e.message || "Video upload failed. Try again."); }
    });
    request.addEventListener("error", () => failed("Upload interrupted. Check the connection and try again."));
    request.addEventListener("timeout", () => failed("Upload timed out. Try a smaller video."));
    request.addEventListener("abort", () => failed("Upload cancelled. No new source was confirmed."));
    request.send(form);
  }

  $("uploadVideoForm").addEventListener("submit", async event => {
    event.preventDefault(); if (sourceBusy) return;
    if (!canAnalyze()) return;
    const file = $("videoFileInput").files[0]; if (!file) return;
    if (file.size + 4096 > (capabilities?.max_upload_bytes || 256 * 1024 * 1024)) { setMessage("Choose a video slightly smaller than 256 MiB to leave room for upload headers.", "error"); return; }
    if (!file.size || !/\.(mp4|avi|mov|mkv|webm|mpeg|mpg|m4v|wmv)$/i.test(file.name)) {
      setMessage("Choose a supported video file: MP4, AVI, MOV, MKV, WebM or MPEG.", "error"); return;
    }
    verifyRecipientBeforeAction(() => {
      setLayout("focus");
      preferences.layout = "focus";
      try { localStorage.setItem("rakshanetra.preferences", JSON.stringify(preferences)); } catch (_) {}
      performVideoUpload(file);
    });
  });
  $("cancelUploadButton").addEventListener("click", () => uploadRequest?.abort());
  $("sourceModal").addEventListener("cancel", event => {
    if (uploadRequest) { event.preventDefault(); setMessage("Upload in progress. Cancel the upload before closing."); }
  });
  $("sourceModal").querySelector("[data-close]").addEventListener("click", () => {
    // Closing does not stop server processing; an active upload is explicitly cancelled.
    if (uploadRequest) uploadRequest.abort();
  });
  async function submitSource(source, streamId = null) {
    if (sourceBusy || !canAnalyze()) return;
    setSourceBusy(true); setMessage("Requesting source…");
    try {
      const response = await apiFetch("/api/source", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(streamId === null ? {source} : {stream_id: streamId})}, 15000);
      sourceRequested(await readResponse(response));
    } catch (e) { setSourceBusy(false); setMessage(e.name === "AbortError" ? "Source request timed out. Check camera health before trying again." : e.message, "error"); }
  }
  $("serverCameraButton").addEventListener("click", () => {
    if (!/^[0-9]$/.test($("webcamIndex").value)) { setMessage("Choose a camera index from 0 to 9.", "error"); return; }
    verifyRecipientBeforeAction(() => submitSource($("webcamIndex").value));
  });

  async function performWebcamStart() {
    await stopBrowserCamera(); const attempt = ++webcamAttempt;
    setSourceBusy(true); setMessage("Allow camera access in your browser to begin. Audio is off.");
    let stream;
    try {
      stream = await navigator.mediaDevices.getUserMedia({video: {width: {ideal: 960}, height: {ideal: 540}, frameRate: {ideal: 15}}, audio: false});
      if (attempt !== webcamAttempt) { stream.getTracks().forEach(t => t.stop()); return; }
      webcamStream = stream; const preview = $("webcamPreview"); preview.srcObject = stream; preview.hidden = false;
      await preview.play();
      const data = await readResponse(await apiFetch("/api/webcam/start", {method: "POST", headers: {"Content-Type": "application/json"}, body: "{}"}));
      if (attempt !== webcamAttempt) {
        await apiFetch("/api/webcam/stop", {method: "POST", headers: {"Content-Type": "application/json", "X-Vigilance-Camera": data.camera_token}, body: "{}"});
        return;
      }
      webcamToken = data.camera_token; $("stopWebcamButton").hidden = false;
      stream.getVideoTracks()[0].addEventListener("ended", () => { if (webcamStream === stream) $("stopWebcamButton").click(); });
      sourceRequested(data, "Your webcam"); sendWebcamFrame();
    } catch (error) {
      if (stream) stream.getTracks().forEach(t => t.stop());
      if (attempt !== webcamAttempt) return;
      await stopBrowserCamera(); setSourceBusy(false);
      const message = error.name === "NotAllowedError" ? "Camera access was denied. Allow it in browser settings, or upload a video." : error.name === "NotFoundError" ? "No camera found on this device. Upload a video instead." : error.name === "NotReadableError" ? "Camera is busy. Close other apps using it and retry." : error.message;
      setMessage(message, "error"); toast(message, "error");
    }
  }

  $("webcamForm").addEventListener("submit", async e => {
    e.preventDefault(); if (sourceBusy || !canAnalyze()) return;
    if (!navigator.mediaDevices?.getUserMedia || !window.isSecureContext) { setMessage("Webcam access requires HTTPS or localhost and a browser with camera support.", "error"); return; }
    verifyRecipientBeforeAction(() => performWebcamStart());
  });
  $("configuredCamera").addEventListener("change", () => { $("rtspUrl").disabled = Boolean($("configuredCamera").value); });
  $("rtspForm").addEventListener("submit", e => {
    e.preventDefault(); const value = $("rtspUrl").value.trim();
    if ($("configuredCamera").value) { submitSource(null, $("configuredCamera").value); return; }
    if (!/^(rtsp|rtsps|https?):\/\/[^\s]+$/i.test(value)) { setMessage("Enter a valid RTSP or HTTP stream URL.", "error"); return; }
    submitSource(value);
  });
  function checkPendingSource(s) {
    if (!pendingSource) return;
    if (!s.running) pendingSource.sawStopped = true;
    const correctGeneration = pendingSource.generation === undefined || s.source_generation === pendingSource.generation;
    const changed = pendingSource.generation !== undefined ? correctGeneration : s.source !== pendingSource.previousSource || s.frame < pendingSource.previousFrame || pendingSource.sawStopped;
    if ((s.error && correctGeneration) || Date.now() - pendingSource.requestedAt > 60000) {
      setMessage(s.error ? "Source could not start: " + s.error : "No new source was confirmed. Check camera health or try again.", "error");
      pendingSource = null; setSourceBusy(false);
    } else if (pendingSource.expected === "idle" && (s.source === "idle" || !s.running) && correctGeneration) {
      pendingSource = null; setSourceBusy(false); setMessage("Video monitoring stopped.", "neutral");
      reconnectFeed(); toast("Video monitoring stopped.");
    } else if (isCapturing(s) && s.source === pendingSource.expected && changed && Number(s.frame) >= 0) {
      pendingSource = null; setSourceBusy(false); setMessage("First frame received. Your source is connected.", "success");
      closeDialog("sourceModal"); window.location.hash = "monitoring";
      reconnectFeed(); toast("Primary camera source connected.");
      setLayout("focus");
      preferences.layout = "focus";
      try { localStorage.setItem("rakshanetra.preferences", JSON.stringify(preferences)); } catch (_) {}
    }
  }

  function entries() { return [...alertStore.values()].sort((a, b) => b.firstSeen - a.firstSeen || b.serial - a.serial); }
  function recentEntries() {
    return entries().filter(entry => !RakshaUI.isActiveAlert(entry) && !clearedRecent.has(entry.id));
  }
  let historyRequestedAt = -Infinity, historyLoading = false;
  async function refreshSavedIncidents() {
    if (!status.incident_history || historyLoading || Date.now() - historyRequestedAt < 2500) return;
    historyRequestedAt = Date.now();
    historyLoading = true;
    try {
      const {incidents} = await readResponse(await apiFetch("/api/incidents"));
      for (const record of incidents.slice(0, RakshaUI.MAX_STORED_ALERTS)) {
        const kind = RakshaUI.category(record.category), id = RakshaUI.savedKey(record.id, kind);
        let entry = alertStore.get(id);
        if (!entry) {
          entry = {id, savedId: record.id, kind, start: record.source_time, end: record.source_time,
            activeUntil: Math.min(Date.now(), record.observed_at * 1000) + RakshaUI.MIN_ACTIVE_ALERT_MS,
            active: false, uncertain: false, snapshot: null, reason: "Review the incident evidence before acting."};
          alertStore.set(id, entry);
        }
        Object.assign(entry, {
          serial: record.sequence, title: record.title, review: record.review,
          source: record.source, camera: record.camera, site: record.site, severity: record.severity, score: score(record.confidence),
          firstSeen: record.observed_at * 1000, test: !!record.test, recorded: true, clip: record.clip
        });
      }
      render();
      if ($("incidentDialog").open && alertStore.has(selectedIncident)) {
        renderIncidentClip(alertStore.get(selectedIncident));
        refreshIncidentActivity(alertStore.get(selectedIncident));
      }
    } catch (_) { /* Live monitoring remains available when history storage is unavailable. */ }
    finally { historyLoading = false; }
  }
  function orderedNodes(parent, desired) {
    const allowed = new Set(desired);
    for (const child of [...parent.children]) if (!allowed.has(child)) child.remove();
    desired.forEach((node, index) => { if (parent.children[index] !== node) parent.insertBefore(node, parent.children[index] || null); });
  }
  function field(node, name, value) {
    const el = node.querySelector('[data-field="' + name + '"]'), next = String(value ?? "");
    if (el && el.textContent !== next) el.textContent = next;
  }
  function updateCounts() {
    const all = entries(), active = all.filter(e => RakshaUI.isActiveAlert(e));
    text("topAlertCount", connected && !stale ? String(active.length).padStart(2, "0") : "—");
    text("sessionIncidentCount", String(all.length).padStart(2, "0"));
    text("inboxCount", active.length); text("activeTabCount", active.length);
    text("recentTabCount", recentEntries().length); text("navIncidentCount", all.length);
    text("reviewCount", all.filter(e => e.review !== "unreviewed").length + " reviewed");
    text("pendingReviewCount", all.filter(e => e.review === "unreviewed").length);
    text("activeStatNote", !connected || stale ? "Current detection status unknown" : active.length ? "Review the observed activity" : status.fight_status === "ASSESSED" && isCapturing(status) ? "No current incidents detected" : status.ended ? "Video processing complete" : "Assessment status shown below");
    $("exportButton").disabled = all.length === 0;
  }
  function renderAlerts() {
    const all = entries(), matching = inboxTab === "active" ? all.filter(e => RakshaUI.isActiveAlert(e)) : recentEntries();
    const visible = inboxTab === "active" ? matching : matching.slice(0, 12);
    $("clearRecentButton").hidden = inboxTab !== "recent";
    $("clearRecentButton").disabled = matching.length === 0;
    const desired = visible.map(entry => {
      let node = cardNodes.get(entry.id);
      if (!node) {
        node = document.createElement("article"); node.className = "alert-card"; node.dataset.id = entry.id;
        node.innerHTML = '<div class="alert-top"><h3 class="alert-title" data-field="title"></h3><span class="live-badge" data-field="state"></span></div>' +
          '<span class="alert-cam" data-field="camera"></span><div class="alert-confidence"><span data-field="scoreLabel"></span><strong data-field="score"></strong></div>' +
          '<div class="confidence-track"><div class="confidence-fill"></div></div><p class="alert-reasoning" data-field="reason"></p><p class="stale-note" data-field="time"></p>' +
          '<span class="review-tag" data-field="review"></span><div class="alert-actions"><button class="button primary" data-action="review">Review incident</button><button class="button secondary" data-action="acknowledge">Acknowledge</button></div>';
        cardNodes.set(entry.id, node);
      }
      node.classList.toggle("stale", !RakshaUI.isActiveAlert(entry));
      field(node, "title", entry.title); field(node, "state", entry.synthetic ? "DEMO" : entry.active ? "ACTIVE" : RakshaUI.isActiveAlert(entry) ? "NEW ALERT" : entry.uncertain ? "LAST RECEIVED" : "RECORDED");
      field(node, "camera", "CAM-01 · " + entry.camera);
      field(node, "scoreLabel", entry.kind === "fight" ? "Fight model score" : "Detection score");
      field(node, "score", entry.score === null ? "Not retained" : Math.round(entry.score * 100) + "%");
      node.querySelector(".confidence-fill").style.width = (entry.score === null ? 0 : entry.score * 100) + "%";
      field(node, "reason", entry.reason);
      field(node, "time", "Source " + sourceTime(entry.start) + (entry.end > entry.start ? " – " + sourceTime(entry.end) : "") +
        (entry.lastSeen ? " · last seen " + Math.max(0, Math.floor((Date.now() - entry.lastSeen) / 1000)) + "s ago" : ""));
      field(node, "review", entry.review === "unreviewed" ? "" : reviewLabels[entry.review]);
      const acknowledge = node.querySelector('[data-action="acknowledge"]');
      acknowledge.hidden = entry.review !== "unreviewed";
      return node;
    });
    orderedNodes($("alertsList"), desired); $("inboxEmpty").hidden = visible.length > 0;
    if (!visible.length) {
      let title, detail;
      if (inboxTab === "recent") { title = "No recent alerts"; detail = "New alerts move here after they leave Active. Saved recordings are in Incident history."; }
      else if (!connected || stale) { title = "Connection interrupted"; detail = "Current activity is unknown. Previous incidents are retained."; }
      else if (status.inference_mode === "synthetic") { title = "Scripted demonstration"; detail = "Connect in model mode to test real camera or video detection."; }
      else if (status.error) { title = "Source needs attention"; detail = "Check the source error above and reconnect."; }
      else if (status.ended) { title = "Video complete"; detail = "Open Recent to review detections."; }
      else if (status.fight_status === "ASSESSED" && isCapturing(status)) { title = "No active incidents"; detail = "Monitoring the current source."; }
      else if (!isCapturing(status)) { title = "Ready to monitor"; detail = "Connect a camera or run a test video."; }
      else { title = "Collecting observations"; detail = "Fight assessment needs visible person pairs over time."; }
      text("inboxEmptyTitle", title); text("inboxEmptyText", detail);
    }
    updateCounts();
  }
  for (const [id, tab] of [["activeTab", "active"], ["recentTab", "recent"]]) $(id).addEventListener("click", () => {
    inboxTab = tab;
    for (const [button, value] of [["activeTab", "active"], ["recentTab", "recent"]]) {
      $(button).classList.toggle("selected", value === tab); $(button).setAttribute("aria-pressed", String(value === tab));
    }
    renderAlerts();
  });
  $("clearRecentButton").addEventListener("click", () => {
    pendingRecentClear = recentEntries().map(entry => entry.id);
    if (pendingRecentClear.length) showDialog("clearRecentDialog");
  });
  $("confirmClearRecent").addEventListener("click", () => {
    for (const id of pendingRecentClear) {
      const entry = alertStore.get(id);
      if (entry && !RakshaUI.isActiveAlert(entry)) clearedRecent.add(id);
    }
    pendingRecentClear = [];
    let saved = true;
    try { localStorage.setItem(clearedRecentKey, JSON.stringify([...clearedRecent].slice(-1000))); }
    catch (_) { saved = false; }
    closeDialog("clearRecentDialog");
    renderAlerts();
    toast(saved ? "Recent alerts cleared. Recordings remain in Incident history." : "Recent alerts cleared for this visit. Browser storage is unavailable.");
  });
  $("alertsList").addEventListener("click", event => {
    const button = event.target.closest("[data-action]"); if (!button) return;
    const entry = alertStore.get(button.closest("[data-id]").dataset.id); if (!entry) return;
    if (button.dataset.action === "review") openIncident(entry.id);
    else {
      entry.review = "acknowledged";
      button.previousElementSibling.focus();
      renderAlerts(); renderHistory(); toast("Incident acknowledged. Monitoring continues.");
    }
  });

  function renderHistory() {
    if (currentView !== "incidents") return;
    const query = $("incidentSearch").value.toLowerCase().trim(), filter = $("incidentFilter").value;
    const filtered = entries().filter(e =>
      (!query || [e.title, e.camera, e.source, "RX-" + e.serial].join(" ").toLowerCase().includes(query)) &&
      (filter === "all" || (filter === "active" ? RakshaUI.isActiveAlert(e) : filter === "reviewed" ? e.review !== "unreviewed" : e.review === "unreviewed"))
    );
    const desired = filtered.map(entry => {
      let row = rowNodes.get(entry.id);
      if (!row) {
        row = document.createElement("tr"); row.dataset.id = entry.id;
        row.innerHTML = '<td><strong data-field="title"></strong><small data-field="id"></small></td><td><span data-field="camera"></span><small data-field="source"></small></td><td data-field="time"></td><td class="table-score" data-field="score"></td><td><span class="table-review" data-field="review"></span></td><td><button class="text-button" data-action="review">Review ' + icon("arrow") + '</button></td>';
        rowNodes.set(entry.id, row);
      }
      field(row, "title", entry.title); field(row, "id", "RX-" + String(entry.serial).padStart(3, "0") + (entry.active ? " · Active" : entry.synthetic ? " · Demo" : ""));
      field(row, "camera", entry.camera); field(row, "source", entry.source);
      field(row, "time", sourceTime(entry.start)); field(row, "score", entry.score === null ? "—" : Math.round(entry.score * 100) + "%");
      field(row, "review", reviewLabels[entry.review]); row.querySelector(".table-review").classList.toggle("reviewed", entry.review !== "unreviewed");
      return row;
    });
    orderedNodes($("incidentRows"), desired);
    $("historyEmpty").hidden = desired.length > 0;
    text("historyEmptyTitle", alertStore.size ? "No matching incidents" : "A clean slate");
    text("historyEmptyText", alertStore.size ? "Try another search or review filter." : "Observed incidents will appear here with their source time and review status.");
  }
  for (const id of ["incidentSearch", "incidentFilter"]) $(id).addEventListener(id === "incidentSearch" ? "input" : "change", renderHistory);
  $("incidentRows").addEventListener("click", event => {
    const button = event.target.closest('[data-action="review"]');
    if (button) openIncident(button.closest("[data-id]").dataset.id);
  });
  function renderIncidentClip(entry) {
    const video = $("incidentClip");
    const url = entry.savedId && entry.clip === "ready"
      ? endpoint("/api/incidents/" + entry.savedId + "/clip.mp4") : "";
    if (url && video.getAttribute("src") !== url) video.src = url;
    if (!url && video.hasAttribute("src")) video.removeAttribute("src");
    video.hidden = !url;
    text("incidentClipStatus", entry.clip === "processing" ? "Preparing incident clip…" :
      entry.clip === "unavailable" && !entry.test ? "Incident video is unavailable." : "");
  }
  async function refreshIncidentActivity(entry) {
    if (!entry.savedId) return;
    try {
      const data = await readResponse(await apiFetch("/api/incidents/" + entry.savedId));
      if (!$("incidentDialog").open || selectedIncident !== entry.id) return;
      const pushLabels = {queued: "queued", accepted: "accepted by the push provider", failed: "failed", expired: "subscription expired"};
      const counts = Object.entries(data.delivery || {}).map(([state, count]) => count + " " + (pushLabels[state] || state));
      text("incidentDelivery", counts.length ? "Push attempts · " + counts.join(" · ") : "");
      const labels = {opened: "Opened", escalated: "Forwarded to campus security", "responder-acknowledged": "Campus security acknowledged", ...reviewLabels};
      const actions = data.actions.map(action => {
        const row = document.createElement("li");
        row.textContent = [labels[action.action] || action.action, action.actor,
          new Date(action.created_at * 1000).toLocaleString(), action.note].filter(Boolean).join(" · ");
        return row;
      });
      $("incidentActivity").replaceChildren(...actions);
      $("incidentActivityPanel").hidden = actions.length === 0 && counts.length === 0;
    } catch (_) { /* Reviews remain available if the activity list cannot be refreshed. */ }
  }
  function openIncident(id) {
    const entry = alertStore.get(id); if (!entry) return;
    selectedIncident = id;
    text("incidentDialogId", "RX-" + String(entry.serial).padStart(3, "0") + " · " + (entry.test ? "TEST ALERT" : entry.synthetic ? "SYNTHETIC DEMO" : entry.active ? "ACTIVE DETECTION" : "RECORDED INCIDENT"));
    text("incidentDialogTitle", entry.title);
    $("incidentDetails").innerHTML = [
      ["Camera", [entry.site, entry.camera].filter(Boolean).join(" · ")],
      [entry.recorded ? "Observed at" : "Source time", entry.recorded ? new Date(entry.firstSeen).toLocaleString() : sourceTime(entry.start) + " – " + sourceTime(entry.end)],
      ["Source", entry.source], ...(entry.severity ? [["Priority", entry.severity]] : []),
      [entry.kind === "fight" ? "Fight model score" : "Detection score", entry.score === null ? "Not retained" : Math.round(entry.score * 100) + "%"]
    ].map(([label, value]) => "<div><dt>" + esc(label) + "</dt><dd>" + esc(value) + "</dd></div>").join("");
    text("incidentReason", entry.reason);
    text("reviewScope", entry.savedId ? "Saved to the shared incident record." : "Saved in this browser session.");
    $("reviewOutcome").value = entry.review;
    const snapshot = entry.savedId
      ? (entry.test ? "" : endpoint("/api/incidents/" + entry.savedId + "/thumbnail.jpg"))
      : entry.snapshot;
    $("snapshotImage").hidden = !snapshot; $("snapshotEmpty").hidden = !!snapshot;
    if (snapshot) $("snapshotImage").src = snapshot; else $("snapshotImage").removeAttribute("src");
    text("snapshotCaption", snapshot ? "Snapshot from around the time of the event." : "No event snapshot was retained.");
    renderIncidentClip(entry);
    $("incidentActivityPanel").hidden = true;
    showDialog("incidentDialog");
    refreshIncidentActivity(entry);
  }
  $("snapshotImage").addEventListener("error", () => { $("snapshotImage").hidden = true; $("snapshotEmpty").hidden = false; });
  $("incidentDialog").addEventListener("close", () => {
    $("incidentClip").removeAttribute("src");
    if (!$("incidentClip").hidden) $("incidentClip").load();
    $("incidentClip").hidden = true;
  });
  $("saveReviewButton").addEventListener("click", async () => {
    const entry = alertStore.get(selectedIncident); if (!entry) return;
    const action = $("reviewOutcome").value, button = $("saveReviewButton");
    button.disabled = true;
    try {
      if (entry.savedId) {
        await readResponse(await apiFetch("/api/incidents/" + entry.savedId + "/review", {
          method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({action})
        }));
      }
      entry.review = action;
      closeDialog("incidentDialog"); renderAlerts(); renderHistory(); toast("Assessment saved.");
    } catch (error) { toast(error.message, "error"); }
    finally { button.disabled = false; }
  });
  $("viewIncidentCamera").addEventListener("click", () => {
    closeDialog("incidentDialog"); window.location.hash = "monitoring"; setLayout("focus");
  });
  $("exportButton").addEventListener("click", () => {
    const cell = RakshaUI.csvCell;
    const rows = [["Incident", "Event", "Site", "Camera", "Source", "Observed at (UTC)", "Source seconds", "Score", "Review", "Test or synthetic"]];
    for (const e of entries()) rows.push(["RX-" + e.serial, e.title, e.site, e.camera, e.source,
      new Date(e.firstSeen).toISOString(), e.start, e.score ?? "", reviewLabels[e.review], Boolean(e.test || e.synthetic)]);
    const url = URL.createObjectURL(new Blob(["\uFEFF" + rows.map(row => row.map(cell).join(",")).join("\r\n")], {type: "text/csv;charset=utf-8"}));
    const a = document.createElement("a"); a.href = url; a.download = "rakshanetra-incidents.csv"; a.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000); toast("Incident export downloaded.");
  });
  async function captureSnapshots(newEntries) {
    const active = newEntries.filter(e => e.active && !e.savedId); if (!active.length) return;
    try {
      const response = await apiFetch("/snapshot.jpg", {}, 3000);
      if (!response.ok || !response.headers.get("Content-Type")?.startsWith("image/")) return;
      const blob = await response.blob();
      for (const entry of active) {
        if (!alertStore.has(entry.id)) continue;
        entry.snapshot = URL.createObjectURL(blob);
      }
      if ($("incidentDialog").open && active.some(e => e.id === selectedIncident)) {
        const outcome = $("reviewOutcome").value; openIncident(selectedIncident); $("reviewOutcome").value = outcome;
      }
    } catch (_) { /* A missing snapshot must not stop incident monitoring. */ }
  }

  const heatColors = ["#253b52", "#30687b", "#44a9ba", "#86eadd", "#efc58a"];
  for (let i = 0; i < 72; i++) {
    const cell = document.createElement("div"); cell.className = "heatmap-cell"; $("heatmapGrid").append(cell);
  }
  const scoreDot = document.createElementNS("http://www.w3.org/2000/svg", "circle");
  scoreDot.setAttribute("r", "3"); scoreDot.setAttribute("fill", "#58d5ec"); $("scoreChart").append(scoreDot);
  function renderAnalytics() {
    const end = Math.max(300, Number(status.ts) || 0), start = end - 300, bins = Array(72).fill(0);
    for (const event of status.timeline || []) if (Number.isFinite(event.start) && event.start >= start && event.start <= end)
      bins[Math.min(71, Math.floor((event.start - start) * 72 / 300))]++;
    [...$("heatmapGrid").children].forEach((cell, index) => {
      cell.style.backgroundColor = heatColors[Math.min(4, bins[index])];
      cell.title = sourceTime(start + index * 300 / 72) + " · " + bins[index] + " incident starts";
    });
    $("heatmapGrid").setAttribute("aria-label", bins.reduce((a, b) => a + b, 0) + " incident starts between source " + sourceTime(start) + " and " + sourceTime(end));
    text("heatmapStart", sourceTime(start)); text("heatmapEnd", sourceTime(end));
    const samples = session.samples.slice(-40), scored = samples.filter(p => p.score !== null);
    const latest = connected && !stale && isCapturing(status) ? topPairScore(status) : null;
    text("currentScore", latest === null ? "—" : latest.toFixed(2));
    let path = "", joined = false, lastPoint = null;
    const first = samples[0]?.time || 0, span = (samples[samples.length - 1]?.time || 0) - first;
    for (const point of samples) {
      if (point.score === null) { joined = false; continue; }
      const x = span > 0 ? (point.time - first) / span * 500 : 500, y = 92 - point.score * 84;
      path += (joined ? "L" : "M") + x.toFixed(1) + " " + y.toFixed(1);
      joined = true; lastPoint = {x, y};
    }
    $("trendLine").setAttribute("d", path); scoreDot.style.display = lastPoint ? "" : "none";
    if (lastPoint) { scoreDot.setAttribute("cx", lastPoint.x); scoreDot.setAttribute("cy", lastPoint.y); }
    $("chartEmpty").hidden = scored.length > 0;
    text("trendStart", samples.length ? sourceTime(first) : "Source time");
    text("trendEnd", samples.length ? sourceTime(samples[samples.length - 1].time) : "Latest observation");
    $("scoreChart").setAttribute("aria-label", scored.length ? scored.length + " observed fight scores; latest retained score " + scored[scored.length - 1].score.toFixed(2) : "No fight scores observed");
    const counts = new Map();
    for (const e of entries()) counts.set(e.title, (counts.get(e.title) || 0) + 1);
    const signature = JSON.stringify([...counts]);
    if ($("eventBreakdown").dataset.signature !== signature) {
      $("eventBreakdown").dataset.signature = signature;
      $("eventBreakdown").innerHTML = counts.size ? [...counts].map(([title, count]) =>
        '<div class="breakdown-row"><span>' + esc(title) + '</span><div class="breakdown-track"><div class="breakdown-fill" style="width:' +
        (count / Math.max(1, alertStore.size) * 100) + '%"></div></div><strong>' + count + '</strong></div>'
      ).join("") : '<p class="muted small">No incident categories observed in this browser session.</p>';
    }
  }
  function renderFeed() {
    const sourceHealthy = connected && isCapturing(status) && !stale;
    const unavailable = !sourceHealthy || streamFailed;
    // Retain a finished video's last image, explicitly labeled as complete.
    $("feedState").hidden = !unavailable || (connected && status.ended && !streamFailed);
    $("retryStreamButton").hidden = !streamFailed && connected && !stale;
    const h = health(status, connected, stale);
    text("feedStateTitle", streamFailed && sourceHealthy ? "Feed image unavailable" : h.title);
    text("feedStateDescription", streamFailed && sourceHealthy ? "Status is connected, but the video stream could not be loaded." : h.detail);
    text("cameraStateText", sourceHealthy && streamFailed ? "Feed unavailable" : h.label);
    $("primaryCameraState").dataset.tone = sourceHealthy && streamFailed ? "warning" : h.tone;
    text("sourceName", displaySource()); text("sourceClock", sourceTime(status.ts));
    text("feedMode", !connected ? "LAST RECEIVED" : status.ended ? "VIDEO COMPLETE" : stale ? "LAST RECEIVED" : sourceMode(status).toUpperCase());
    const hasActiveVideo = (connected && !stale && isCapturing(status)) || Boolean(webcamStream) || (connected && status.running && status.source && status.source !== "idle");
    if ($("stopMonitoringButton")) $("stopMonitoringButton").hidden = !hasActiveVideo;
    if ($("feedStopFloatingButton")) $("feedStopFloatingButton").hidden = !hasActiveVideo;
  }
  function renderHealth() {
    const h = health(status, connected, stale);
    const capturing = connected && !stale && isCapturing(status);
    text("camCount", connected ? capturing ? "01" : "00" : "—");
    $("cameraStatDot").style.background = capturing ? "var(--mint)" : "var(--subtle)";
    text("cameraStatNote", capturing ? "Primary source connected" : connected && status.ended ? "Recorded source complete" : connected ? "No current capture" : "Connection unavailable");
    $("navLiveDot").style.background = capturing ? "var(--mint)" : "var(--subtle)";
    text("sourceType", sourceMode(status));
    const fps = capturing && Number.isFinite(status.fps) ? status.fps : null;
    text("fpsValue", fps === null ? "—" : fps.toFixed(1));
    text("processingNote", fps > 0 ? (1000 / fps).toFixed(0) + " ms processing interval" : "No current processing data");
    text("healthSourceName", displaySource()); text("healthSourceStatus", h.label);
    text("healthCapture", capturing ? "Frames received" : h.label);
    const assessmentLabels = {ASSESSED: "Assessed", WARMING_UP: "Observing person pairs", UNAVAILABLE: "Unavailable", ERROR: "Error", DISABLED: "Disabled"};
    text("healthAssessment", !connected || stale ? "Current status unknown" : status.inference_mode === "synthetic" ? status.model?.head_trained ? "Model on scripted skeletons" : "Scripted · no trained model" : assessmentLabels[status.fight_status] || (status.model?.head_trained ? "Ready · awaiting source" : "Unavailable"));
    text("modelSummary", !connected ? "Detection server disconnected" : status.model?.head_trained ? "Fight model ready · allow 4 seconds of visible person-pair activity" : "Fight model unavailable · check camera health");
    text("sourceAvailability", status.inference_mode === "synthetic" ? "Scripted mode: restart the server in model mode to use real sources." : "");
    text("webcamSupport", navigator.mediaDevices?.getUserMedia && window.isSecureContext ? "Camera access is requested only when you press Open webcam." : "This browser needs HTTPS or localhost for webcam access. You can still upload a video.");
    const notice = !connected ? "Detection server disconnected. Reconnecting…" : status.inference_mode === "synthetic" ? "Scripted demo mode. For real video, start: python ui.py --config config/default.json --source idle --judge-mode" : status.error ? "Source error: " + status.error : stale ? "No recent frames. Current detection status is unknown." : Object.keys(status.scenario_errors || {}).length ? "Some detection models need attention. Open Camera health or Diagnostics." : "";
    text("serviceNotice", notice);
    $("serviceNotice").hidden = !notice; $("serviceNotice").dataset.tone = connected && !status.error ? "warning" : "error";
    if (notice && notice !== previousNotice && lastUpdate) toast(notice, status.error || !connected ? "error" : "info");
    previousNotice = notice;
    renderNotificationPreference();
    text("healthPeople", capturing ? (status.people || []).length : "—");
    text("healthFps", fps === null ? "—" : fps.toFixed(1) + " frames/sec");
    text("techMeta", JSON.stringify({source: status.source || null, frame: status.frame, source_time: status.ts, running: status.running, inference_mode: status.inference_mode, connected, stale}, null, 2));
    text("techModels", JSON.stringify({fight: status.model || {}, assessment: status.fight_status, optional: status.threat_models || {}, scenario_errors: status.scenario_errors || {}}, null, 2));
    text("techPairs", JSON.stringify({people: status.people || [], pairs: status.pairs || []}, null, 2));
    text("techDiag", JSON.stringify(status.diagnostics || {}, null, 2));
    const overlay = capturing ? RakshaUI.currentDetections(status).map(d => d.title).join(" · ") : "";
    if ($("camOverlayAlerts").textContent !== overlay) {
      $("camOverlayAlerts").replaceChildren();
      if (overlay) { const tag = document.createElement("span"); tag.className = "cam-alert-tag"; tag.textContent = overlay; $("camOverlayAlerts").append(tag); }
    }
    renderFeed(); updateFreshness();
  }
  function updateFreshness() {
    const age = lastUpdate ? Math.max(0, Math.floor((Date.now() - lastUpdate) / 1000)) : null;
    const label = age === null ? "Awaiting connection" : connected ? "Status updated " + (age < 2 ? "just now" : age + "s ago") : "Disconnected · last status " + age + "s ago";
    text("freshnessLabel", label); text("lastUpdate", age === null ? "Not received" : age < 2 ? "Just now" : age + " seconds ago");
  }
  function render() { renderHealth(); renderAlerts(); renderHistory(); renderAnalytics(); }
  async function pollBackend() {
    if (polling) return;
    polling = true; clearTimeout(pollTimer);
    try {
      const response = await apiFetch("/api/status");
      if (!response.ok) { expiredSession = response.status === 403; throw new Error("Status request failed"); }
      const next = await response.json();
      if (!next || typeof next !== "object" || Array.isArray(next) || typeof next.running !== "boolean") throw new Error("Invalid monitoring status");
      const identity = String(next.source) + ":" + next.frame + ":" + next.ts;
      if (identity !== lastFrameIdentity) { lastFrameIdentity = identity; lastFrameAt = Date.now(); }
      lastUpdate = Date.now(); stale = isCapturing(next) && Date.now() - lastFrameAt > 6500;
      connected = true; expiredSession = false; status = next;
      if (currentView === "recipients") {
        loadPhoneDevices();
      }
      const result = session.consume(status, Date.now(), !stale, preferences.camera, displaySource());
      for (const entry of result.evicted) {
        if (entry.snapshot) URL.revokeObjectURL(entry.snapshot);
        cardNodes.delete(entry.id); rowNodes.delete(entry.id);
      }
      checkPendingSource(status); render(); refreshSavedIncidents(); captureSnapshots(result.created.filter(entry => entry.active)); notifyIncidents(result.created);
    } catch (_) {
      connected = false; session.disconnect(); render();
      if (expiredSession) {
        if (activePhoneInvite) {
          clearPhoneInvite();
          text("phoneMessage", "Reload this dashboard before generating another QR.");
        }
        text("feedStateTitle", "Session expired"); text("feedStateDescription", "Reload the workspace to establish a new session.");
        text("retryStreamButton", "Reload workspace"); $("retryStreamButton").hidden = false;
      }
      if (pendingSource && Date.now() - pendingSource.requestedAt > 30000) {
        pendingSource = null; setSourceBusy(false); setMessage("Connection interrupted before the source was confirmed. Check camera health.", "error");
      }
    } finally {
      polling = false; pollTimer = setTimeout(pollBackend, connected ? 650 : 2000);
    }
  }
  window.addEventListener("online", pollBackend);
  document.addEventListener("visibilitychange", () => { if (!document.hidden) pollBackend(); });
  window.addEventListener("pagehide", () => {
    stopBrowserCamera(false);
    for (const entry of alertStore.values()) if (entry.snapshot) URL.revokeObjectURL(entry.snapshot);
  });
  applyPreferences(); navigate(); pollBackend(); loadCapabilities();
  setInterval(() => {
    updateClock(); updateFreshness();
    updatePhoneInvite();
    renderAlerts();
  }, 1000);
})();
