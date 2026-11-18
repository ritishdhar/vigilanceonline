/* DOM integration only. No browser, camera, external request or desktop alert.
   Run with NODE_PATH pointing to a jsdom installation. */
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {JSDOM} = require('jsdom');
const root = path.resolve(__dirname, '..');
const html = fs.readFileSync(path.join(root, 'web/dashboard.html'), 'utf8').replace('__VIGILANCE_TOKEN__', 't'.repeat(40));
const script = fs.readFileSync(path.join(root, 'web/dashboard.js'), 'utf8');
const flush = async () => { for (let i = 0; i < 15; i++) await new Promise(setImmediate); };

async function app(phone, storage = {}) {
  const dom = new JSDOM(html, {url: 'https://judges.example/', runScripts: 'outside-only', pretendToBeVisual: true});
  const w = dom.window, calls = [], notices = [], errors = [], timers = [];
  for (const [key, value] of Object.entries(storage)) w.localStorage.setItem(key, value);
  const status = {running: false, source: 'idle', source_generation: 0, model: {ready: true, head_trained: true}, timeline: [], inference_mode: 'model'};
  let tick = 0, permissionCalls = 0, stopped = 0;
  w.addEventListener('error', e => errors.push(e.message));
  w.setTimeout = () => ++tick; w.clearTimeout = () => {};
  w.setInterval = action => { timers.push(action); return ++tick; };
  w.matchMedia = () => ({matches: false, addEventListener() {}});
  w.scrollTo = () => {}; w.focus = () => {}; w.isSecureContext = true;
  w.Headers = Headers; w.AbortController = AbortController; w.AbortSignal = AbortSignal;
  w.URL.createObjectURL = () => 'blob:test'; w.URL.revokeObjectURL = () => {};
  w.HTMLDialogElement.prototype.showModal = function () { this.setAttribute('open', ''); };
  w.HTMLDialogElement.prototype.close = function () { this.removeAttribute('open'); this.dispatchEvent(new w.Event('close')); };
  w.HTMLMediaElement.prototype.play = async function () {
    Object.defineProperty(this, 'videoWidth', {configurable: true, value: 640});
    Object.defineProperty(this, 'videoHeight', {configurable: true, value: 360});
  };
  w.HTMLCanvasElement.prototype.getContext = () => ({drawImage() {}});
  w.HTMLCanvasElement.prototype.toBlob = function (cb) { cb(new w.Blob(['jpeg'], {type: 'image/jpeg'})); };
  const track = {stop() { stopped++; }, addEventListener() {}};
  Object.defineProperty(w.navigator, 'mediaDevices', {value: {getUserMedia: async () => ({getTracks: () => [track], getVideoTracks: () => [track]})}});
  w.Notification = class {
    static permission = 'default';
    static async requestPermission() { permissionCalls++; this.permission = 'granted'; return 'granted'; }
    constructor(title, options) { notices.push({title, options}); }
    close() {}
  };
  w.XMLHttpRequest = class extends w.EventTarget {
    constructor() { super(); this.upload = new w.EventTarget(); this.headers = {}; }
    open(method, url) { this.method = method; this.url = url; }
    setRequestHeader(name, value) { this.headers[name] = value; }
    send(body) {
      calls.push({url: this.url, options: {method: this.method, body, headers: this.headers}});
      this.status = 200; this.responseText = JSON.stringify({ok: true, source: 'uploaded.mp4', source_generation: 1});
      queueMicrotask(() => this.dispatchEvent(new w.Event('load')));
    }
    abort() { this.dispatchEvent(new w.Event('abort')); }
  };
  w.fetch = async (url, options = {}) => {
    calls.push({url, options});
    let data = {}, code = 200;
    if (url === '/api/capabilities') data = {inference_mode: 'model', model: status.model, max_active_sources: 1, browser_webcam: true, samples: [{id: 'test1.mp4', filename: 'test1.mp4', bytes: 870743}], streams: [{id: '0', label: 'rtsp://camera.local/live'}]};
    else if (url === '/api/status') data = {...status};
    else if (url === '/api/demo' || url === '/api/source') data = {ok: true, source: url === '/api/demo' ? 'test1.mp4' : JSON.parse(options.body).source || 'rtsp://camera.local/live', source_generation: 1};
    else if (url === '/api/webcam/start') data = {ok: true, source: 'browser-webcam', source_generation: 1, camera_token: 'camera-token'};
    else if (url.startsWith('/api/webcam/')) data = {ok: true};
    else if (phone && url === '/api/phone-admin') data = {phone_https: true, push: true, devices: phone.devices, demo_available: !!phone.demo};
    else if (phone?.demo && url === '/api/phone-demo-admin') data = {origin: 'https://public.example', phone_https: true, push: true, devices: phone.devices};
    else if (phone?.demo && url === '/api/phone-demo-admin/invite') data = {qr: 'data:image/png;base64,AA==', url: 'https://public.example/recipient/#invite=' + phone.code, code: phone.code, expires: Date.now() / 1000 + 600};
    else if (phone?.demo && url === '/api/phone-demo-admin/invite/progress') data = phone.progress;
    else if (phone && url === '/api/phone-admin/invite') {
      data = {qr: 'data:image/png;base64,AA==', url: 'https://phones.example/recipient/#invite=' + phone.code, code: phone.code, expires: Date.now() / 1000 + 600};
    }
    else if (phone && url === '/api/phone-admin/invite/progress') data = phone.progress;
    else if (phone && url === '/api/phone-admin/invite/cancel') data = {status: 'canceled'};
    else if (url === '/snapshot.jpg') return {ok: true, headers: new Headers({'Content-Type': 'image/jpeg'}), blob: async () => new w.Blob(['jpeg'])};
    else { code = 404; data = {error: 'Not found'}; }
    return {ok: code === 200, status: code, json: async () => data};
  };
  w.eval(script); await flush();
  const $ = id => w.document.getElementById(id);
  async function poll(next) { Object.assign(status, next); w.dispatchEvent(new w.Event('online')); await flush(); }
  function submit(id) { $(id).dispatchEvent(new w.Event('submit', {cancelable: true})); }
  async function openSource(button) {
    button.click(); await flush();
    if ($('recipientCautionDialog').open) {
      $('cautionSkipRecipient').click(); await flush();
    }
    assert($('sourceModal').open, 'source dialog must open after recipient choice');
  }
  return {w, $, calls, notices, errors, status, poll, submit, openSource,
    tick: async () => { timers.forEach(action => action()); await flush(); },
    close: () => dom.window.close(), get permissionCalls() {return permissionCalls;}, get stopped() {return stopped;}};
}

test('weapon alerts stay in Active for ten seconds before moving into Recent', async () => {
  const ui = await app();
  let now = 100000;
  ui.w.Date.now = () => now;
  const event = {id: 'b'.repeat(32), start: 1, end: 1, scene: 'THREAT',
    alerts: ['Weapon Threat'], confidence: .65, observed_at: now / 1000};
  try {
    await ui.poll({running: true, source: '0', source_generation: 1, frame: 1, ts: 1,
      fight_status: 'ASSESSED', scene: {verdict: 'NO_FIGHT'}, timeline: [event],
      active_event_ids: [event.id], alert_details: {'Weapon Threat': {confidence: .65}}});
    assert.equal(ui.$('activeTabCount').textContent, '1');
    assert.equal(ui.$('recentTabCount').textContent, '0');
    now += 1000;
    await ui.poll({frame: 2, ts: 2, active_event_ids: [], alert_details: {}});
    assert.equal(ui.$('activeTabCount').textContent, '1');
    assert.match(ui.$('alertsList').textContent, /NEW ALERT/);
    ui.$('recentTab').click();
    assert.equal(ui.$('alertsList').children.length, 0);
    ui.$('activeTab').click();
    now = 109999;
    await ui.tick();
    assert.equal(ui.$('activeTabCount').textContent, '1');
    now = 110000;
    await ui.tick();
    assert.equal(ui.$('activeTabCount').textContent, '0');
    assert.equal(ui.$('recentTabCount').textContent, '1');
    ui.$('recentTab').click();
    assert.match(ui.$('alertsList').textContent, /Possible weapon/);
    assert.deepEqual(ui.errors, []);
  } finally { ui.close(); }
});

test('clearing Recent preserves active alerts and history and survives reopening', async () => {
  const a = await app();
  const now = Date.now(), old = {id: 'c'.repeat(32), start: 1, end: 2,
    scene: 'THREAT', alerts: ['Weapon Threat'], confidence: .65, observed_at: now / 1000 - 60};
  const active = {...old, id: 'd'.repeat(32), start: 3, end: 4, observed_at: now / 1000};
  const status = {running: true, source: '0', source_generation: 1, frame: 4, ts: 4,
    fight_status: 'ASSESSED', timeline: [old, active], active_event_ids: [active.id],
    scene: {verdict: 'NO_FIGHT'}, alert_details: {'Weapon Threat': {confidence: .65}}};
  let reopened;
  try {
    await a.poll(status);
    assert(a.$('clearRecentButton').hidden);
    a.$('recentTab').click();
    assert(!a.$('clearRecentButton').hidden);
    assert.equal(a.$('recentTabCount').textContent, '1');
    a.$('clearRecentButton').click();
    assert(a.$('clearRecentDialog').open);
    a.w.document.querySelector('[data-close="clearRecentDialog"]').click();
    assert.equal(a.$('recentTabCount').textContent, '1');
    a.$('clearRecentButton').click();
    a.$('confirmClearRecent').click();
    assert.equal(a.$('recentTabCount').textContent, '0');
    assert.equal(a.$('activeTabCount').textContent, '1');
    assert.equal(a.$('navIncidentCount').textContent, '2');
    assert(a.$('clearRecentButton').disabled);
    await a.poll(status);
    assert.equal(a.$('alertsList').children.length, 0, 'Polling must not restore cleared alerts');
    a.w.location.hash = 'incidents';
    a.w.dispatchEvent(new a.w.HashChangeEvent('hashchange'));
    await flush();
    assert.equal(a.$('incidentRows').children.length, 2);
    const storage = Object.fromEntries(Object.keys(a.w.localStorage).map(key => [key, a.w.localStorage.getItem(key)]));
    reopened = await app(undefined, storage);
    await reopened.poll(status);
    reopened.$('recentTab').click();
    assert.equal(reopened.$('recentTabCount').textContent, '0');
    assert.equal(reopened.$('activeTabCount').textContent, '1');
    const newer = {...old, id: 'e'.repeat(32), start: 5, end: 6};
    await reopened.poll({...status, frame: 6, ts: 6, timeline: [old, active, newer]});
    assert.equal(reopened.$('recentTabCount').textContent, '1', 'New incidents must still enter Recent');
    assert.deepEqual(a.errors, []);
    assert.deepEqual(reopened.errors, []);
  } finally { a.close(); reopened?.close(); }
});

test('recipient QR is replaced by the device that consumed the invite', async () => {
  const phone = {code: 'c'.repeat(43), progress: {status: 'waiting'}, devices: []};
  const a = await app(phone);
  try {
    a.w.location.hash = 'recipients';
    await a.poll({incident_history: true});
    assert.match(a.$('recipientsHeading').textContent, /Linked devices/);
    assert.equal(a.$('phoneRole'), null);
    assert.equal(a.$('openPhoneApp'), null);
    assert(a.$('phoneTestButton').disabled);
    a.$('phoneInviteButton').click();
    await flush();
    assert.equal(a.$('phoneInvite').hidden, false);
    assert.equal(a.$('phoneInviteStatus').textContent, 'Waiting for phone');
    assert.equal(a.$('phoneInviteCode').value, phone.code);
    assert.equal(a.$('phoneInviteAddress').textContent, 'phones.example');
    const created = a.calls.find(call => call.url === '/api/phone-admin/invite');
    assert.deepEqual(JSON.parse(created.options.body), {role: 'recipient'});
    assert(a.$('phoneInviteButton').disabled);
    a.$('phoneInviteCancel').click();
    await flush();
    const canceled = a.calls.find(call => call.url === '/api/phone-admin/invite/cancel');
    assert.deepEqual(JSON.parse(canceled.options.body), {code: phone.code});
    assert(a.$('phoneInvite').hidden);
    assert.equal(a.$('phoneInviteCode').value, '');
    assert(!a.$('phoneInviteButton').disabled);
    phone.code = 'd'.repeat(43);
    a.$('phoneInviteButton').click();
    await flush();
    assert.equal(a.$('phoneInviteCode').value, phone.code);

    const device = {id: '1'.repeat(32), name: 'iPhone', role: 'recipient', subscribed: false};
    phone.devices.push(device);
    phone.progress = {status: 'linked', device};
    await a.tick();
    assert(a.$('phoneInvite').hidden);
    assert.equal(a.$('phoneInviteQR').getAttribute('src'), null);
    assert.equal(a.$('phoneInviteCode').value, '');
    assert.equal(a.$('phoneLinked').hidden, false);
    assert.equal(a.$('phoneLinkedName').textContent, 'iPhone linked');
    assert.match(a.$('phoneDeviceRows').textContent, /iPhoneNotifications off/);
    assert.equal(a.$('phoneTestButton').disabled, false);
    assert.deepEqual(a.errors, []);
  } finally { a.close(); }
});

test('public PWA invites are selected automatically when the public app is available', async () => {
  const a = await app({demo: true, code: 'c'.repeat(43), devices: [], progress: {status: 'waiting'}});
  try {
    a.w.location.hash = 'recipients';
    await a.poll({incident_history: true});
    assert.equal(a.$('phoneWorkspaceSelect').value, 'demo');
    assert.equal(a.$('phoneInviteButton').disabled, false);
    a.$('phoneInviteButton').click();
    await flush();
    assert(a.calls.some(call => call.url === '/api/phone-demo-admin/invite'));
    assert(!a.calls.some(call => call.url === '/api/phone-admin/invite'));
    assert.equal(a.$('phoneInviteAddress').textContent, 'public.example');
    assert.deepEqual(a.errors, []);
  } finally { a.close(); }
});

test('recipient prompt waits for a choice and resumes the selected source once', async () => {
  const a = await app();
  try {
    a.$('connectButton').click(); await flush();
    assert(a.$('recipientCautionDialog').open);
    assert.equal(a.$('sourceModal').open, false);
    assert(!a.calls.some(c => ['/api/source', '/api/webcam/start', '/api/upload'].includes(c.url)));
    a.$('cautionSkipRecipient').click(); await flush();
    assert.equal(a.$('recipientCautionDialog').open, false);
    assert(a.$('sourceModal').open);
    assert.equal(a.$('uploadVideoForm').hidden, false);
    assert.deepEqual(a.errors, []);
  } finally { a.close(); }
});

test('closing the recipient prompt cancels the pending source action', async () => {
  const a = await app();
  try {
    a.$('connectButton').click(); await flush();
    a.$('recipientCautionDialog').close();
    a.$('cautionSkipRecipient').click(); await flush();
    assert.equal(a.$('sourceModal').open, false);
    assert(!a.calls.some(c => c.url === '/api/upload'));
  } finally { a.close(); }
});

test('four cards open their actual source flows; focus and navigation work', async () => {
  const a = await app();
  try {
    assert.equal(a.w.document.querySelectorAll('#cameraGrid .camera-card').length, 4);
    const buttons = [...a.w.document.querySelectorAll('.add-camera-source')];
    for (const [i, form] of ['webcamForm', 'rtspForm', 'uploadVideoForm'].entries()) {
      await a.openSource(buttons[i]);
      assert(a.$('sourceModal').open); assert.equal(a.$(form).hidden, false);
      a.$('sourceModal').close();
    }
    a.$('focusButton').click(); assert(a.$('cameraGrid').classList.contains('focus-layout'));
    a.$('gridButton').click(); assert(!a.$('cameraGrid').classList.contains('focus-layout'));
    for (const view of ['overview', 'incidents', 'analytics', 'health', 'settings', 'recipients', 'monitoring']) {
      a.w.location.hash = view; a.w.dispatchEvent(new a.w.HashChangeEvent('hashchange')); await flush();
      assert.equal(a.w.document.body.dataset.view, view);
      const visible = [...a.w.document.querySelectorAll('.view-section')].filter(e => !e.hidden);
      assert(visible.every(e => e.dataset.views.split(' ').includes(view)));
    }
    assert.deepEqual(a.errors, []);
  } finally { a.close(); }
});

test('video library is removed and the upload source action remains available', async () => {
  const a = await app();
  try {
    assert.equal(a.$('demoVideos'), null);
    assert.equal(a.$('demoUploadButton'), null);
    assert.equal(a.w.document.querySelector('.demo-bar'), null);
    assert(!a.w.document.body.textContent.includes('Video library'));
    await a.openSource(a.$('connectButton'));
    assert(!a.$('uploadVideoForm').hidden);
    assert(!a.calls.some(c => c.url === '/api/demo' || c.url.startsWith('/api/demos/download/')));
    assert.deepEqual(a.errors, []);
  } finally { a.close(); }
});

test('webcam permission, frame upload, and stop use the owning session', async () => {
  const a = await app();
  try {
    await a.openSource(a.w.document.querySelector('.add-camera-source'));
    a.submit('webcamForm'); await flush();
    assert(a.calls.some(c => c.url === '/api/webcam/start'));
    assert(a.calls.some(c => c.url === '/api/webcam/frame' && c.options.headers.get('X-Vigilance-Camera') === 'camera-token'));
    assert.equal(a.$('stopWebcamButton').hidden, false);
    a.$('stopWebcamButton').click(); await flush();
    assert(a.stopped > 0); assert(a.calls.some(c => c.url === '/api/webcam/stop'));
    assert.equal(a.$('webcamPreview').srcObject, null);
    assert.deepEqual(a.errors, []);
  } finally { a.close(); }
});

test('denied webcam access produces an actionable in-app error', async () => {
  const a = await app();
  try {
    a.w.navigator.mediaDevices.getUserMedia = async () => { const error = new Error(); error.name = 'NotAllowedError'; throw error; };
    await a.openSource(a.w.document.querySelector('.add-camera-source')); a.submit('webcamForm'); await flush();
    assert.match(a.$('modalMessage').textContent, /Camera access was denied/);
    assert.equal(a.$('startWebcamButton').disabled, false);
    assert(!a.calls.some(c => c.url === '/api/webcam/start'));
  } finally { a.close(); }
});

test('browser alerts request permission on click and notify once per real incident', async () => {
  const a = await app();
  try {
    assert.equal(a.permissionCalls, 0);
    a.$('notificationButton').click(); await flush(); assert.equal(a.permissionCalls, 1);
    const detection = {running: true, source: 'test1.mp4', frame: 30, ts: 5, fight_status: 'ASSESSED', source_generation: 1,
      scene: {verdict: 'FIGHT'}, pairs: [{score: .8}], alert_details: {'Violence/Fight': {confidence: .8}}, active_event_ids: ['1:1'],
      timeline: [{id: '1:1', start: 4, end: 5, scene: 'FIGHT', alerts: ['Violence/Fight'], confidence: .8}]};
    await a.poll(detection); await a.poll(detection);
    assert.equal(a.notices.length, 1); assert.match(a.$('toastMessage').textContent, /fight detected/);
    assert.equal(a.$('alertsList').children.length, 1);
    assert.deepEqual(a.errors, []);
  } finally { a.close(); }
});

test('upload sends the selected video, confirms capture, and exposes its filename', async () => {
  const a = await app();
  try {
    await a.openSource(a.$('connectButton'));
    const file = new a.w.File(['sample'], 'judge-clip.mp4', {type: 'video/mp4'});
    Object.defineProperty(a.$('videoFileInput'), 'files', {value: [file]});
    a.submit('uploadVideoForm'); await flush();
    const call = a.calls.find(c => c.url === '/api/upload'); assert(call);
    assert.equal(call.options.body.get('video').name, 'judge-clip.mp4');
    assert.equal(call.options.headers['X-Vigilance-Token'], 't'.repeat(40));
    await a.poll({source: 'uploaded.mp4', source_generation: 1, running: true, frame: 0, ts: 0, fight_status: 'WARMING_UP'});
    assert.equal(a.$('sourceName').textContent, 'judge-clip.mp4');
    assert.equal(a.$('sourceModal').open, false);
    assert.deepEqual(a.errors, []);
  } finally { a.close(); }
});

test('configured camera submits its ID rather than a redacted credential URL', async () => {
  const a = await app();
  try {
    await a.openSource(a.w.document.querySelectorAll('.add-camera-source')[1]);
    a.$('configuredCamera').value = '0'; a.submit('rtspForm'); await flush();
    const call = a.calls.find(c => c.url === '/api/source');
    assert.deepEqual(JSON.parse(call.options.body), {stream_id: '0'});
  } finally { a.close(); }
});

test('closing while webcam permission is pending releases the camera when it resolves', async () => {
  const a = await app();
  try {
    let resolve, stopped = false;
    a.w.navigator.mediaDevices.getUserMedia = () => new Promise(r => { resolve = r; });
    await a.openSource(a.w.document.querySelector('.add-camera-source')); a.submit('webcamForm'); await flush();
    a.$('sourceModal').close();
    resolve({getTracks: () => [{stop() { stopped = true; }}]}); await flush();
    assert(stopped); assert(!a.calls.some(c => c.url === '/api/webcam/start'));
    assert.equal(a.$('startWebcamButton').disabled, false);
  } finally { a.close(); }
});

test('source errors stay visible without reporting a successful connection', async () => {
  const a = await app();
  try {
    await a.openSource(a.$('connectButton'));
    const file = new a.w.File(['invalid video'], 'unreadable.mp4', {type: 'video/mp4'});
    Object.defineProperty(a.$('videoFileInput'), 'files', {value: [file]});
    a.submit('uploadVideoForm'); await flush();
    await a.poll({running: false, source: 'uploaded.mp4', source_generation: 1, error: 'Cannot decode this video'});
    assert(a.$('sourceModal').open);
    assert.match(a.$('modalMessage').textContent, /Cannot decode/);
    assert.equal(a.$('serviceNotice').hidden, false);
    assert.match(a.$('serviceNotice').textContent, /Source error/);
  } finally { a.close(); }
});
