const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const script = fs.readFileSync(path.join(__dirname, '../web/recipient/alarm.js'), 'utf8');

test('ringing requires a gesture to arm, repeats, and releases sound and vibration on stop', async () => {
  let stopped = 0, disconnected = 0, started = 0, intervals = 0, cleared = 0;
  const vibrations = [];
  class Audio {
    constructor() { this.state = 'suspended'; this.sampleRate = 8000; }
    async resume() { this.state = 'running'; }
    createBuffer(_, length) { return {getChannelData: () => new Float32Array(length)}; }
    createBufferSource() {
      return {connect() {}, start() { started++; }, stop() { stopped++; }, disconnect() { disconnected++; }};
    }
  }
  const context = vm.createContext({
    window: {AudioContext: Audio}, document: {hidden: false},
    navigator: {vibrate: value => vibrations.push(value)},
    setInterval() { return ++intervals; }, clearInterval(id) { if (id) cleared++; }
  });
  vm.runInContext(script + '\nvar alarm = new RecipientAlarm();', context);
  const alarm = context.alarm;
  alarm.start();
  assert.equal(started, 0);
  await alarm.arm();
  alarm.start();
  alarm.start();
  assert.equal(started, 1);
  assert.equal(intervals, 1);
  assert(Array.isArray(vibrations[0]));
  alarm.stop();
  alarm.stop();
  assert.equal(stopped, 1);
  assert.equal(disconnected, 1);
  assert.equal(cleared, 1);
  assert.equal(vibrations.at(-1), 0);
  context.document.hidden = true;
  alarm.start();
  assert.equal(started, 1);
});
