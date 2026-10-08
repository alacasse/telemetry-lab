'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const {flowSummary} = require('../../demo/static/thermostat.js');

function fixture() {
  return {
    simulation_id: 'session-1', mode: 'cooling', settings_revision: 4,
    latest_reading: {
      sequence: 12, temperature_c: 21.5, heater_on: true, cooler_on: false,
      observed_at: '2026-10-08T10:00:00Z', published_at: '2026-10-08T10:00:01Z',
      processed_at: '2026-10-08T10:00:03Z', processed_event_id: 'event-12',
      transport_receipt: {status: 'accepted', event_id: 'event-12', transport_receipt: {
        source: 'SQS.SendMessage', message_id: 'reading-message-12', observed_at: '2026-10-08T10:00:02Z',
      }},
    },
    latest_command: {
      sequence: 8, command_type: 'stop_heating', reading_sequence: 11, settings_revision: 3,
      created_at: '2026-10-08T09:59:00Z', published_at: '2026-10-08T09:59:01Z',
      transport_message_id: 'command-message-8', status: 'applied', applied_at: '2026-10-08T09:59:02Z',
    },
  };
}
const confirmed = steps => steps.map(step => step.confirmed);

test('dated nominal evidence confirms each independent service milestone', () => {
  const evidence = fixture();
  const flow = flowSummary(evidence);
  assert.deepEqual(confirmed(flow.reading), [true, true, true, true]);
  assert.deepEqual(flow.reading.map(step => step.at), [evidence.latest_reading.observed_at,
    evidence.latest_reading.published_at, evidence.latest_reading.transport_receipt.transport_receipt.observed_at,
    evidence.latest_reading.processed_at]);
  assert.deepEqual(confirmed(flow.command), [true, true, true]);
  assert.match(flow.readingLabel, /12.*21\.5/);
  assert.match(flow.commandLabel, /8.*stop_heating/);
  assert.notEqual(flow.reading[2].service, flow.command[1].service);
  assert.match(flow.reading[2].service, /SQS.*mesures/);
  assert.match(flow.command[1].service, /SQS.*commandes/);
});

test('processing does not fabricate HTTP acceptance or SQS publication', () => {
  const evidence = fixture();
  delete evidence.latest_reading.transport_receipt;
  const flow = flowSummary(evidence);
  assert.deepEqual(confirmed(flow.reading), [true, false, false, true]);
  assert.equal(flow.reading[1].at, null);
  assert.equal(flow.reading[2].at, null);
});

test('HTTP acceptance and SQS publication retain independent proof requirements', () => {
  const evidence = fixture();
  evidence.latest_reading.transport_receipt.status = 'rejected';
  assert.deepEqual(confirmed(flowSummary(evidence).reading), [true, false, true, true]);
  evidence.latest_reading.transport_receipt.status = 'accepted';
  evidence.latest_reading.transport_receipt.transport_receipt.source = 'queue-counter';
  assert.deepEqual(confirmed(flowSummary(evidence).reading), [true, true, false, true]);
});

test('missing dates cannot confirm any milestone even when identifiers exist', () => {
  const evidence = fixture();
  delete evidence.latest_reading.observed_at;
  delete evidence.latest_reading.published_at;
  delete evidence.latest_reading.processed_at;
  delete evidence.latest_reading.transport_receipt.transport_receipt.observed_at;
  delete evidence.latest_command.created_at;
  delete evidence.latest_command.published_at;
  delete evidence.latest_command.applied_at;
  const flow = flowSummary(evidence);
  for (const step of [...flow.reading, ...flow.command]) {
    assert.equal(step.confirmed, false);
    assert.equal(step.at, null);
  }
});

test('missing event and transport identifiers withhold their dated confirmations', () => {
  const evidence = fixture();
  delete evidence.latest_reading.processed_event_id;
  delete evidence.latest_reading.transport_receipt.event_id;
  delete evidence.latest_reading.transport_receipt.transport_receipt.message_id;
  delete evidence.latest_command.transport_message_id;
  const flow = flowSummary(evidence);
  assert.deepEqual(confirmed(flow.reading), [true, false, false, false]);
  assert.deepEqual(confirmed(flow.command), [true, false, true]);
});

test('requested settings and current phase cannot substitute for command application proof', () => {
  const evidence = fixture();
  evidence.phase = 'cooling';
  evidence.latest_command.status = 'published';
  const flow = flowSummary(evidence);
  assert.equal(flow.command[2].confirmed, false);
  assert.equal(flow.command[2].at, null);
  assert.match(flow.command[2].label, /non confirmée/);
});

test('applied status requires its applied timestamp and a timestamp requires applied status', () => {
  const evidence = fixture();
  delete evidence.latest_command.applied_at;
  assert.equal(flowSummary(evidence).command[2].confirmed, false);
  evidence.latest_command.applied_at = '2026-10-08T09:59:02Z';
  evidence.latest_command.status = 'pending';
  assert.equal(flowSummary(evidence).command[2].confirmed, false);
});

test('cancelled commands remain cancelled even if an application timestamp is present', () => {
  const evidence = fixture();
  evidence.latest_command.status = 'cancelled';
  const finalStep = flowSummary(evidence).command[2];
  assert.equal(finalStep.confirmed, false);
  assert.equal(finalStep.at, null);
  assert.equal(finalStep.label, 'Commande annulée');
});

test('command origin refers to its own source reading and settings revision', () => {
  const flow = flowSummary(fixture());
  assert.match(flow.commandOrigin, /mesure n° 11/);
  assert.match(flow.commandOrigin, /Réglage n° 3/);
  assert.doesNotMatch(flow.commandOrigin, /mesure n° 12|Réglage n° 4/);
});

test('initial missing evidence renders unknown milestones with no dates', () => {
  for (const evidence of [null, undefined, {}]) {
    const flow = flowSummary(evidence);
    assert.match(flow.readingLabel, /Aucune mesure/);
    assert.match(flow.commandLabel, /Aucune commande/);
    for (const step of [...flow.reading, ...flow.command]) {
      assert.equal(step.confirmed, false);
      assert.equal(step.at, null);
    }
  }
});

test('missing display identities are unknown and never borrow the latest reading', () => {
  const evidence = fixture();
  delete evidence.latest_reading.sequence;
  delete evidence.latest_command.sequence;
  delete evidence.latest_command.command_type;
  delete evidence.latest_command.reading_sequence;
  delete evidence.latest_command.settings_revision;
  const flow = flowSummary(evidence);
  for (const label of [flow.readingLabel, flow.commandLabel, flow.commandOrigin]) {
    assert.doesNotMatch(label, /undefined|null|NaN/);
    assert.match(label, /inconn|indisponible/i);
  }
});

const vm = require('node:vm');
const fs = require('node:fs');
const browserScript = fs.readFileSync(require.resolve('../../demo/static/thermostat.js'), 'utf8');

function browserHarness(initial, {post = null, get = null, cached = null, storageFails = false} = {}) {
  class Element {
    constructor(tag) { this.tagName = tag; this.children = []; this.style = {}; this.listeners = {}; this.attributes = {}; this.textContent = ''; this.value = ''; }
    append(...children) { this.children.push(...children); }
    replaceChildren(...children) { this.children = children; }
    setAttribute(name, value) { this.attributes[name] = value; }
    addEventListener(name, callback) { this.listeners[name] = callback; }
  }
  const html = fs.readFileSync(require.resolve('../../demo/static/index.html'), 'utf8');
  const elements = new Map([...html.matchAll(/<([\w-]+)\b([^>]*\bid="([^"]+)"[^>]*)>/g)].map(match => {
    const node = new Element(match[1]);
    node.hidden = /\bhidden\b/.test(match[2]);
    node.disabled = /\bdisabled\b/.test(match[2]);
    return [match[3], node];
  }));
  const element = id => {
    assert(elements.has(id), 'Missing HTML element: ' + id);
    return elements.get(id);
  };
  let response = initial, scheduled;
  const calls = [];
  const storage = new Map(cached ? [['telemetry-lab:thermostat-v1', JSON.stringify(cached)]] : []);
  vm.runInNewContext(browserScript, {
    document: {getElementById: element, createElement: tag => new Element(tag),
      hidden: false, activeElement: null, addEventListener() {}},
    window: {
      localStorage: {getItem: key => storage.get(key) ?? null, setItem: (key, value) => {
        if (storageFails) throw Error('Storage unavailable');
        storage.set(key, value);
      }},
      fetch: async (url, options) => {
        calls.push({url, options});
        if (options.method === 'POST') {
          if (post) return post(url, options);
          assert.equal(url, '/thermal-simulations/session-1/settings');
          response = {...response, ...JSON.parse(options.body), settings_revision: response.settings_revision + 1};
        } else {
          if (get) { const result = get(url); if (result) return result; }
          assert.equal(url, '/thermal-simulations/current');
        }
        assert.equal(options.cache, 'no-store');
        if (response instanceof Error) throw response;
        return {ok: true, json: async () => structuredClone(response)};
      },
    },
    crypto: {randomUUID: () => 'operation-1'}, AbortSignal,
    setTimeout: callback => { scheduled = callback; return 1; },
    clearTimeout: () => { scheduled = null; },
  });
  return {
    element, calls, storage,
    click(mode) { return element('thermostat-' + mode).listeners.click(); },
    changeTarget(value) { element('thermostat-target').value = value; return element('thermostat-target').listeners.change(); },
    async settle() { await new Promise(resolve => setImmediate(resolve)); },
    async refresh(next, viaTimer = false) {
      response = next;
      if (viaTimer) { assert.equal(typeof scheduled, 'function'); await scheduled(); }
      else await element('thermostat-refresh').listeners.click();
    },
    nodes: kind => element('flow-' + kind).children,
  };
}
const pulses = nodes => nodes.map(node => node.className.split(' ').includes('flow-pulse'));
const dates = nodes => nodes.map(node => node.children.find(child => child.tagName === 'time')?.dateTime ?? null);
function sameNodes(actual, expected) {
  assert.equal(actual.length, expected.length);
  actual.forEach((node, index) => assert.equal(node, expected[index]));
}

test('browser adapter pulses only new application proof and preserves DOM through repeated or failed reads', async () => {
  const evidence = fixture();
  Object.assign(evidence, {policy: 'thermostat', status: 'active', runtime: {status: 'available'}});
  evidence.latest_command.status = 'pending';
  delete evidence.latest_command.applied_at;
  const browser = browserHarness(evidence);
  await browser.settle();
  assert.deepEqual(pulses(browser.nodes('reading')), [false, false, false, false]);
  assert.deepEqual(pulses(browser.nodes('command')), [false, false, false]);

  const applied = structuredClone(evidence);
  applied.latest_command.status = 'applied';
  applied.latest_command.applied_at = '2026-10-08T10:01:00Z';
  const originalReadings = [...browser.nodes('reading')];
  await browser.refresh(applied, true);
  sameNodes(browser.nodes('reading'), originalReadings);
  assert.deepEqual(pulses(browser.nodes('command')), [false, false, true]);
  const appliedNodes = [...browser.nodes('command')];
  const appliedDates = dates(appliedNodes);
  await browser.refresh(applied);
  sameNodes(browser.nodes('command'), appliedNodes);
  await browser.refresh(new Error('network unavailable'));
  sameNodes(browser.nodes('command'), appliedNodes);
  sameNodes(browser.nodes('reading'), originalReadings);
  assert.deepEqual(dates(browser.nodes('command')), appliedDates);
  assert.match(browser.element('flow-status').textContent, /incertaine.*preuves conservées/);

  const newReading = structuredClone(applied);
  newReading.latest_reading.sequence = 13;
  await browser.refresh(newReading);
  assert.deepEqual(pulses(browser.nodes('reading')), [true, true, true, true]);
  const newReadingNodes = [...browser.nodes('reading')];
  await browser.refresh(newReading);
  sameNodes(browser.nodes('reading'), newReadingNodes);
});

test('browser adapter cancellation does not pulse upstream command stages', async () => {
  const evidence = fixture();
  Object.assign(evidence, {policy: 'thermostat', status: 'active', runtime: {status: 'available'}});
  evidence.latest_command.status = 'pending';
  delete evidence.latest_command.applied_at;
  const browser = browserHarness(evidence);
  await browser.settle();
  const cancelled = structuredClone(evidence);
  cancelled.latest_command.status = 'cancelled';
  await browser.refresh(cancelled);
  assert.deepEqual(pulses(browser.nodes('command')), [false, false, false]);
  assert.equal(browser.nodes('command')[2].children[1].textContent, 'Commande annulée');
  assert.deepEqual(dates(browser.nodes('command')), [evidence.latest_command.created_at, evidence.latest_command.published_at, null]);
});

function thermostatEvidence(overrides = {}) {
  return {...fixture(), policy: 'thermostat', status: 'active', phase: 'acting',
    runtime: {status: 'available'}, target_c: 22, ...overrides};
}
function selected(browser, mode) {
  assert.equal(browser.element('thermostat-cooling').attributes['aria-pressed'], String(mode === 'cooling'));
  assert.equal(browser.element('thermostat-heating').attributes['aria-pressed'], String(mode === 'heating'));
}
function controlsDisabled(browser, disabled) {
  for (const name of ['cooling', 'heating', 'target']) assert.equal(browser.element('thermostat-' + name).disabled, disabled);
}
const posts = browser => browser.calls.filter(call => call.options.method === 'POST');

test('mode buttons initialize passively from requested mode independently of measured action', async () => {
  const browser = browserHarness(thermostatEvidence());
  selected(browser, null);
  controlsDisabled(browser, true);
  await browser.settle();
  selected(browser, 'cooling');
  controlsDisabled(browser, false);
  assert.match(browser.element('thermostat-applied').textContent, /chauffage actif/);
  assert.equal(posts(browser).length, 0);
  await browser.click('cooling');
  assert.equal(posts(browser).length, 0);
  await browser.refresh(thermostatEvidence({mode: 'heating', latest_reading: {...fixture().latest_reading, heater_on: false, cooler_on: true}}));
  selected(browser, 'heating');
  assert.match(browser.element('thermostat-applied').textContent, /refroidissement actif/);
});

for (const mode of ['heating', 'cooling']) test('direct ' + mode + ' click submits the requested mode and current target exactly once', async () => {
  const browser = browserHarness(thermostatEvidence({mode: mode === 'heating' ? 'cooling' : 'heating'}));
  await browser.settle();
  browser.element('thermostat-target').value = '25.5';
  await browser.click(mode);
  assert.deepEqual(JSON.parse(posts(browser)[0].options.body), {
    operation_id: 'operation-1', expected_revision: 4, mode, target_c: 25.5,
  });
  selected(browser, mode);
  await browser.click(mode);
  assert.equal(posts(browser).length, 1);
});

test('target change preserves requested cooling while heating remains measured', async () => {
  const browser = browserHarness(thermostatEvidence());
  await browser.settle();
  await browser.changeTarget('24.5');
  assert.deepEqual(JSON.parse(posts(browser)[0].options.body), {
    operation_id: 'operation-1', expected_revision: 4, mode: 'cooling', target_c: 24.5,
  });
});

for (const initial of [thermostatEvidence({phase: 'stop_pending'}), thermostatEvidence({runtime: {status: 'unavailable'}})]) {
  test('mode and target guards block settings during ' + (initial.phase === 'stop_pending' ? 'stop pending' : 'uncertainty'), async () => {
    const browser = browserHarness(initial);
    await browser.settle();
    controlsDisabled(browser, true);
    await browser.click('heating');
    await browser.click('cooling');
    await browser.changeTarget('24');
    assert.equal(posts(browser).length, 0);
    selected(browser, 'cooling');
  });
}

test('in-flight settings lock all controls and preserve requested selection until response', async () => {
  let release;
  const browser = browserHarness(thermostatEvidence(), {post: () => new Promise(resolve => { release = resolve; })});
  await browser.settle();
  const pending = browser.click('heating');
  controlsDisabled(browser, true);
  selected(browser, 'cooling');
  await browser.click('cooling');
  await browser.changeTarget('26');
  assert.equal(posts(browser).length, 1);
  release({ok: true, json: async () => thermostatEvidence({mode: 'heating', settings_revision: 5})});
  await pending;
  controlsDisabled(browser, false);
  selected(browser, 'heating');
});

const recoveryNames = ['refresh', 'resume', 'new', 'retry', 'abandon'];
function recoveryActions(browser) {
  return recoveryNames.filter(name => !browser.element('thermostat-' + name).hidden);
}
function recoveryText(browser) {
  return browser.element('thermostat-recovery-message').textContent;
}
function settingOperation(overrides = {}) {
  return {simulation_id: 'session-1', operation_id: 'saved-operation', expected_revision: 4,
    mode: 'heating', target_c: 24, ...overrides};
}
const missing = () => ({ok: false, status: 404, json: async () => ({})});

test('healthy page has no recovery panel or focusable recovery controls', async () => {
  const browser = browserHarness(thermostatEvidence());
  assert.equal(browser.element('thermostat-recovery').hidden, true);
  await browser.settle();
  assert.equal(browser.element('thermostat-recovery').hidden, true);
  assert.deepEqual(recoveryActions(browser), []);
  for (const name of recoveryNames) assert.equal(browser.element('thermostat-' + name).disabled, true);
});

test('interrupted session shows only explicit resume then disappears after recovery', async () => {
  const browser = browserHarness(thermostatEvidence({status: 'interrupted'}), {
    post: (url, options) => {
      assert.equal(url, '/thermal-simulations/session-1/resume');
      assert.equal(options.body, '{}');
      return {ok: true, json: async () => ({})};
    },
  });
  await browser.settle();
  assert.match(recoveryText(browser), /interrompue/);
  assert.deepEqual(recoveryActions(browser), ['resume']);
  assert.equal(posts(browser).length, 0);
  await browser.click('resume');
  assert.equal(posts(browser).length, 1);
  await browser.refresh(thermostatEvidence());
  assert.equal(browser.element('thermostat-recovery').hidden, true);
});

for (const status of ['completed', 'stopped', 'expired']) test(status + ' session offers a new simulation only', async () => {
  const browser = browserHarness(thermostatEvidence({status}));
  await browser.settle();
  assert.match(recoveryText(browser), /terminée|arrêtée|expiré/);
  assert.deepEqual(recoveryActions(browser), ['new']);
  assert.equal(browser.element('thermostat-new').textContent, 'Démarrer une nouvelle simulation');
  assert.equal(posts(browser).length, 0);
});

test('confirmed missing session offers creation but first network failure does not', async () => {
  const absent = browserHarness(null, {get: missing});
  await absent.settle();
  assert.deepEqual(recoveryActions(absent), ['new']);
  assert.equal(absent.element('thermostat-new').textContent, 'Démarrer une simulation');
  const failed = browserHarness(new Error('offline'));
  await failed.settle();
  assert.deepEqual(recoveryActions(failed), ['refresh']);
  assert.match(recoveryText(failed), /indisponible|impossible/);
  await failed.click('new'); // Even programmatic activation must be guarded.
  assert.equal(posts(failed).length, 0);
});

test('failed read retains evidence without offering creation from stale terminal state', async () => {
  const browser = browserHarness(thermostatEvidence({status: 'stopped'}));
  await browser.settle();
  await browser.refresh(new Error('offline'));
  assert.deepEqual(recoveryActions(browser), ['refresh']);
  assert.match(browser.element('thermostat-temperature').textContent, /21.5/);
  assert.match(recoveryText(browser), /conserv/);
  await browser.click('new');
  assert.equal(posts(browser).length, 0);
  await browser.refresh(thermostatEvidence());
  assert.equal(browser.element('thermostat-recovery').hidden, true);
});

test('unavailable runtime blocks resume and offers only a passive read', async () => {
  const browser = browserHarness(thermostatEvidence({status: 'interrupted', runtime: {status: 'unavailable'}}));
  await browser.settle();
  assert.deepEqual(recoveryActions(browser), ['refresh']);
  controlsDisabled(browser, true);
  await browser.click('resume');
  assert.equal(posts(browser).length, 0);
});

test('cached uncertain setting offers a checked exact retry and resolves by passive lookup', async () => {
  const operation = settingOperation();
  let found = false;
  const browser = browserHarness(thermostatEvidence(), {
    cached: {operation},
    get: url => url.endsWith('/settings/saved-operation') ?
      found ? {ok: true, json: async () => ({operation_id: 'saved-operation'})} : missing() : null,
    post: (url, options) => { assert.equal(url, '/thermal-simulations/session-1/settings');
      assert.deepEqual(JSON.parse(options.body), {operation_id: operation.operation_id,
        expected_revision: 4, mode: 'heating', target_c: 24}); throw Error('lost response'); },
  });
  await browser.settle();
  assert.equal(posts(browser).length, 0);
  assert.deepEqual(recoveryActions(browser), ['refresh', 'retry']);
  assert.equal(browser.element('thermostat-refresh').textContent, 'Vérifier la demande');
  await browser.click('retry');
  assert.equal(posts(browser).length, 1);
  found = true;
  await browser.refresh(thermostatEvidence());
  assert.equal(browser.element('thermostat-recovery').hidden, true);
});

test('rejected request closes locally without undoing any server setting', async () => {
  const browser = browserHarness(thermostatEvidence(), {
    cached: {operation: settingOperation({rejected: true})},
    get: url => url.includes('/settings/') ? missing() : null,
  });
  await browser.settle();
  assert.deepEqual(recoveryActions(browser), ['refresh', 'abandon']);
  assert.match(recoveryText(browser), /conflit|rejet/);
  assert.match(recoveryText(browser), /serveur/);
  await browser.click('abandon');
  assert.equal(JSON.parse(browser.storage.get('telemetry-lab:thermostat-v1')).operation, null);
  assert.equal(posts(browser).length, 0);
  await browser.refresh(thermostatEvidence());
  assert.equal(browser.element('thermostat-recovery').hidden, true);
});

test('storage failure explains blocked settings without a fake repair action', async () => {
  const browser = browserHarness(thermostatEvidence(), {storageFails: true});
  await browser.settle();
  assert.match(recoveryText(browser), /stockage.*indisponible/i);
  assert.match(recoveryText(browser), /bloqu/);
  assert.deepEqual(recoveryActions(browser), []);
  controlsDisabled(browser, true);
  await browser.changeTarget('25');
  assert.equal(posts(browser).length, 0);
});

test('cached old stop survives reload and explicitly retries the same session action', async () => {
  const pendingAction = {kind: 'stop', simulation_id: 'session-1', predecessor_id: 'session-1', prior_status: 'active'};
  const browser = browserHarness(thermostatEvidence(), {
    cached: {pendingAction},
    post: (url, options) => { assert.equal(url, '/thermal-simulations/session-1/stop');
      assert.equal(options.body, '{}'); return {ok: true, json: async () => ({})}; },
  });
  await browser.settle();
  assert.equal(posts(browser).length, 0);
  assert.deepEqual(recoveryActions(browser), ['refresh', 'retry']);
  await browser.click('retry');
  assert.equal(posts(browser).length, 1);
  await browser.refresh(thermostatEvidence({status: 'stopped'}));
  assert.deepEqual(recoveryActions(browser), ['new']);
});

test('polling keeps recovery controls visible but disabled until the read completes', async () => {
  let delayed = false, release;
  const proof = thermostatEvidence({status: 'interrupted'});
  const browser = browserHarness(proof, {get: () => delayed ? new Promise(resolve => { release = resolve; }) : null});
  await browser.settle();
  assert.deepEqual(recoveryActions(browser), ['resume']);
  delayed = true;
  const pending = browser.refresh(proof, true);
  assert.deepEqual(recoveryActions(browser), ['resume']);
  assert.equal(browser.element('thermostat-resume').disabled, true);
  await browser.click('resume');
  assert.equal(posts(browser).length, 0);
  release({ok: true, json: async () => proof});
  await pending;
  assert.equal(browser.element('thermostat-resume').disabled, false);
});

test('stale revision of a missing request can receive a rejection and be closed', async () => {
  const proof = thermostatEvidence({settings_revision: 5});
  const browser = browserHarness(proof, {
    cached: {operation: settingOperation()},
    get: url => url.includes('/settings/') ? missing() : null,
    post: (url, options) => {
      assert.equal(JSON.parse(options.body).expected_revision, 4);
      return {ok: false, status: 409};
    },
  });
  await browser.settle();
  assert.deepEqual(recoveryActions(browser), ['refresh', 'retry']);
  await browser.click('retry');
  assert.deepEqual(recoveryActions(browser), ['refresh', 'abandon']);
  await browser.click('abandon');
  await browser.refresh(proof);
  assert.equal(browser.element('thermostat-recovery').hidden, true);
});

test('a cached missing request cannot be retried against a different current session', async () => {
  const browser = browserHarness(thermostatEvidence({simulation_id: 'different'}), {
    cached: {operation: settingOperation({missing: true})},
  });
  await browser.settle();
  assert.deepEqual(recoveryActions(browser), ['refresh']);
  await browser.click('retry');
  assert.equal(posts(browser).length, 0);
});
