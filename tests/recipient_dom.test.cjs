const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {JSDOM} = require('jsdom');

const root = path.resolve(__dirname, '../web/recipient');
const html = fs.readFileSync(path.join(root, 'index.html'), 'utf8');
const script = fs.readFileSync(path.join(root, 'app.js'), 'utf8');
const alarmScript = fs.readFileSync(path.join(root, 'alarm.js'), 'utf8');
const settle = async () => { for (let i = 0; i < 6; i++) await new Promise(setImmediate); };

test('scanned invite links before installation and the installed app opens incident evidence', async () => {
  const id = '1'.repeat(32), code = 'c'.repeat(43);
  const appUrl = 'https://phones.example/recipient/';
  const media = '/api/recipient/incidents/' + id;
  const dom = new JSDOM(html, {
    url: appUrl + '#invite=' + code, runScripts: 'outside-only', pretendToBeVisual: true
  });
  const w = dom.window;
  const $ = name => w.document.getElementById(name);
  const installed = {matches: false, addEventListener(_, callback) { this.update = callback; }};
  const incident = {
    id, sequence: 1, title: 'Camera alert', severity: 'Review', site: 'Campus', camera: 'Front door',
    observed_at: 1, review: 'unreviewed', clip: 'processing', stills: ['thumbnail.jpg'], actions: []
  };
  let device = null, clearedThrough = 0, clearFails = true, clearRequests = 0;
  w.matchMedia = () => installed;
  w.HTMLMediaElement.prototype.load = () => {};
  w.HTMLDialogElement.prototype.showModal = function () { this.open = true; };
  w.HTMLDialogElement.prototype.close = function () { this.open = false; };
  w.fetch = async (url, options) => {
    let result = {};
    if (url.endsWith('/enroll')) {
      assert.equal(JSON.parse(options.body).invite, code);
      device = {id: 'a'.repeat(32), name: 'Recipient device', role: 'recipient', subscribed: false};
    } else if (url.endsWith('/session')) result = {device, workspace_name: 'East campus'};
    else if (url === media) result = {...incident};
    else if (url.endsWith('/incidents')) result = {incidents: [incident]};
    return new Response(JSON.stringify(result), {headers: {'Content-Type': 'application/json'}});
  };
  try {
    w.eval(alarmScript + "\n" + script);
    await settle();
    assert(!$('installScreen').hidden);
    assert($('appScreen').hidden);
    assert.match($('installStatus').textContent, /Phone linked/);
    assert.equal(w.location.href, appUrl);

    installed.matches = true;
    installed.update();
    await settle();
    assert(!$('inbox').hidden);
    assert.equal($('notificationPanel'), null);
    assert.equal($('testPhoneNotification'), null);
    assert.equal($('devicePanel'), null);
    assert(!$('connectionCard').hidden);
    assert.equal($('connectionWorkspace').textContent, 'East campus');
    assert.equal($('connectionState').textContent, 'Connected');
    $('previewAlert').click();
    await settle();
    assert(!$('incomingAlert').hidden);
    assert.equal($('incomingAlertCount').textContent, 'Alert preview');
    assert.equal($('incomingAlertTitle').textContent, 'Sample incident alert');
    assert($('incomingAlertOpen').hidden);
    assert($('incomingAlertAcknowledge').hidden);
    assert.match($('incomingAlertBody').textContent, /No incident has been created/);
    w.dispatchEvent(new w.Event('offline'));
    assert.equal($('connectionState').textContent, 'Offline');
    const alertId = '2'.repeat(32);
    const incoming = [{...incident, id: alertId, title: 'Possible weapon detected'}];
    w.fetch = async (url, options) => {
      if (url.endsWith('/logout')) device = null;
      if (url.endsWith('/inbox/clear')) {
        clearRequests++;
        if (clearFails) return new Response(JSON.stringify({error: 'Try again'}),
          {status: 503, headers: {'Content-Type': 'application/json'}});
        clearedThrough = JSON.parse(options.body).through;
      }
      if (url.endsWith('/review')) {
        incoming.find(item => url.includes(item.id)).review = JSON.parse(options.body).action;
      }
      const result = url.endsWith('/inbox/clear') ? {cleared_through: clearedThrough} :
        url.endsWith('/session') ? {device, cleared_through: clearedThrough} : url.endsWith('/incidents') ?
        {incidents: [...incoming, incident]} : {...incident};
      return new Response(JSON.stringify(result), {headers: {'Content-Type': 'application/json'}});
    };
    w.dispatchEvent(new w.Event('online'));
    await settle();
    assert(!$('incomingAlert').hidden);
    assert.equal($('incomingAlertTitle').textContent, 'Possible weapon detected');
    assert.equal($('incomingAlertCount').textContent, 'Incoming incident');
    assert(!$('incomingAlertAcknowledge').hidden, 'A real alert must replace the preview');
    assert.equal($('incomingAlertOpen').getAttribute('href'), '#incident=' + alertId);
    $('incomingAlertClose').click();
    assert($('incomingAlert').hidden);
    w.dispatchEvent(new w.Event('online'));
    await settle();
    assert($('incomingAlert').hidden, 'polling must not re-ring a dismissed alert');
    const secondId = '3'.repeat(32), thirdId = '4'.repeat(32);
    incoming.unshift(
      {...incident, id: thirdId, title: 'Third alert'},
      {...incident, id: secondId, title: 'Second alert'}
    );
    w.dispatchEvent(new w.Event('online'));
    await settle();
    assert.equal($('incomingAlertTitle').textContent, 'Second alert');
    assert.match($('incomingAlertCount').textContent, /2 alerts/);
    $('incomingAlertAcknowledge').click();
    await settle();
    assert.equal(incoming.find(item => item.id === secondId).review, 'acknowledged');
    assert.equal($('incomingAlertTitle').textContent, 'Third alert');
    $('incomingAlertClose').click();
    assert($('incomingAlert').hidden);
    assert.equal($('refresh').getAttribute('aria-label'), 'Refresh incidents');
    $('incidents').lastElementChild.querySelector('a').click();
    await settle();
    assert(!$('detail').hidden);
    assert($('clip').hidden);
    assert(!$('videoPlaceholder').hidden);
    assert.equal($('stills').textContent, 'At alert');

    incident.clip = 'ready';
    incident.stills = ['before.jpg', 'thumbnail.jpg', 'after.jpg'];
    w.dispatchEvent(new w.Event('online'));
    await settle();
    assert.equal($('clip').getAttribute('src'), media + '/clip.mp4');
    assert(!$('clip').hidden);
    assert($('videoPlaceholder').hidden);
    assert($('stillsPlaceholder').hidden);
    assert.deepEqual([...$('stills').children].map(button => button.textContent),
      ['Before alert', 'At alert', 'After alert']);
    $('stills').lastElementChild.click();
    assert($('stillDialog').open);
    assert.equal($('enlargedStill').getAttribute('src'), media + '/after.jpg');
    incident.source = 'Detection notification';
    incident.inline_evidence = true;
    incident.evidence_status = 'ready';
    incident.protected_evidence = false;
    w.dispatchEvent(new w.Event('online'));
    await settle();
    assert(!$('clip').hidden, 'Public detection notices must play their mapped clip inline');
    assert(!$('stillsPanel').hidden, 'Public detection notices must show their mapped photos');
    assert($('openProtectedEvidence').hidden);
    assert($('protectedEvidenceStatus').hidden);
    assert.equal($('clip').getAttribute('src'), media + '/clip.mp4');
    assert.equal($('stills').children.length, 3);
    $('back').click();
    await settle();
    assert(!$('stillDialog').open);
    assert(!$('clip').hasAttribute('src'));
    assert(!$('enlargedStill').hasAttribute('src'));
    assert.equal($('stills').children.length, 0);
    const inboxCount = $('incidents').children.length;
    $('clearAlerts').click();
    assert($('confirmClearAlerts').open);
    $('confirmClearAlerts').returnValue = 'cancel';
    $('confirmClearAlerts').dispatchEvent(new w.Event('close'));
    await settle();
    assert.equal(clearRequests, 0);
    $('clearAlerts').click();
    $('confirmClearAlerts').returnValue = 'clear';
    $('confirmClearAlerts').dispatchEvent(new w.Event('close'));
    await settle();
    assert.equal($('incidents').children.length, inboxCount, 'A failed clear must keep alerts');
    assert(!$('clearAlerts').disabled);
    clearFails = false;
    $('clearAlerts').click();
    $('confirmClearAlerts').returnValue = 'clear';
    $('confirmClearAlerts').dispatchEvent(new w.Event('close'));
    await settle();
    assert.equal($('incidents').children.length, 0);
    assert(!$('empty').hidden);
    assert($('clearAlerts').disabled);
    w.dispatchEvent(new w.Event('online'));
    await settle();
    assert.equal($('incidents').children.length, 0, 'Cleared rows must stay hidden after reconnect');
    incoming.unshift({...incident, id: '5'.repeat(32), sequence: 2, title: 'New incident'});
    w.dispatchEvent(new w.Event('online'));
    await settle();
    assert.equal($('incidents').children.length, 1);
    assert.equal($('incomingAlertTitle').textContent, 'New incident');
    $('incomingAlertClose').click();
    $('previewAlert').click();
    await settle();
    assert.equal($('incomingAlertCount').textContent, 'Alert preview');
    $('incomingAlertClose').click();
    assert($('incomingAlert').hidden);
    $('disconnect').click();
    assert($('confirmDisconnect').open);
    $('confirmDisconnect').returnValue = 'disconnect';
    $('confirmDisconnect').dispatchEvent(new w.Event('close'));
    await settle();
    assert($('connectionCard').hidden);
    assert($('inbox').hidden);
    assert(!$('enrollment').hidden);
  } finally { w.close(); }
});
