const {test} = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const serviceWorker = fs.readFileSync(path.join(__dirname, "../web/recipient/sw.js"), "utf8");
const origin = "https://recipient.example";
const incident = "1".repeat(32);

function startWorker(clients = [], failDisplay = false) {
  const handlers = new Map(), notifications = [], opened = [], receipts = [];
  vm.runInNewContext(serviceWorker, {URL, AbortSignal,
    fetch: async (_, options) => { receipts.push(JSON.parse(options.body)); return {ok: true}; },
    self: {
    location: {origin},
    addEventListener: (event, callback) => handlers.set(event, callback),
    registration: {
      async showNotification(title, options) {
        if (failDisplay) throw new Error('denied');
        notifications.push({title, options});
      }
    },
    clients: {
      async matchAll() { return clients; },
      async openWindow(url) { opened.push(url); }
    }
  }});
  async function dispatch(name, data) {
    let work;
    handlers.get(name)({...data, waitUntil(promise) { work = promise; }});
    await work;
  }
  return {dispatch, notifications, opened, receipts};
}

test("push follows phone settings and requests a persistent notification linked to the incident", async () => {
  const {dispatch, notifications} = startWorker();
  await dispatch("push", {data: {json: () => ({title: "RakshaNetra incident", incident})}});
  assert.equal(notifications.length, 1);
  assert.equal(notifications[0].options.silent, undefined);
  assert.equal(notifications[0].options.requireInteraction, true);
  assert.equal(notifications[0].options.renotify, true);
  assert(notifications[0].options.vibrate.length > 0);
  assert.equal(notifications[0].options.data.incident, incident);
});

test('push reports display failure and still wakes the app alert screen', async () => {
  const messages = [];
  const worker = startWorker([{postMessage: message => messages.push(message)}], true);
  await worker.dispatch('push', {data: {json: () => ({incident})}});
  assert.equal(worker.receipts[0].status, 'display-failed');
  assert.equal(messages[0].incident, incident);
});

test('notification acknowledgement posts the review and refreshes the app without opening another window', async () => {
  const messages = [];
  const worker = startWorker([{postMessage: message => messages.push(message)}]);
  await worker.dispatch('notificationclick', {action: 'acknowledge', notification: {data: {incident}, close() {}}});
  assert.equal(worker.receipts[0].status, 'opened');
  assert.equal(worker.receipts[1].action, 'acknowledged');
  assert.equal(messages[0].type, 'acknowledged');
  assert.equal(worker.opened.length, 0);
});

test("notification tap routes an already-open app to the incident", async () => {
  let navigated, focused = false, closed = false;
  const {dispatch, opened} = startWorker([{
    url: origin + "/recipient/#incident=" + "2".repeat(32),
    async navigate(url) {
      navigated = url;
      return {async focus() { focused = true; }};
    }
  }]);
  await dispatch("notificationclick", {notification: {data: {incident}, close() { closed = true; }}});
  assert.equal(navigated, origin + "/recipient/#incident=" + incident);
  assert.equal(focused && closed, true);
  assert.equal(opened.length, 0);
});

test("notification tap opens the incident when no app window exists", async () => {
  const {dispatch, opened} = startWorker();
  await dispatch("notificationclick", {notification: {data: {incident}, close() {}}});
  assert.deepEqual(opened, [origin + "/recipient/#incident=" + incident]);
});
