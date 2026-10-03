// Node test without Home Assistant: node tests/card.test.cjs
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname, '../custom_components/wake_word_collector/frontend/wake-word-collector-card.js'), 'utf8');
const registry = new Map();
const window = {};
const context = vm.createContext({
  window, confirm: () => true, Date, Promise, Map, Math, Number, String, Array, setTimeout,
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
  console.log('card ok');
})().catch(error => { console.error(error); process.exitCode = 1; });
