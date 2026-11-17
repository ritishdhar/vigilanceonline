"use strict";

const byId = id => document.getElementById(id);
const write = (id, text) => { byId(id).textContent = text; };
const api = "/api/recipient";
const root = "/recipient/";
const evidenceView = new URLSearchParams(location.search).get("evidence") === "1";
const sessionRoot = root + (evidenceView ? "?evidence=1" : "");
const idPattern = /^[0-9a-f]{32}$/;
const invitePattern = /^[A-Za-z0-9_-]{43}$/;
const stillLabels = new Map([
  ["before.jpg", "Before alert"], ["thumbnail.jpg", "At alert"], ["after.jpg", "After alert"]
]);
const reviews = {
  unreviewed: "Needs review", acknowledged: "Acknowledged",
  resolved: "Resolved", "false-positive": "False positive",
  opened: "Opened", escalated: "Forwarded to campus security",
  "responder-acknowledged": "Campus responder acknowledged"
};
let device = null;
let pushKey = null;
let worker = null;
let registeringWorker = "serviceWorker" in navigator;
let current = null;
let refreshing = false;
let refreshAgain = false;
let installPrompt = null;
const standaloneMode = window.matchMedia("(display-mode: standalone)");
let appUnlocked = false;
let linkingInvite = null;
let seenAlerts = null;
let firstInbox = true;
let pendingAlerts = [];
let alertPreview = null;
let clearedThrough = 0;
let inboxSequence = 0;
let clearTarget = null;
let clearingInbox = false;
let inboxVersion = 0;
let alarmMuted = false;
let incomingError = "";
const alarm = new RecipientAlarm();
let subscriptionSync = null;
let pushSetupError = "";

function setConnectionState(state) {
  if (!device) return;
  byId("connectionCard").dataset.state = state;
  write("connectionState", {connected: "Connected", reconnecting: "Reconnecting", offline: "Offline"}[state]);
}

function rememberAlerts() {
  if (!device || !seenAlerts) return;
  seenAlerts = new Set([...seenAlerts].slice(-400));
  try { localStorage.setItem("rakshanetra.seen." + device.id, JSON.stringify([...seenAlerts])); } catch (_) {}
}

function renderIncomingAlert() {
  const dialog = byId("incomingAlert"), incident = pendingAlerts[0] || alertPreview;
  if (!incident) {
    alarm.stop();
    dialog.hidden = true;
    if (dialog.open) dialog.close();
    return;
  }
  write("incomingAlertTitle", incident.title);
  dialog.dataset.priority = incident.test ? "test" : incident.severity === "High" ? "high" : "review";
  write("incomingAlertCount", incident.preview ? "Alert preview" : pendingAlerts.length > 1 ? pendingAlerts.length + " alerts awaiting attention" : "Incoming incident");
  write("incomingAlertBody", incident.preview ? "This is a sample alert. No incident has been created." : incident.test ? "A test alert was sent to this device." : "Review the incident and confirm your response.");
  write("incomingAlertLocation", incident.preview ? "Visible only on this device" : [incident.source === "Detection notification" ? "" : incident.camera,
    incident.observed_at ? new Date(incident.observed_at * 1000).toLocaleTimeString([], {hour: "2-digit", minute: "2-digit"}) : ""
  ].filter(Boolean).join(" · "));
  write("incomingAlarmStatus", incomingError || (alarmMuted ? "Alert silenced." : ""));
  byId("incomingAlertAcknowledge").hidden = !!incident.preview;
  byId("incomingAlertOpen").hidden = !!incident.preview;
  if (!incident.preview) byId("incomingAlertOpen").href = "#incident=" + incident.id;
  else byId("incomingAlertOpen").removeAttribute("href");
  byId("incomingAlertSilence").disabled = alarmMuted;
  dialog.hidden = false;
  if (!dialog.open) dialog.showModal();
  if (!alarmMuted) alarm.start();
}

function removeIncomingAlert(id) {
  incomingError = "";
  alertPreview = null;
  pendingAlerts = pendingAlerts.filter(incident => incident.id !== id);
  renderIncomingAlert();
}

function announceAlert(incident) {
  if (!device || !appUnlocked || !idPattern.test(incident.id)) return;
  if (incident.inbox_cleared || (device.role === "recipient" && incident.sequence <= clearedThrough)) return;
  if (seenAlerts?.has(incident.id)) return;
  seenAlerts?.add(incident.id);
  rememberAlerts();
  pendingAlerts.push(incident);
  alertPreview = null;
  incomingError = "";
  alarmMuted = false;
  renderIncomingAlert();
}
byId("incomingAlertClose").addEventListener("click", () => { removeIncomingAlert(pendingAlerts[0]?.id); });
byId("incomingAlertOpen").addEventListener("click", () => { removeIncomingAlert(pendingAlerts[0]?.id); });
byId("incomingAlertSilence").addEventListener("click", () => {
  alarmMuted = true;
  alarm.stop();
  renderIncomingAlert();
});
byId("incomingAlert").addEventListener("cancel", event => {
  event.preventDefault();
  removeIncomingAlert(pendingAlerts[0]?.id);
});
byId("incomingAlertAcknowledge").addEventListener("click", async event => {
  const incident = pendingAlerts[0];
  if (!incident) return;
  const button = event.currentTarget;
  button.disabled = true;
  incomingError = "";
  const previouslyMuted = alarmMuted;
  alarm.stop();
  alarmMuted = true;
  try {
    await request("/incidents/" + incident.id + "/review", {action: "acknowledged"});
    if (pendingAlerts[0]?.id === incident.id) alarmMuted = previouslyMuted;
    removeIncomingAlert(incident.id);
    await refresh();
  } catch (error) {
    incomingError = error.message;
    renderIncomingAlert();
  }
  finally { button.disabled = false; }
});
function message(text, error = false) {
  write("message", text);
  byId("message").dataset.error = String(error);
}

async function request(path, body) {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 8000);
  try {
    const response = await fetch(api + path, {
      credentials: "same-origin", cache: "no-store", signal: controller.signal,
      ...(body === undefined ? {} : {
        method: "POST",
        headers: {"Content-Type": "application/json", "X-Raksha-Recipient": "1"},
        body: JSON.stringify(body)
      })
    });
    const isJson = response.headers.get("Content-Type")?.includes("application/json");
    if (!isJson) throw new Error("Could not reach the workspace. Check the connection and try again.");
    const data = await response.json();
    if (!response.ok) {
      if (response.status === 401 && device) {
        device = null;
        clearPrivateView();
        message("Device disconnected. Scan a new QR to reconnect.", true);
      }
      const error = new Error(data.error || "The request could not be completed.");
      error.status = response.status;
      throw error;
    }
    return data;
  } catch (error) {
    if (error.name === "AbortError" || error instanceof TypeError) {
      throw new Error("Could not reach the workspace. Check the connection and try again.");
    }
    throw error;
  } finally { clearTimeout(timeout); }
}

function clearPrivateView() {
  alarm.stop();
  pendingAlerts = [];
  alertPreview = null;
  clearedThrough = 0;
  inboxSequence = 0;
  clearTarget = null;
  firstInbox = true;
  seenAlerts = null;
  byId("incomingAlert").hidden = true;
  clearIncident();
  byId("inbox").hidden = true;
  byId("connectionCard").hidden = true;
  write("connectionWorkspace", "");
  write("connectionDevice", "");
  byId("confirmDisconnect").close("cancel");
  byId("confirmClearAlerts").close("cancel");
  byId("enrollment").hidden = false;
  byId("incidents").replaceChildren();
  delete byId("incidents").dataset.signature;
}

function params() { return new URLSearchParams(location.hash.slice(1)); }
function inviteCode(value) {
  const trimmed = value.trim();
  if (invitePattern.test(trimmed)) return trimmed;
  try {
    const url = new URL(trimmed);
    const token = new URLSearchParams(url.hash.slice(1)).get("invite") || "";
    return url.origin === location.origin && [root, root.slice(0, -1)].includes(url.pathname)
      && invitePattern.test(token) ? token : "";
  }
  catch (_) { return ""; }
}

async function checkScannedInvite() {
  const input = byId("invite");
  const code = inviteCode(input.value);
  byId("copyInvite").hidden = !code;
  write("inviteStatus", code ? "Checking invite…" : "");
  if (!code) return false;
  try {
    const {expires} = await request("/invite/status", {invite: code});
    if (inviteCode(input.value) !== code) return false;
    write("inviteStatus", "Invite valid until " + new Date(expires * 1000).toLocaleTimeString() + ".");
    return true;
  } catch (error) {
    if (inviteCode(input.value) === code) {
      byId("copyInvite").hidden = true;
      write("inviteStatus", error.message);
    }
    return false;
  }
}

function loadInviteFromLink() {
  const scanned = params().get("invite");
  if (scanned !== null) byId("invite").value = scanned;
  checkScannedInvite();
}

function enrolledDeviceName() {
  const agent = navigator.userAgent;
  if (/iPad/i.test(agent)) return "iPad";
  if (/iPhone/i.test(agent)) return "iPhone";
  if (/Android/i.test(agent)) return "Android phone";
  return "Recipient device";
}

function relativeTime(unix) { return new Date(unix * 1000).toLocaleString(); }
function makeElement(tag, text, className) {
  const element = document.createElement(tag);
  if (text) element.textContent = text;
  if (className) element.className = className;
  return element;
}

function renderInbox(incidents) {
  const list = byId("incidents");
  inboxSequence = Math.max(clearedThrough, ...incidents.map(incident => incident.sequence));
  byId("clearAlerts").disabled = clearingInbox || incidents.length === 0;
  const signature = JSON.stringify(incidents);
  if (list.dataset.signature === signature) return;
  list.dataset.signature = signature;
  const awaiting = incidents.filter(incident => incident.review === "unreviewed").length;
  write("inboxSummary", incidents.length ? awaiting + " awaiting review · " + incidents.length + " recent" : "Your alerts will appear here.");
  byId("empty").hidden = incidents.length > 0;
  list.replaceChildren(...incidents.map(incident => {
    const row = makeElement("li");
    const link = makeElement("a", null, "incident-link");
    link.href = "#incident=" + incident.id;
    const dot = makeElement("span", null, "incident-dot" + (incident.severity === "High" ? " high" : ""));
    const copy = makeElement("span", null, "incident-copy");
    const arrow = makeElement("span", "›", "incident-arrow");
    dot.setAttribute("aria-hidden", "true");
    arrow.setAttribute("aria-hidden", "true");
    const videoState = incident.clip === "ready" ? "Video ready" : incident.clip === "processing" ? "Video processing" : "";
    copy.append(
      makeElement("strong", incident.title),
      makeElement("span", (incident.source === "Detection notification" ? ["Detection notice"] :
        [incident.site, incident.camera]).concat(relativeTime(incident.observed_at)).join(" · "), "incident-meta"),
      makeElement("span", [reviews[incident.review], videoState, incident.escalated ? "Forwarded" : ""].filter(Boolean).join(" · "), "incident-state")
    );
    link.append(dot, copy, arrow);
    row.append(link);
    return row;
  }));
}

function setClip(source) {
  const clip = byId("clip");
  if (source) {
    if (clip.getAttribute("src") !== source) clip.src = source;
  } else if (clip.hasAttribute("src")) {
    clip.removeAttribute("src");
    clip.load();
  }
  clip.hidden = !source;
}

function renderStills(media, names = []) {
  const stills = byId("stills");
  const shots = names.filter(name => stillLabels.has(name));
  const signature = media + " " + shots.join(" ");
  if (stills.dataset.shots === signature) return;
  stills.dataset.shots = signature;
  byId("stillsPlaceholder").hidden = shots.length > 0;
  stills.replaceChildren(...shots.map(name => {
    const label = stillLabels.get(name), url = media + "/" + name;
    const button = makeElement("button", null, "incident-still");
    const photo = makeElement("img");
    button.type = "button";
    button.setAttribute("aria-label", "View photo " + label.toLowerCase());
    photo.alt = "";
    photo.addEventListener("error", () => {
      button.hidden = true;
      if (stills.dataset.shots === signature && !stills.querySelector("button:not([hidden])")) {
        byId("stillsPlaceholder").hidden = false;
      }
    });
    photo.src = url;
    button.append(photo, makeElement("span", label));
    button.addEventListener("click", () => {
      write("stillHeading", label);
      const enlarged = byId("enlargedStill");
      enlarged.alt = "Camera still " + label.toLowerCase();
      enlarged.src = url;
      byId("stillDialog").showModal();
    });
    return button;
  }));
}

function clearIncident() {
  current = null;
  byId("detail").hidden = true;
  byId("confirmHandoff").close("cancel");
  byId("stillDialog").close();
  byId("enlargedStill").removeAttribute("src");
  setClip("");
  renderStills("");
  for (const id of ["activity", "incidentFacts"]) byId(id).replaceChildren();
  byId("note").value = "";
}

function renderIncident(incident) {
  const changed = current?.id !== incident.id;
  const notice = incident.source === "Detection notification";
  const inlineEvidence = !notice || incident.inline_evidence === true;
  current = incident;
  byId("inbox").hidden = true;
  byId("detail").hidden = false;
  if (changed) byId("note").value = "";
  write("incidentLabel", incident.test ? "TEST ALERT" : "INCIDENT RX-" + String(incident.sequence).padStart(3, "0"));
  write("incidentTitle", incident.title);
  write("incidentReviewStatus", reviews[incident.review]);
  byId("incidentReviewStatus").dataset.status = incident.review;
  for (const button of byId("reviews").querySelectorAll("[data-review]")) {
    button.setAttribute("aria-pressed", String(button.dataset.review === incident.review));
  }
  byId("incidentFacts").replaceChildren(...[
    ...(notice ? [] : [["Location", incident.site], ["Camera", incident.camera]]),
    ["Observed", relativeTime(incident.observed_at)], ["Priority", incident.severity],
    ...(incident.confidence == null ? [] : [["Detection score", Math.round(incident.confidence * 100) + "%"]])
  ].map(([name, value]) => {
    const field = document.createElement("div");
    field.append(makeElement("dt", name), makeElement("dd", value));
    return field;
  }));
  const media = api + "/incidents/" + incident.id;
  const status = notice ? incident.evidence_status : incident.clip;
  write("evidenceHeading", notice ? "Video & photos" : "Incident video");
  write("evidenceState", incident.test ? "Delivery test" : status === "ready" ? "Clip ready" :
    status === "processing" ? "Recording" : incident.protected_evidence ? "Secure review" : "Unavailable");
  byId("openProtectedEvidence").hidden = !incident.protected_evidence;
  byId("protectedEvidenceStatus").hidden = !incident.protected_evidence;
  write("protectedEvidenceStatus", (status === "processing" ? "The clip is being recorded. " :
    status === "ready" ? "Your recorded clip is ready. " : "") +
    (incident.photo_count ? incident.photo_count + " incident photos available. " : "") +
    "Open secure review and sign in with your approved email.");
  setClip(incident.clip === "ready" ? media + "/clip.mp4" : "");
  byId("videoPlaceholder").hidden = !!incident.protected_evidence || (incident.clip === "ready" && !byId("clip").error);
  byId("retryClip").hidden = incident.clip !== "ready" || !byId("clip").error;
  const stills = incident.stills ?? (incident.test ? [] : ["thumbnail.jpg"]);
  renderStills(media, stills);
  byId("stillsPanel").hidden = incident.test || !inlineEvidence;
  write("stillsStatus", incident.clip === "processing" ? "Photos will appear when they’re ready." : "No photos available for this incident.");
  write("clipStatus", !inlineEvidence ? "Camera evidence is unavailable for this incident." :
    incident.test ? "Delivery test. This incident has no camera footage." :
    incident.clip === "processing" ? "The video will appear here when processing finishes." :
    incident.clip === "unavailable" ? "An incident clip is unavailable." :
    byId("clip").error ? "The video could not be played." : "");
  const forwarded = incident.actions.some(action => action.action === "escalated");
  const acknowledged = incident.actions.some(action => action.action === "responder-acknowledged");
  byId("forward").hidden = device.role !== "recipient" || forwarded;
  byId("forward").disabled = incident.review !== "acknowledged" || incident.handoff_available === false;
  write("handoffStatus", acknowledged ? "Campus security acknowledged this incident." :
    forwarded ? "Forwarded to campus security. Awaiting responder acknowledgement." :
    device.role !== "recipient" ? "" :
    incident.handoff_available === false ? "Campus security forwarding needs a linked responder device." :
    incident.review !== "acknowledged" ? "Acknowledge this incident to enable forwarding." : "");
  byId("activity").replaceChildren(...incident.actions.map(action => {
    const row = makeElement("li");
    row.append(makeElement("strong", reviews[action.action] || action.action),
      makeElement("span", action.actor + " · " + relativeTime(action.created_at)));
    if (action.note) row.append(makeElement("span", action.note));
    return row;
  }));
  if (changed) {
    byId("incidentTitle").focus({preventScroll: true});
    request("/incidents/" + incident.id + "/opened", {}).catch(() => {});
  }
}

async function refresh() {
  if (!device || document.hidden) return;
  if (refreshing) { refreshAgain = true; return; }
  const session = device;
  const id = params().get("incident") || "";
  const detailRequested = idPattern.test(id);
  if (current && current.id !== id) clearIncident();
  refreshing = true;
  const version = inboxVersion;
  try {
    const response = await request("/incidents");
    if (session !== device) return;
    if (version !== inboxVersion) { refreshAgain = true; return; }
    const incidents = response.incidents.filter(incident => device.role !== "recipient" || incident.sequence > clearedThrough);
    setConnectionState("connected");
    const active = new Set(incidents.filter(incident => incident.review === "unreviewed").map(incident => incident.id));
    pendingAlerts = pendingAlerts.filter(incident => active.has(incident.id));
    for (const incident of [...incidents].reverse()) {
      if (incident.review !== "unreviewed" || (firstInbox && incident.observed_at < Date.now() / 1000 - 60)) {
        seenAlerts.add(incident.id);
      } else {
        announceAlert(incident);
      }
    }
    firstInbox = false;
    rememberAlerts();
    renderIncomingAlert();
    if (detailRequested) {
      const incident = await request("/incidents/" + id);
      if (session === device && params().get("incident") === id) renderIncident(incident);
    } else {
      if (session !== device || (params().get("incident") || "") !== id) return;
      byId("inbox").hidden = false;
      renderInbox(incidents);
    }
    message("");
  } catch (error) {
    if (session !== device) return;
    setConnectionState(navigator.onLine === false ? "offline" : "reconnecting");
    if (detailRequested && error.status === 404 && params().get("incident") === id) {
      clearIncident();
      history.replaceState(null, "", root);
      byId("inbox").hidden = false;
    }
    message(error.message || "Cannot reach the incident service.", true);
  }
  finally {
    refreshing = false;
    if (refreshAgain) {
      refreshAgain = false;
      refresh();
    }
  }
}

async function loadSession() {
  const data = await request("/session");
  const oldDevice = device;
  device = data.device;
  clearedThrough = Number.isSafeInteger(data.cleared_through) ? data.cleared_through : 0;
  pushKey = data.push_key;
  const number = data.emergency_number;
  const emergency = byId("emergency");
  emergency.hidden = !number;
  if (number) { emergency.href = "tel:" + number; emergency.textContent = "Call " + number; }
  if (!device) {
    clearPrivateView();
    message("");
    loadInviteFromLink();
    return;
  }
  if (!seenAlerts || oldDevice?.id !== device.id) {
    let stored = [];
    try { stored = JSON.parse(localStorage.getItem("rakshanetra.seen." + device.id) || "[]"); } catch (_) {}
    seenAlerts = new Set(Array.isArray(stored) ? stored.filter(id => typeof id === "string" && idPattern.test(id)) : []);
    firstInbox = true;
  }
  if (params().has("invite")) history.replaceState(null, "", root);
  byId("enrollment").hidden = true;
  byId("connectionCard").hidden = false;
  write("connectionWorkspace", data.workspace_name || "RakshaNetra workspace");
  write("connectionDevice", device.name);
  setConnectionState("connected");
  write("inboxTitle", device.role === "responder" ? "Forwarded incidents" : "Incident inbox");
  reportPushError();
  reconcileSubscription();
  await refresh();
}

function matchesPushKey(subscription) {
  const stored = subscription?.options?.applicationServerKey;
  if (!stored || !pushKey) return false;
  const expected = applicationKey(pushKey), actual = new Uint8Array(stored);
  return expected.length === actual.length && expected.every((byte, index) => byte === actual[index]);
}

async function reconcileSubscription() {
  if (!device || !worker || !pushKey || subscriptionSync || !("PushManager" in window)) return;
  const session = device;
  subscriptionSync = (async () => {
    const subscription = await worker.pushManager.getSubscription();
    if (device !== session) return;
    const valid = window.Notification?.permission === "granted" && matchesPushKey(subscription);
    if (valid) await request("/subscribe", subscription.toJSON());
    else if (device.subscribed) await request("/unsubscribe", {});
    if (device === session) { device.subscribed = valid; pushSetupError = ""; }
  })().catch(error => { if (device === session) pushSetupError = error.message; })
    .finally(() => { subscriptionSync = null; reportPushError(); });
  reportPushError();
  await subscriptionSync;
}

function reportPushError() {
  if (device && pushSetupError) message(pushSetupError, true);
}

let preparingNotifications = false;
function prepareAlertsFromGesture() {
  if (!device || !appUnlocked) return;
  if (!alarm.armed || (alarm.audio && alarm.audio.state !== "running")) {
    alarm.arm().then(renderIncomingAlert).catch(() => {});
  }
  if (evidenceView) return;
  if (!worker || !pushKey || !("Notification" in window) || !("PushManager" in window) ||
      preparingNotifications || window.Notification.permission === "denied") return;
  if (device.subscribed && window.Notification.permission === "granted") return;
  const session = device;
  preparingNotifications = true;
  const permission = Notification.permission === "granted" ?
    Promise.resolve("granted") : Notification.requestPermission();
  permission.then(async result => {
    if (result !== "granted" || device !== session) return;
    const ready = await navigator.serviceWorker.ready;
    let subscription = await ready.pushManager.getSubscription();
    if (subscription && !matchesPushKey(subscription)) {
      await subscription.unsubscribe();
      subscription = null;
    }
    if (!subscription) subscription = await ready.pushManager.subscribe({
      userVisibleOnly: true, applicationServerKey: applicationKey(pushKey)
    });
    if (device !== session) return;
    await request("/subscribe", subscription.toJSON());
    if (device === session) { device.subscribed = true; pushSetupError = ""; }
  }).catch(error => { pushSetupError = error.message; reportPushError(); })
    .finally(() => { preparingNotifications = false; });
}
document.addEventListener("click", prepareAlertsFromGesture, {capture: true});

byId("invite").addEventListener("change", checkScannedInvite);
byId("copyInvite").addEventListener("click", async () => {
  const input = byId("invite");
  const code = inviteCode(input.value);
  if (!code) return;
  try {
    await navigator.clipboard.writeText(code);
    message("Invite code copied. Paste it in the installed app if prompted.");
  } catch (_) {
    input.focus();
    input.select();
    message("Use your phone’s Copy action on the selected invite.");
  }
});

byId("enrollForm").addEventListener("submit", async event => {
  event.preventDefault();
  const button = event.currentTarget.querySelector('button[type="submit"]');
  button.disabled = true;
  try {
    const invite = inviteCode(byId("invite").value);
    if (!invite) throw new Error("Scan or enter an invite for this workspace.");
    await request("/enroll", {name: enrolledDeviceName(), invite});
    const incident = params().get("incident") || "";
    history.replaceState(null, "", sessionRoot + (idPattern.test(incident) ? "#incident=" + incident : ""));
    byId("enrollForm").reset();
    write("inviteStatus", "");
    await loadSession();
  } catch (error) { message(error.message, true); }
  finally { button.disabled = false; }
});

function linkScannedInvite() {
  const invite = params().get("invite") || "";
  if (!invitePattern.test(invite)) return Promise.resolve();
  if (linkingInvite) return linkingInvite;
  linkingInvite = (async () => {
    byId("retryInvite").hidden = true;
    const report = (status, error = false) => {
      if (appUnlocked) message(status, error);
      else write("installStatus", status);
    };
    report("Linking your phone…");
    try {
      await request("/enroll", {name: enrolledDeviceName(), invite});
      const incident = params().get("incident") || "";
      history.replaceState(null, "", sessionRoot + (idPattern.test(incident) ? "#incident=" + incident : ""));
      if (!appUnlocked) report("Phone linked. Install RakshaNetra, then open it from your home screen.");
    } catch (error) {
      report(error.message, true);
      byId("retryInvite").hidden = false;
    }
  })().finally(() => { linkingInvite = null; });
  return linkingInvite;
}
byId("refresh").addEventListener("click", async event => {
  const button = event.currentTarget;
  if (refreshing) return;
  button.querySelector("svg").getAnimations?.().forEach(animation => animation.cancel());
  if (!window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
    button.querySelector("svg").animate?.([{transform: "rotate(0deg)"}, {transform: "rotate(360deg)"}],
      {duration: 550, easing: "ease-in-out"});
  }
  button.disabled = true;
  try { await refresh(); }
  finally { button.disabled = false; }
});
byId("previewAlert").addEventListener("click", () => {
  if (!device || pendingAlerts.length) return;
  alertPreview = {preview: true, test: true, title: "Sample incident alert"};
  incomingError = "";
  alarmMuted = false;
  renderIncomingAlert();
});
byId("clearAlerts").addEventListener("click", () => {
  if (!device || clearingInbox || !byId("incidents").childElementCount) return;
  clearTarget = {deviceId: device.id, through: inboxSequence};
  byId("confirmClearAlerts").returnValue = "cancel";
  byId("confirmClearAlerts").showModal();
});
byId("confirmClearAlerts").addEventListener("close", async event => {
  const target = clearTarget;
  clearTarget = null;
  if (event.target.returnValue !== "clear" || !target || !device || target.deviceId !== device.id) return;
  clearingInbox = true;
  byId("clearAlerts").disabled = true;
  byId("clearAlerts").setAttribute("aria-busy", "true");
  try {
    const result = await request("/inbox/clear", {through: target.through});
    if (!device || device.id !== target.deviceId) return;
    inboxVersion++;
    clearedThrough = Math.max(clearedThrough, result.cleared_through);
    pendingAlerts = pendingAlerts.filter(incident => incident.sequence > clearedThrough);
    renderIncomingAlert();
    if (!current) renderInbox([]);
    // Close notifications belonging to alerts that were visible when clear was tapped.
    try {
      const notifications = await worker?.getNotifications?.();
      for (const notification of notifications || []) {
        if (seenAlerts?.has(notification.data?.incident)) notification.close();
      }
    } catch (_) {}
    await refresh();
  } catch (error) { message(error.message, true); }
  finally {
    clearingInbox = false;
    byId("clearAlerts").removeAttribute("aria-busy");
    byId("clearAlerts").disabled = !device || !byId("incidents").childElementCount;
  }
});
byId("openProtectedEvidence").addEventListener("click", async event => {
  if (!current) return;
  const incidentId = current.id;
  const button = event.currentTarget;
  button.disabled = true;
  try {
    const result = await request("/incidents/" + incidentId + "/evidence", {});
    if (current?.id !== incidentId) return;
    const target = new URL(result.url);
    if (target.protocol !== "https:" || target.username || target.password || target.pathname !== root ||
        new URLSearchParams(target.hash.slice(1)).get("incident") !== incidentId) {
      throw new Error("The evidence address is unavailable.");
    }
    location.assign(target.href);
  } catch (error) { message(error.message, true); }
  finally { button.disabled = false; }
});
byId("disconnect").addEventListener("click", () => {
  byId("confirmDisconnect").returnValue = "cancel";
  byId("confirmDisconnect").showModal();
});
byId("confirmDisconnect").addEventListener("close", async event => {
  if (event.target.returnValue !== "disconnect" || !device) return;
  const button = byId("disconnect"), session = device;
  button.disabled = true;
  try {
    await request("/logout", {});
    try { localStorage.removeItem("rakshanetra.seen." + session.id); } catch (_) {}
    device = null;
    history.replaceState(null, "", sessionRoot);
    clearPrivateView();
    message("Phone disconnected. Scan a new QR to connect.");
  } catch (error) { message(error.message, true); }
  finally { button.disabled = false; }
});
byId("retryInvite").addEventListener("click", linkScannedInvite);
byId("stillDialog").addEventListener("close", event => {
  if (!event.target.open) byId("enlargedStill").removeAttribute("src");
});
byId("clip").addEventListener("error", () => {
  if (current?.clip !== "ready") return;
  byId("videoPlaceholder").hidden = false;
  byId("retryClip").hidden = false;
  write("clipStatus", "The video could not be played.");
});
byId("clip").addEventListener("loadedmetadata", () => {
  byId("retryClip").hidden = true;
  byId("videoPlaceholder").hidden = true;
});
byId("retryClip").addEventListener("click", () => {
  if (current?.clip !== "ready") return;
  byId("clip").load();
  byId("clip").play().catch(() => { write("clipStatus", "Tap play to start the video."); });
});
byId("reviews").addEventListener("click", async event => {
  const button = event.target.closest("[data-review]");
  if (!button || !current) return;
  button.disabled = true;
  try {
    await request("/incidents/" + current.id + "/review", {action: button.dataset.review, note: byId("note").value});
    removeIncomingAlert(current.id);
    byId("note").value = "";
    await refresh();
  } catch (error) { message(error.message, true); }
  finally { button.disabled = false; }
});
byId("forward").addEventListener("click", () => {
  byId("confirmHandoff").returnValue = "cancel";
  byId("confirmHandoff").showModal();
});
byId("confirmHandoff").addEventListener("close", async event => {
  if (event.target.returnValue !== "send" || !current) return;
  try {
    await request("/incidents/" + current.id + "/handoff", {confirm: true, note: byId("note").value});
    byId("note").value = "";
    await refresh();
  } catch (error) { message(error.message, true); }
});

function applicationKey(value) {
  const base64 = (value + "=".repeat((4 - value.length % 4) % 4)).replace(/-/g, "+").replace(/_/g, "/");
  return Uint8Array.from(atob(base64), char => char.charCodeAt(0));
}
window.addEventListener("beforeinstallprompt", event => {
  event.preventDefault();
  if (appUnlocked) return;
  installPrompt = event;
  byId("installFromScreen").hidden = false;
});
byId("installFromScreen").addEventListener("click", async () => {
  if (!installPrompt) return;
  if (linkingInvite) {
    write("installStatus", "Wait for your phone to finish linking, then tap Install app.");
    return;
  }
  const prompt = installPrompt;
  installPrompt = null;
  byId("installFromScreen").hidden = true;
  try {
    await prompt.prompt();
    const {outcome} = await prompt.userChoice;
    if (outcome === "accepted") write("installStatus", "Open RakshaNetra from your home screen to continue.");
  } catch (_) {
    write("installStatus", "Use the browser menu to add the app to your home screen.");
  }
});
byId("back").addEventListener("click", event => {
  event.preventDefault();
  history.pushState(null, "", root);
  refresh();
});
window.addEventListener("hashchange", () => {
  if (!appUnlocked) { linkScannedInvite(); return; }
  if (device) refresh();
  else loadInviteFromLink();
});
window.addEventListener("online", () => {
  if (device) connect();
  else if (!appUnlocked && !byId("retryInvite").hidden) linkScannedInvite();
  else if (!byId("retryConnection").hidden) connect();
});
window.addEventListener("offline", () => setConnectionState("offline"));
document.addEventListener("visibilitychange", () => {
  if (document.hidden) alarm.stop();
  else {
    if (device) connect();
    if (pendingAlerts.length) renderIncomingAlert();
  }
});
window.addEventListener("pagehide", () => alarm.stop());
setInterval(refresh, 2000);

async function connect() {
  if (!appUnlocked) return;
  const retry = byId("retryConnection");
  retry.hidden = true;
  message("Connecting…");
  try { await loadSession(); }
  catch (error) {
    message(error.message || "Could not connect to RakshaNetra.", true);
    retry.hidden = false;
  }
}
if (registeringWorker) {
  const previouslyControlled = !!navigator.serviceWorker.controller;
  let updatingApp = false;
  navigator.serviceWorker.addEventListener("controllerchange", () => {
    if (previouslyControlled && !updatingApp) {
      updatingApp = true;
      if (!pendingAlerts.length) { alarm.stop(); location.reload(); }
    }
  });
  navigator.serviceWorker.register(root + "sw.js")
    .then(async registration => { worker = registration; await reconcileSubscription(); })
    .catch(() => { pushSetupError = "The notification service could not start. Reopen the app to retry."; })
    .finally(() => { registeringWorker = false; reportPushError(); });
  navigator.serviceWorker.addEventListener("message", event => {
    if (event.data?.type === "alert") {
      if (device && idPattern.test(event.data.incident || "")) {
        const session = device;
        request("/incidents/" + event.data.incident).then(incident => {
          if (device === session && incident.review === "unreviewed") announceAlert(incident);
        }).catch(() => {});
      }
    } else if (event.data?.type === "acknowledged") {
      removeIncomingAlert(event.data.incident);
    } else if (idPattern.test(event.data?.incident || "")) {
      location.hash = "incident=" + event.data.incident;
    }
    refresh();
  });
}
byId("retryConnection").addEventListener("click", connect);
function updateInstallScreen() {
  const installed = standaloneMode.matches || window.navigator.standalone === true || evidenceView;
  document.documentElement.classList.toggle("standalone", installed);
  byId("installScreen").hidden = installed;
  byId("appScreen").hidden = !installed;
  if (installed && !appUnlocked) {
    appUnlocked = true;
    linkScannedInvite().then(connect);
  } else if (!installed) {
    linkScannedInvite();
  }
}
standaloneMode.addEventListener("change", updateInstallScreen);
updateInstallScreen();
