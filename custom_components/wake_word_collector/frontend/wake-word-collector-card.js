/* Wake Word Collector: listen to, accept, reject and trim collected recordings.
   Served by the integration and loaded into every dashboard (type: custom:wake-word-collector-card).
   Texts in English and German, chosen by the user's Home Assistant language. */
(() => {
const I18N = {
  en: {
    title: 'Wake word recordings', loading: 'Loading recordings …', none: 'Wake Word Collector is not set up.',
    empty: 'No recordings in this view.', all: 'All', review: 'To check', candidates: 'Usable',
    usable: '{n} usable', to_check: '{n} to check', total: '{n} in total',
    cat_candidates: 'usable', cat_needs_review: 'to check', cat_rejected_transcript: 'to check',
    cat_triggers: 'activation', cat_negatives: 'not the wake word', cat_control: 'command',
    negatives: 'Not the wake word', triggers: 'Activations', triggers_count: '{n} activations to judge',
    negative: 'Not the wake word', false_alarm: 'False alarm', was_wake_word: 'Was the wake word',
    play: 'Play', accept: 'Accept', reject: 'Reject', edit: 'Edit', close: 'Close', more: 'Show more',
    confirm_reject: 'Reject this recording? It is kept and can be restored from the storage folder.',
    keep: 'Keep selection', remove: 'Delete selection', extract: 'Save selection as new recording',
    play_selection: 'Play selection', start: 'Start', end: 'End', selection: 'Selection {from}–{to} s ({len} s)',
    min_length: 'At least 0.5 s must remain.', done: 'Done.', heard: 'Heard: “{text}”', refresh: 'Refresh',
  },
  de: {
    title: 'Wakeword-Aufnahmen', loading: 'Aufnahmen werden geladen …', none: 'Wake Word Collector ist nicht eingerichtet.',
    empty: 'Keine Aufnahmen in dieser Ansicht.', all: 'Alle', review: 'Zu prüfen', candidates: 'Verwendbar',
    usable: '{n} verwendbar', to_check: '{n} zu prüfen', total: '{n} insgesamt',
    cat_candidates: 'verwendbar', cat_needs_review: 'zu prüfen', cat_rejected_transcript: 'zu prüfen',
    cat_triggers: 'Auslösung', cat_negatives: 'kein Aktivierungswort', cat_control: 'Befehl',
    negatives: 'Kein Aktivierungswort', triggers: 'Auslösungen', triggers_count: '{n} Auslösungen zu bewerten',
    negative: 'Kein Aktivierungswort', false_alarm: 'Fehlalarm', was_wake_word: 'War das Aktivierungswort',
    play: 'Abspielen', accept: 'Annehmen', reject: 'Verwerfen', edit: 'Bearbeiten', close: 'Schließen', more: 'Mehr anzeigen',
    confirm_reject: 'Diese Aufnahme verwerfen? Sie bleibt erhalten und lässt sich im Speicherordner wiederherstellen.',
    keep: 'Auswahl behalten', remove: 'Auswahl löschen', extract: 'Auswahl als neue Aufnahme speichern',
    play_selection: 'Auswahl abspielen', start: 'Anfang', end: 'Ende', selection: 'Auswahl {from}–{to} s ({len} s)',
    min_length: 'Es müssen mindestens 0,5 s übrig bleiben.', done: 'Erledigt.', heard: 'Erkannt: „{text}“', refresh: 'Aktualisieren',
  },
};
const escapeHtml = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c]));
function tr(lang, key, vars = {}) {
  const text = I18N[lang]?.[key] ?? I18N.en[key] ?? key;
  return text.replace(/\{(\w+)\}/g, (match, name) => vars[name] ?? '');
}

class WakeWordCollectorCard extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({mode: 'open'});
    this.collectors = null;
    this.filter = 'all';
    this.limit = 30;
    this.message = '';
    this.busy = false;
    this.editing = null; // {key, item, peaks, duration, start, end}
    this.audioUrls = {};
    this.refreshTimer = null;
    this.audio = null;
  }
  setConfig(config) { this.config = config || {}; }
  static getStubConfig() { return {}; }
  getCardSize() { return 8; }
  locale() { return this._hass?.locale?.language || this._hass?.language || 'en'; }
  lang() { return String(this.locale()).toLowerCase().startsWith('de') ? 'de' : 'en'; }
  t(key, vars) { return tr(this.lang(), key, vars); }
  num(value, digits = 1) { return Number(value).toLocaleString(this.locale(), {minimumFractionDigits: digits, maximumFractionDigits: digits}); }
  set hass(hass) {
    const first = !this._hass;
    this._hass = hass;
    const sensors = Object.entries(hass.states || {}).filter(([id, state]) =>
      this.config?.recordings_entity ? id === this.config.recordings_entity :
        id.startsWith('sensor.') && state.attributes?.candidates_by_device !== undefined);
    const fingerprint = JSON.stringify(sensors.map(([id, state]) => [id, state.state, state.attributes]));
    const changed = fingerprint !== this.fingerprint;
    this.fingerprint = fingerprint;
    if (first) this.load();
    else if (changed) {
      clearTimeout(this.refreshTimer);
      this.refreshTimer = setTimeout(() => { if (!this.busy && !this.editing) this.load(); }, 300);
    }
  }
  async load() {
    try {
      const msg = {type: 'wake_word_collector/list'};
      if (this.config?.entry_id) msg.entry_id = this.config.entry_id;
      const result = await this._hass.callWS(msg);
      this.collectors = result.collectors;
    } catch (error) {
      this.collectors = [];
      this.message = error.message || String(error);
    }
    this.render();
  }
  collector() { return this.collectors?.[0]; }
  key(item) { return `${item.category}/${item.device}/${item.filename}`; }
  items() {
    const items = this.collector()?.items || [];
    const groups = {
      review: ['needs_review', 'rejected_transcript', 'triggers'],
      candidates: ['candidates'],
      triggers: ['triggers'],
      negatives: ['negatives', 'control'],
    };
    return groups[this.filter] ? items.filter(i => groups[this.filter].includes(i.category)) : items;
  }
  async signed(item) {
    const key = this.key(item);
    const cached = this.audioUrls[key];
    if (cached && cached.expires > Date.now()) return cached.url;
    const result = await this._hass.callWS({type: 'auth/sign_path', path: item.audio_path, expires: 600});
    this.audioUrls[key] = {url: result.path, expires: Date.now() + 500000};
    return result.path;
  }
  async play(item) {
    try {
      this.audio?.pause();
      this.audio = new Audio(await this.signed(item));
      await this.audio.play();
    } catch (error) {
      this.message = error.message || String(error);
      this.render();
    }
  }
  disconnectedCallback() {
    clearTimeout(this.refreshTimer);
    this.audio?.pause();
  }
  async service(name, data) {
    this.busy = true;
    this.render();
    try {
      const call = {...data};
      if (this.collector()?.entry_id) call.config_entry_id = this.collector().entry_id;
      await this._hass.callService('wake_word_collector', name, call);
      this.message = this.t('done');
      return true;
    } catch (error) {
      this.message = error.message || String(error);
      return false;
    } finally {
      this.busy = false;
      await this.load();
    }
  }
  async review(item, decision) {
    if (decision === 'reject' && !confirm(this.t('confirm_reject'))) return;
    await this.service('review', {category: item.category, device: item.device, filename: item.filename, decision});
  }
  peaks(data, bars = 300) {
    const size = Math.max(1, Math.floor(data.length / bars));
    return Array.from({length: bars}, (_, i) => {
      let peak = 0;
      for (let j = i * size; j < Math.min(data.length, (i + 1) * size); j++) peak = Math.max(peak, Math.abs(data[j]));
      return peak;
    });
  }
  async edit(item) {
    const key = this.key(item);
    if (this.editing?.key === key) { this.editing = null; this.render(); return; }
    const response = await fetch(await this.signed(item));
    const buffer = await response.arrayBuffer();
    const context = new (window.AudioContext || window.webkitAudioContext)();
    const decoded = await context.decodeAudioData(buffer);
    context.close?.();
    this.editing = {key, item, peaks: this.peaks(decoded.getChannelData(0)), duration: decoded.duration, start: 0, end: decoded.duration};
    this.render();
  }
  async playSelection() {
    const e = this.editing;
    if (!e) return;
    const audio = new Audio(await this.signed(e.item));
    audio.currentTime = e.start;
    const stop = () => { if (audio.currentTime >= e.end) { audio.pause(); audio.removeEventListener('timeupdate', stop); } };
    audio.addEventListener('timeupdate', stop);
    this.audio?.pause();
    this.audio = audio;
    await audio.play();
  }
  async trim(mode) {
    const e = this.editing;
    if (!e) return;
    const selected = e.end - e.start, rest = e.duration - selected;
    if ((mode === 'keep' && selected < 0.5) || (mode === 'remove' && rest < 0.5) || (mode === 'extract' && (selected < 0.5 || rest < 0.5))) {
      this.message = this.t('min_length');
      this.render();
      return;
    }
    const item = e.item;
    const ok = await this.service('trim', {category: item.category, device: item.device, filename: item.filename,
      start_ms: Math.round(e.start * 1000), end_ms: Math.round(e.end * 1000), mode});
    if (ok) {
      delete this.audioUrls[e.key];
      this.editing = null;
      this.render();
    }
  }
  waveform() {
    const e = this.editing, width = 600, height = 90;
    const bars = e.peaks.map((peak, i) => {
      const x = i * width / e.peaks.length, h = Math.max(1, peak * height);
      const t = i / e.peaks.length * e.duration, inside = t >= e.start && t <= e.end;
      return `<rect x="${x.toFixed(1)}" y="${((height - h) / 2).toFixed(1)}" width="${(width / e.peaks.length * 0.8).toFixed(2)}" height="${h.toFixed(1)}" fill="${inside ? 'var(--primary-color)' : 'var(--disabled-text-color, #aaa)'}"/>`;
    }).join('');
    return `<svg class="wave" viewBox="0 0 ${width} ${height}" preserveAspectRatio="none">${bars}</svg>`;
  }
  selectionText() {
    const e = this.editing;
    return this.t('selection', {from: this.num(e.start, 2), to: this.num(e.end, 2), len: this.num(e.end - e.start, 2)});
  }
  editor() {
    const e = this.editing, d = e.duration, disabled = this.busy ? 'disabled' : '';
    return `<div class="editor">${this.waveform()}
      <label>${escapeHtml(this.t('start'))}<input type="range" data-edge="start" min="0" max="${d}" step="0.01" value="${e.start}"></label>
      <label>${escapeHtml(this.t('end'))}<input type="range" data-edge="end" min="0" max="${d}" step="0.01" value="${e.end}"></label>
      <div class="muted" data-selection>${escapeHtml(this.selectionText())}</div>
      <div class="actions"><button data-action="play-selection">${escapeHtml(this.t('play_selection'))}</button>
      <button data-trim="keep" ${disabled}>${escapeHtml(this.t('keep'))}</button>
      <button data-trim="remove" ${disabled}>${escapeHtml(this.t('remove'))}</button>
      <button data-trim="extract" ${disabled}>${escapeHtml(this.t('extract'))}</button></div></div>`;
  }
  row(item) {
    const key = this.key(item), editing = this.editing?.key === key, disabled = this.busy ? 'disabled' : '';
    const when = new Date(item.created_at).toLocaleString(this.locale());
    const length = item.duration_ms != null ? ` · ${this.num(item.duration_ms / 1000)} s` : '';
    return `<div class="item ${item.category === 'candidates' ? '' : 'check'}" data-key="${escapeHtml(key)}">
      <div class="head"><strong>${escapeHtml(when)}</strong><span class="badge">${escapeHtml(this.t('cat_' + item.category))}</span></div>
      <div class="muted">${escapeHtml((this.config?.device_names?.[item.device] || item.device) + length)}</div>
      ${item.transcript ? `<div class="transcript">${escapeHtml(this.t('heard', {text: item.transcript}))}</div>` : ''}
      ${item.note ? `<div class="muted">${escapeHtml(item.note)}</div>` : ''}
      <div class="actions"><button data-action="play">${escapeHtml(this.t('play'))}</button>
      ${item.category !== 'candidates' ? `<button data-action="accept" ${disabled}>${escapeHtml(this.t(item.category === 'triggers' ? 'was_wake_word' : 'accept'))}</button>` : ''}
      ${item.category !== 'negatives' ? `<button data-action="negative" ${disabled}>${escapeHtml(this.t(item.category === 'triggers' ? 'false_alarm' : 'negative'))}</button>` : ''}
      <button data-action="reject" class="danger" ${disabled}>${escapeHtml(this.t('reject'))}</button>
      <button data-action="edit">${escapeHtml(this.t(editing ? 'close' : 'edit'))}</button></div>
      ${editing ? this.editor() : ''}</div>`;
  }
  render() {
    const style = `<style>
      ha-card{display:block;padding:16px}h2{margin:0 0 4px;font-size:20px}.muted{color:var(--secondary-text-color);font-size:13px}
      .toolbar{display:flex;flex-wrap:wrap;gap:8px;align-items:center;margin:12px 0}
      button,select{font:inherit;color:var(--primary-text-color);background:var(--card-background-color);border:1px solid var(--divider-color);border-radius:10px;padding:8px 10px;min-height:40px;cursor:pointer}
      button:disabled{opacity:.5}button.danger{color:var(--error-color)}
      .item{border:1px solid var(--divider-color);border-radius:12px;padding:10px 12px;margin:8px 0}.item.check{border-left:4px solid var(--warning-color,#e6a100)}
      .head{display:flex;justify-content:space-between;gap:8px;align-items:center}.badge{font-size:12px;padding:2px 8px;border-radius:10px;background:var(--secondary-background-color)}
      .transcript{margin:4px 0;font-size:14px}.actions{display:flex;flex-wrap:wrap;gap:6px;margin-top:8px}
      .editor{margin-top:10px}.wave{width:100%;height:90px;display:block;background:var(--secondary-background-color);border-radius:8px}
      label{display:block;font-size:13px;margin-top:6px}input[type=range]{width:100%}.notice{padding:8px 10px;border-left:3px solid var(--primary-color);margin:8px 0;font-size:13px}
    </style>`;
    if (this.collectors === null) { this.shadowRoot.innerHTML = `${style}<ha-card><p>${escapeHtml(this.t('loading'))}</p></ha-card>`; return; }
    const collector = this.collector();
    if (!collector) { this.shadowRoot.innerHTML = `${style}<ha-card><p>${escapeHtml(this.message || this.t('none'))}</p></ha-card>`; return; }
    const stats = collector.stats || {}, items = this.items();
    const parts = [this.t('usable', {n: stats.candidates ?? 0}), this.t('to_check', {n: stats.needs_review ?? 0})];
    if (stats.triggers) parts.push(this.t('triggers_count', {n: stats.triggers}));
    parts.push(this.t('total', {n: stats.total ?? 0}));
    const summary = parts.join(' · ');
    this.shadowRoot.innerHTML = `${style}<ha-card>
      <h2>${escapeHtml(this.config?.title || `${this.t('title')}: ${collector.title}`)}</h2>
      <div class="muted">${escapeHtml(summary)}</div>
      <div class="toolbar"><select data-filter>${['all', 'review', 'candidates', 'triggers', 'negatives'].map(f => `<option value="${f}" ${f === this.filter ? 'selected' : ''}>${escapeHtml(this.t(f))}</option>`).join('')}</select>
      <button data-action="refresh">${escapeHtml(this.t('refresh'))}</button></div>
      ${this.message ? `<div class="notice" role="status">${escapeHtml(this.message)}</div>` : ''}
      ${items.length ? items.slice(0, this.limit).map(item => this.row(item)).join('') : `<p class="muted">${escapeHtml(this.t('empty'))}</p>`}
      ${items.length > this.limit ? `<button data-action="more">${escapeHtml(this.t('more'))}</button>` : ''}
    </ha-card>`;
    this.bind(items);
  }
  bind(items) {
    const root = this.shadowRoot, byKey = new Map(items.map(item => [this.key(item), item]));
    root.querySelector('[data-filter]').onchange = event => { this.filter = event.target.value; this.limit = 30; this.render(); };
    root.querySelector('[data-action="refresh"]').onclick = () => this.load();
    const more = root.querySelector('[data-action="more"]');
    if (more) more.onclick = () => { this.limit += 30; this.render(); };
    root.querySelectorAll('.item').forEach(row => {
      const item = byKey.get(row.dataset.key);
      if (!item) return;
      row.querySelector('[data-action="play"]').onclick = () => this.play(item);
      row.querySelector('[data-action="accept"]')?.addEventListener('click', () => this.review(item, 'accept'));
      row.querySelector('[data-action="negative"]')?.addEventListener('click', () => this.review(item, 'negative'));
      row.querySelector('[data-action="reject"]').onclick = () => this.review(item, 'reject');
      row.querySelector('[data-action="edit"]').onclick = () => this.edit(item).catch(error => { this.message = error.message || String(error); this.render(); });
      row.querySelector('[data-action="play-selection"]')?.addEventListener('click', () => this.playSelection().catch(error => { this.message = error.message || String(error); this.render(); }));
      row.querySelectorAll('[data-trim]').forEach(button => { button.onclick = () => this.trim(button.dataset.trim); });
      row.querySelectorAll('[data-edge]').forEach(input => {
        input.oninput = () => {
          const e = this.editing, value = Number(input.value);
          if (input.dataset.edge === 'start') e.start = Math.min(value, e.end - 0.05); else e.end = Math.max(value, e.start + 0.05);
          row.querySelector('.wave').outerHTML = this.waveform();
          row.querySelector('[data-selection]').textContent = this.selectionText();
        };
      });
    });
  }
}

if (!customElements.get('wake-word-collector-card')) customElements.define('wake-word-collector-card', WakeWordCollectorCard);
window.customCards = window.customCards || [];
if (!window.customCards.some(card => card.type === 'wake-word-collector-card')) {
  window.customCards.push({type: 'wake-word-collector-card', name: 'Wake Word Collector', description: 'Listen to, accept, reject and trim wake word recordings.'});
}
})();
