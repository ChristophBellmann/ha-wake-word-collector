// Node test without Home Assistant: node tests/card.test.cjs
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname, '../custom_components/wake_word_collector/frontend/wake-word-collector-card.js'), 'utf8');
const registry = new Map();
const window = {};
const context = vm.createContext({
  window, confirm: () => true, Date, Promise, Map, Math, Number, String, Array, setTimeout, clearTimeout,
  requestAnimationFrame: () => 0, cancelAnimationFrame: () => {},
  Audio: class { addEventListener() {} pause() {} async play() { throw new Error("Playback failed"); } },
  HTMLElement: class { attachShadow() { this.shadowRoot = {innerHTML: '', querySelector: () => null, querySelectorAll: () => []}; } },
  customElements: {get: n => registry.get(n), define: (n, c) => registry.set(n, c)},
});
vm.runInContext(source, context);
vm.runInContext(source, context);
assert.equal(registry.size, 1);
assert.equal(window.customCards.length, 1);
const Card = registry.get('wake-word-collector-card');

const items = [
  {category: 'needs_review', device: 'kitchen', filename: 'ha_kitchen_20261003T120000_aaaaaaaaaaaa.wav', created_at: '2026-10-03T12:00:00Z', transcript: 'Hey Novi', duration_ms: 1500, audio_path: '/a'},
  {category: 'candidates', device: 'bath', filename: 'ha_bath_20261003T110000_bbbbbbbbbbbb.wav', created_at: '2026-10-03T11:00:00Z', transcript: 'Hey Nova', duration_ms: 1200, audio_path: '/b'},
];
const calls = [];
function card(language) {
  const c = new Card();
  c.setConfig({});
  c.bind = () => {};
  c._hass = {language, callWS: async () => ({collectors: [{entry_id: 'e1', title: 'Hey Nova', stats: {candidates: 1, needs_review: 1, total: 2}, items}]}),
    callService: async (...args) => calls.push(args)};
  return c;
}

(async () => {
  const de = card('de');
  await de.load();
  let html = de.shadowRoot.innerHTML;
  assert.ok(html.includes('Wakeword-Aufnahmen: Hey Nova'));
  assert.ok(html.includes('1 verwendbar · 1 zu prüfen · 2 insgesamt'));
  assert.ok(html.includes('Erkannt: „Hey Novi“'));
  assert.ok(html.includes('1,5 s'));
  assert.equal((html.match(/data-action="accept"/g) || []).length, 1, 'Only recordings to check can be accepted.');
  de.filter = 'review';
  de.render();
  assert.ok(!de.shadowRoot.innerHTML.includes('ha_bath'));

  // A reported activation: judged as the wake word or as a false alarm.
  const trigger = {category: 'triggers', device: 'kitchen', filename: 'ha_kitchen_20261003T130000_cccccccccccc.wav', created_at: '2026-10-03T13:00:00Z', transcript: '', duration_ms: 3000, audio_path: '/c'};
  items.push(trigger);
  de.collectors[0].stats.triggers = 1;
  de.filter = 'triggers';
  de.render();
  html = de.shadowRoot.innerHTML;
  assert.ok(html.includes('War das Aktivierungswort') && html.includes('Fehlalarm') && html.includes('Auslösung'));
  assert.ok(!html.includes('ha_kitchen_20261003T120000'), 'Only activations in this view.');
  await de.review(trigger, 'negative');
  assert.equal(calls.at(-1)[2].decision, 'negative');
  assert.equal(calls.at(-1)[2].category, 'triggers');
  de.collectors[0].stats.triggers = 1;  // reloaded after the decision
  de.filter = 'all';
  de.render();
  assert.ok(de.shadowRoot.innerHTML.includes('1 Auslösungen zu bewerten'));
  items.pop();

  const en = card('fr');
  await en.load();
  assert.ok(en.shadowRoot.innerHTML.includes('1 usable · 1 to check · 2 in total'));
  await en.review(items[0], 'accept');
  assert.deepEqual(JSON.parse(JSON.stringify(calls.at(-1))), ['wake_word_collector', 'review', {category: 'needs_review', device: 'kitchen', filename: items[0].filename, decision: 'accept', config_entry_id: 'e1'}]);

  // Trimming refuses to leave less than 0.5 s and sends milliseconds.
  en.editing = {key: 'k', item: items[0], peaks: [0.1, 0.5], duration: 1.5, start: 0.2, end: 0.6};
  await en.trim('keep');
  assert.equal(en.message, 'At least 0.5 s must remain.');
  en.editing = {key: 'k', item: items[0], peaks: [0.1, 0.5], duration: 1.5, start: 0.2, end: 0.9};
  await en.trim('keep');
  assert.equal(calls.at(-1)[1], 'trim');
  assert.equal(calls.at(-1)[2].start_ms, 200);
  assert.equal(calls.at(-1)[2].end_ms, 900);
  assert.equal(en.editing, null);
  assert.deepEqual(JSON.parse(JSON.stringify(en.peaks([0, 0.5, -1, 0.25], 2))), [0.5, 1]);
  de.setConfig({device_names: {kitchen: 'Büro'}, recordings_entity: 'sensor.test_recordings'});
  de.render();
  assert.ok(de.shadowRoot.innerHTML.includes('Büro'));
  de.signed = async () => '/signed';
  await de.play(items[0]);
  assert.equal(de.message, 'Playback failed');
  assert.equal(de.playing, null, 'A failed start leaves nothing marked as playing.');

  // While playing: the row is marked, the button stops, the waveform has a moving playhead.
  const playable = class {
    constructor() { this.currentTime = 0; this.listeners = {}; }
    addEventListener(name, fn) { this.listeners[name] = fn; }
    pause() { this.paused = true; }
    async play() {}
  };
  const key0 = de.key(items[0]);
  de.waves[key0] = {peaks: [0.002, 0.01, 0.004, 0.001], duration: 2, scale: 50};
  de.shown = key0;
  const audio = new playable();
  await de.startPlayback(key0, audio);
  audio.currentTime = 1;
  let row = de.row(items[0]);
  assert.ok(row.includes('class="item check playing"'));
  assert.ok(row.includes('aria-pressed="true"') && row.includes('>Stopp<'));
  assert.ok(row.includes('class="playhead" x1="300.0"'), 'Playhead halfway at 1 of 2 s.');
  assert.ok(row.includes('Leise Aufnahme: Wellenform 50-fach vergrößert dargestellt'));
  audio.listeners.ended();
  assert.equal(de.playing, null);
  assert.ok(audio.paused);
  row = de.row(items[0]);
  assert.ok(row.includes('>Abspielen<') && !row.includes('class="playhead"'), 'After the end: waveform stays, playhead gone.');
  assert.ok(row.includes('class="wave"'));
  let refreshes = 0;
  de.load = async () => { refreshes++; };
  const state = {state: '1', attributes: {candidates_by_device: {kitchen: 1}}};
  de.hass = {...de._hass, states: {'sensor.test_recordings': state}};
  await new Promise(resolve => setTimeout(resolve, 350));
  assert.equal(refreshes, 1);
  de.hass = {...de._hass, states: {'sensor.test_recordings': JSON.parse(JSON.stringify(state))}};
  await new Promise(resolve => setTimeout(resolve, 350));
  assert.equal(refreshes, 1, 'Unchanged statistics do not cause a refresh loop.');
  de.hass = {...de._hass, states: {'sensor.test_recordings': {...state, state: '2'}}};
  await new Promise(resolve => setTimeout(resolve, 350));
  assert.equal(refreshes, 2, 'New recordings refresh without a manual click.');
  de.disconnectedCallback();
  const quiet = {...items[0], category: 'rejected_quality', quality_reasons: ['too_quiet']};
  assert.ok(de.row(quiet).includes('Zu leise'));
  assert.ok(de.row(quiet).includes('data-action="play"'));
  assert.ok(de.row(quiet).includes('data-action="accept"'));
  assert.ok(!de.row(quiet).includes('data-action="reject"'));
  const c = de.collector(); c.has_trainer = true; c.auto_extract = true;
  const manual = {...items[0], kind: 'manual', extraction_state: 'done', extraction_count: 3};
  assert.ok(de.row(manual).includes('Aktivierungswörter ausschneiden'));
  assert.ok(de.row(manual).includes('3 Clips ausgeschnitten'));
  c.items = [manual]; de.filter = 'all'; de.render();
  assert.ok(de.shadowRoot.innerHTML.includes('data-auto-extract checked'));
  assert.ok(de.row({...manual, extraction_state: 'error', extraction_error: '<offline>'}).includes('&lt;offline&gt;'));
  await de.service('extract', {category: manual.category, device: manual.device, filename: manual.filename});
  assert.equal(calls.at(-1)[1], 'extract');
  console.log('card ok');
})().catch(error => { console.error(error); process.exitCode = 1; });
