/* Frontend regressions: node --test tests/frontend.test.cjs. No model/runtime dependencies. */
const {test} = require('node:test');
const assert = require('node:assert/strict');
const UI = require('../web/dashboard.js');

function fight(overrides = {}) {
  return {
    running: true, source: 'sample.mp4', ts: 12, frame: 240, fight_status: 'ASSESSED',
    scene: {verdict: 'FIGHT', incidents: [{members: [1, 2], violent_edges: 1}]},
    alert_details: {'Violence/Fight': {confidence: .57, message: 'Review violent interaction'}},
    pairs: [{score: .57}], timeline: [{start: 10, end: 12, scene: 'FIGHT', alerts: ['Violence/Fight']}],
    ...overrides
  };
}
test('scene verdict and equivalent fight detail produce one incident', () => {
  const state = UI.createSession(); state.consume(fight(), 1000);
  assert.equal(state.alertStore.size, 1);
  const entry = [...state.alertStore.values()][0];
  assert.equal(entry.active, true); assert.equal(entry.score, .57);
});
test('end of video retains history without a current active detection', () => {
  const state = UI.createSession(); state.consume(fight(), 1000);
  state.consume(fight({running: false, ended: true}), 2000);
  assert.equal(state.alertStore.size, 1);
  assert.equal([...state.alertStore.values()].filter(e => e.active).length, 0);
  assert.equal(UI.health(fight({running: false, ended: true}), true).label, 'Video complete');
});
test('network loss marks prior detections uncertain, not current or resolved', () => {
  const state = UI.createSession(); state.consume(fight(), 1000); state.disconnect();
  const entry = [...state.alertStore.values()][0];
  assert.equal(entry.active, false); assert.equal(entry.uncertain, true);
  assert.equal(entry.review, 'unreviewed');
});
test('stale frames cannot sustain active alerts', () => {
  const state = UI.createSession(); state.consume(fight(), 1000);
  state.consume(fight(), 9000, false);
  assert.equal([...state.alertStore.values()][0].active, false);
  assert.equal([...state.alertStore.values()][0].uncertain, true);
  assert.equal(UI.health(fight(), true, true).label, 'Waiting for frames');
});
test('independent episodes retain independent reviews', () => {
  const state = UI.createSession(); state.consume(fight(), 1000);
  [...state.alertStore.values()][0].review = 'acknowledged';
  state.consume(fight({ts: 15, frame: 300, scene: {verdict: 'NO_FIGHT'}, alert_details: {}}), 2000);
  state.consume(fight({ts: 25, frame: 500, timeline: [
    {start: 10, end: 12, scene: 'FIGHT', alerts: ['Violence/Fight']},
    {start: 24, end: 25, scene: 'FIGHT', alerts: ['Violence/Fight']}
  ]}), 3000);
  assert.equal(state.alertStore.size, 2);
  const active = [...state.alertStore.values()].filter(e => e.active);
  assert.equal(active.length, 1); assert.equal(active[0].review, 'unreviewed');
});
test('same source replay starts a new epoch and resets the score trend', () => {
  const state = UI.createSession(); state.consume(fight(), 1000);
  const oldId = [...state.alertStore.keys()][0];
  state.consume(fight({ts: 1, frame: 20, timeline: [{start: 0, end: 1, scene: 'FIGHT', alerts: ['Violence/Fight']}]}), 2000);
  assert.equal(state.epoch, 1); assert.equal(state.alertStore.size, 2);
  assert.equal(state.alertStore.get(oldId).active, false);
  assert.equal(state.samples.length, 1);
});
test('switching sources preserves labeled history and isolates new activity', () => {
  const state = UI.createSession(); state.consume(fight(), 1000, true, 'Main gate');
  state.consume(fight({source: 'second.mp4'}), 2000, true, 'Lobby');
  const records = [...state.alertStore.values()];
  assert.equal(records.length, 2); assert.equal(records[0].camera, 'Main gate');
  assert.equal(records[1].camera, 'Lobby'); assert.equal(records[1].source, 'second.mp4');
});
test('polls of the same frame do not fabricate new chart observations', () => {
  const state = UI.createSession(); state.consume(fight(), 1000); state.consume(fight(), 1500);
  assert.equal(state.samples.length, 1);
  state.consume(fight({frame: 241, ts: 12.05, pairs: [{score: null}]}), 2000);
  assert.equal(state.samples.length, 2); assert.equal(state.samples[1].score, null);
});
test('missing or invalid scores remain absent; zero is a valid observation', () => {
  assert.equal(UI.topPairScore({pairs: [{score: null}, {score: undefined}, {score: NaN}]}), null);
  assert.equal(UI.topPairScore({pairs: [{score: 0}]}), 0);
  assert.equal(UI.score('0.9'), null);
});
test('different event categories remain separate and correctly titled', () => {
  const detections = UI.currentDetections(fight({alert_details: {
    'Violence/Fight': {confidence: .57}, 'Fire Threat': {confidence: .88}
  }}));
  assert.equal(detections.length, 2); assert.equal(detections[1].title, 'Possible fire');
});
test('warming, unavailable, synthetic and disconnected states are explicit', () => {
  assert.equal(UI.health(fight({fight_status: 'WARMING_UP'}), true).label, 'Observing activity');
  assert.equal(UI.health(fight({fight_status: 'UNAVAILABLE'}), true).label, 'Assessment unavailable');
  assert.match(UI.health(fight({inference_mode: 'synthetic'}), true).detail, /Real model detection is disabled/);
  assert.match(UI.health(fight(), false).detail, /unknown/);
});
test('UI output escapes markup and CSV formula-leading labels', () => {
  assert.equal(UI.escapeHTML('<img onerror="bad()">'), '&lt;img onerror=&quot;bad()&quot;&gt;');
  assert.equal(UI.csvCell('=HYPERLINK("url")'), '"\'=HYPERLINK(""url"")"');
  assert.equal(UI.csvCell('Main, gate'), '"Main, gate"');
});
test('stream labels do not expose embedded credentials', () => {
  assert.equal(UI.sourceLabel('rtsp://admin:password@camera.local/live?token=secret'), 'camera.local/live');
  assert.equal(UI.sourceLabel('C:\\videos\\clip.mp4'), 'clip.mp4');
  assert.equal(UI.sourceMode({source: '0'}), 'Live source');
  assert.equal(UI.sourceMode({source: 'clip.mp4'}), 'Recorded video');
});
test('the dashboard has unique element IDs and satisfies its script references', () => {
  const fs = require('node:fs');
  const path = require('node:path');
  const html = fs.readFileSync(path.join(__dirname, '../web/dashboard.html'), 'utf8');
  const script = fs.readFileSync(path.join(__dirname, '../web/dashboard.js'), 'utf8');
  const dynamicMarkup = script.match(/recipientView\.innerHTML = `([\s\S]*?)`;/)?.[1] || '';
  const ids = [...(html + dynamicMarkup).matchAll(/\sid="([^"]+)"/g)].map(match => match[1]);
  assert.equal(ids.length, new Set(ids).size, 'duplicate element IDs');
  const referenced = [...script.matchAll(/\$\("([^"]+)"\)/g)].map(match => match[1]);
  assert.deepEqual(referenced.filter(id => !ids.includes(id)), []);
});
test('Alert recipients is a first-class sidebar navigation destination', () => {
  const fs = require('node:fs'), path = require('node:path');
  const html = fs.readFileSync(path.join(__dirname, '../web/dashboard.html'), 'utf8');
  const menu = html.match(/<nav class="nav-menu"[\s\S]*?<\/nav>/)?.[0] || '';
  assert.match(menu, /href="#recipients" data-view="recipients"/);
  assert.equal((html.match(/href="#recipients"/g) || []).length, 1);
});

test('backend incident IDs keep simultaneous incidents and scores separate', () => {
  const state = UI.createSession();
  const timeline = [
    {id: '1:1', start: 10, end: 12, scene: 'FIGHT', alerts: ['Violence/Fight'], confidence: .63},
    {id: '1:2', start: 10, end: 12, scene: 'FIGHT', alerts: ['Violence/Fight'], confidence: .82}
  ];
  const result = state.consume(fight({source_generation: 1, timeline, active_event_ids: ['1:1', '1:2']}));
  assert.equal(result.created.length, 2);
  assert.deepEqual([...state.alertStore.values()].map(e => e.score), [.63, .82]);
  assert.equal([...state.alertStore.values()].filter(e => e.active).length, 2);
  assert.equal(state.consume(fight({source_generation: 1, timeline, active_event_ids: ['1:1', '1:2']})).created.length, 0);
});

test('a generation change starts a new source session even before frame counter changes', () => {
  const state = UI.createSession();
  state.consume(fight({source_generation: 1}));
  state.consume(fight({source_generation: 2}));
  assert.equal(state.epoch, 1);
  assert.equal(state.alertStore.size, 2);
});

test('webcam and idle labels match actual backend source types', () => {
  assert.equal(UI.sourceLabel('browser-webcam'), 'Your webcam');
  assert.equal(UI.sourceMode({source: 'browser-webcam'}), 'Your webcam');
  assert.equal(UI.sourceMode({source: 'idle'}), 'No active source');
  assert.equal(UI.sourceLabel('rtmp://user:secret@camera.local/live?secret=yes'), 'camera.local/live');
  assert.match(UI.health(fight({inference_mode: 'synthetic', model: {head_trained: true}}), true).detail, /scripted skeletons/);
});
