import { Button, Input, Textarea, SETTINGS_PLUGINS_AREA } from '@hermes/plugin-sdk';
import { useEffect, useRef, useState } from 'react';
import { jsx } from 'react/jsx-runtime';

const fields = ['name', 'language', 'gender', 'voice_id'];
const emptySlot = () => ({ name: '', language: '', gender: '', voice_id: '' });
const uuid = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const same = (a, b) => fields.every(key => a[key] === b[key]);
const validVoice = row => row.name.trim() && row.language.trim() && ['male', 'female'].includes(row.gender) && uuid.test(row.voice_id);

export function validateFavorites(rows) {
  if (!Array.isArray(rows) || rows.length !== 5) throw new Error('Exactly five slots are required.');
  const seen = new Set();
  return rows.map((row, index) => {
    if (!row || fields.some(key => typeof row[key] !== 'string') || Object.keys(row).some(key => !fields.includes(key))) {
      throw new Error(`Slot ${index + 1}: use only name, language, gender and voice_id strings.`);
    }
    const copy = Object.fromEntries(fields.map(key => [key, row[key]]));
    if (fields.every(key => copy[key] === '')) return copy;
    if (!validVoice(copy)) throw new Error(`Slot ${index + 1}: fill all fields, choose male/female, and enter a UUID voice ID, or leave the entire slot empty.`);
    if (seen.has(copy.voice_id)) throw new Error('Voice IDs must be unique.');
    seen.add(copy.voice_id);
    return copy;
  });
}

// No public Settings-scope atom is exported by the SDK. Requests deliberately
// use ctx.rest's ambient gateway scope, never host.state.profile (launch home).
// IMPORTANT: the installed SDK does NOT forward Settings' Applies to override.
// See SCOPE-COMPATIBILITY.md. A preflight + expected_profile guards gateway
// profile changes only; it cannot observe the unsupported Settings override.
export class FavoritesModel {
  constructor(ctx, changed = () => {}) {
    this.ctx = ctx; this.changed = changed; this.generation = 0;
    this.saved = null; this.rows = Array.from({ length: 5 }, emptySlot);
    this.busy = false; this.error = ''; this.message = '';
  }
  emit() { this.changed(); }
  dispose() { this.generation++; this.changed = () => {}; }
  accept(data) {
    const rows = validateFavorites(data.favorites);
    if (typeof data.profile !== 'string' || !data.profile || !data.selection || data.catalog_scope !== 'shared-local-user') throw new Error('Unexpected backend state; Reload required.');
    this.saved = { ...data, favorites: rows };
    this.rows = rows.map(row => ({ ...row }));
  }
  async reload() {
    const generation = ++this.generation;
    this.saved = null; this.busy = true; this.error = ''; this.message = ''; this.emit();
    try {
      const data = await this.ctx.rest('/favorites');
      if (generation === this.generation) this.accept(data);
    } catch (error) { if (generation === this.generation) this.error = String(error.message || error); }
    finally { if (generation === this.generation) { this.busy = false; this.emit(); } }
  }
  edit(index, field, value) {
    if (this.busy) return;
    this.rows = this.rows.map((row, i) => i === index ? { ...row, [field]: value } : row);
    this.error = ''; this.message = ''; this.emit();
  }
  importJSON(text) {
    if (this.busy) return;
    try { this.rows = validateFavorites(JSON.parse(text)); this.error = ''; this.message = 'Imported draft. Save favorites to persist.'; }
    catch (error) { this.error = String(error.message || error); }
    this.emit();
  }
  selectable(index) {
    return !this.busy && this.saved && validVoice(this.rows[index]) && same(this.rows[index], this.saved.favorites[index]);
  }
  async write(path, body) {
    if (!this.saved || this.busy) return;
    const expected_profile = this.saved.profile;
    const generation = ++this.generation;
    this.busy = true; this.error = ''; this.message = ''; this.emit();
    try {
      const current = await this.ctx.rest('/favorites');
      if (generation !== this.generation) return;
      if (current.profile !== expected_profile) throw new Error('Settings profile changed. Reload required before saving or selecting.');
      const result = await this.ctx.rest(path, { method: path === '/favorites' ? 'PUT' : 'POST', body: { ...body, expected_profile } });
      if (generation !== this.generation) return;
      if (result.profile !== expected_profile) throw new Error('Settings profile changed. Reload required.');
      // Read back the exact target before reporting persistence success.
      const verified = await this.ctx.rest('/favorites');
      if (generation !== this.generation) return;
      if (verified.profile !== expected_profile) throw new Error('Settings profile changed. Reload required.');
      if (path === '/favorites' && !validateFavorites(verified.favorites).every((row, index) => same(row, body.favorites[index]))) throw new Error('Favorites changed during save. Reload required.');
      if (path === '/selection' && verified.selection.voice_id !== body.voice_id) throw new Error('Selection could not be verified. Reload required.');
      this.accept(verified); this.message = path === '/favorites' ? 'Favorites saved.' : 'Selection saved. Provider was not changed.';
    } catch (error) {
      if (generation === this.generation) { this.error = `${String(error.message || error)} Reload required.`; this.saved = null; }
    } finally { if (generation === this.generation) { this.busy = false; this.emit(); } }
  }
  async save() {
    try { const favorites = validateFavorites(this.rows); await this.write('/favorites', { favorites }); }
    catch (error) { this.error = String(error.message || error); this.emit(); }
  }
  async select(index) {
    if (!this.selectable(index)) return;
    // Preserve unsaved drafts in other slots after a selection-only operation.
    const draft = this.rows;
    await this.write('/selection', { voice_id: this.saved.favorites[index].voice_id });
    if (this.saved) { this.rows = draft; this.emit(); }
  }
}

const cellStyle = { padding: '5px', borderBottom: '1px solid var(--ui-stroke-secondary)', verticalAlign: 'middle' };
function FavoritesPage({ ctx }) {
  const [, refresh] = useState(0);
  const ref = useRef(null);
  if (!ref.current) ref.current = new FavoritesModel(ctx, () => refresh(value => value + 1));
  const model = ref.current;
  const [json, setJSON] = useState('');
  useEffect(() => { model.changed = () => refresh(value => value + 1); void model.reload(); return () => model.dispose(); }, [model]);
  const saved = model.saved;
  const action = (label, onClick, disabled = false) => jsx(Button, { type: 'button', size: 'sm', variant: 'outline', disabled, onClick, children: label }, label);
  return jsx('section', { style: { color: 'var(--ui-text-secondary)', fontSize: '13px', display: 'grid', gap: '12px' }, children: [
    jsx('h2', { style: { fontSize: '18px', fontWeight: 600 }, children: 'Cartesia Voice' }, 'heading'),
    jsx('p', { children: 'Five user-saved favorites, shared locally across profiles. Selection is per profile; selecting a voice never switches your TTS provider. No previews or catalog requests; this page uses no Cartesia credits.' }, 'notice'),
    jsx('p', { role: 'status', children: saved ? `Loaded backend profile: ${saved.profile} · Current TTS provider: ${saved.selection.provider || '(not set)'} · Selected voice: ${saved.selection.voice_id || '(none)'} · Language: ${saved.selection.language || '(not set)'}` : 'No verified profile loaded. Reload required.' }, 'scope'),
    jsx('p', { children: 'SDK compatibility warning: this SDK targets the active gateway profile, NOT Settings’ Applies to override. Writes affect only the loaded backend profile shown above. Change the active gateway profile and click Reload to retarget. Full Settings-selector support is blocked pending a public SDK scope API.' }, 'reload-notice'),
    jsx('div', { style: { overflowX: 'auto' }, children: jsx('table', { style: { width: '100%', borderCollapse: 'collapse' }, children: [
      jsx('thead', { children: jsx('tr', { children: ['Current', 'Voice Name', 'Language', 'Gender', 'Voice ID'].map(label => jsx('th', { scope: 'col', style: { ...cellStyle, textAlign: 'left' }, children: label }, label)) }) }, 'head'),
      jsx('tbody', { children: model.rows.map((row, index) => jsx('tr', { children: [
        jsx('td', { style: cellStyle, children: jsx('input', { type: 'radio', name: 'cartesia-current-voice', 'aria-label': `Select favorite ${index + 1}`, checked: Boolean(saved && row.voice_id && saved.selection.voice_id === row.voice_id), disabled: !model.selectable(index), onChange: () => void model.select(index) }) }, 'radio'),
        ...fields.map(field => jsx('td', { style: cellStyle, children: field === 'gender' ? jsx('select', { 'aria-label': `Favorite ${index + 1} gender`, value: row.gender, disabled: model.busy || !saved, onChange: event => model.edit(index, field, event.target.value), style: { color: 'var(--ui-text-primary)', background: 'var(--ui-bg-secondary)', border: '1px solid var(--ui-stroke-secondary)', borderRadius: '4px', padding: '6px' }, children: ['', 'male', 'female'].map(value => jsx('option', { value, children: value || '—' }, value)) }) : jsx(Input, { 'aria-label': `Favorite ${index + 1} ${field}`, value: row[field], disabled: model.busy || !saved, onChange: event => model.edit(index, field, event.target.value), style: { minWidth: field === 'voice_id' ? '260px' : '85px' } }) }, field))
      ] }, index)) }, 'body')
    ] }) }, 'table'),
    jsx('div', { style: { display: 'flex', gap: '8px' }, children: [action('Save favorites', () => void model.save(), model.busy || !saved), action('Reload', () => void model.reload())] }, 'actions'),
    model.error ? jsx('p', { role: 'alert', children: model.error }, 'error') : null,
    model.message ? jsx('p', { role: 'status', children: model.message }, 'message') : null,
    jsx('details', { children: [jsx('summary', { children: 'Import / export favorites JSON' }, 'summary'), jsx(Textarea, { 'aria-label': 'Favorites JSON', value: json, onChange: event => setJSON(event.target.value), rows: 7 }, 'json'), jsx('div', { style: { display: 'flex', gap: '8px', marginTop: '8px' }, children: [action('Import as draft', () => model.importJSON(json), model.busy || !saved), action('Export saved JSON', () => setJSON(JSON.stringify(model.saved.favorites, null, 2)), !saved || model.busy)] }, 'json-actions')] }, 'import-export')
  ] });
}

export default {
  id: 'cartesia-voice', name: 'Cartesia Voice', defaultEnabled: false,
  register(ctx) {
    const page = { id: 'favorites', title: 'Cartesia Voice', icon: 'unmute', render: () => jsx(FavoritesPage, { ctx }) };
    if (typeof ctx.registerSettingsPage === 'function') ctx.registerSettingsPage(page);
    else ctx.register({ id: page.id, title: page.title, area: SETTINGS_PLUGINS_AREA, data: { icon: page.icon, children: [] }, render: page.render });
  }
};
