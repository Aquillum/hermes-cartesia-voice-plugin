import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import vm from 'node:vm';
import { createRequire } from 'node:module';
import { homedir } from 'node:os';
import { resolve } from 'node:path';
const require = createRequire(resolve(process.env.HERMES_AGENT_ROOT || resolve(homedir(), '.hermes', 'hermes-agent'), 'package.json'));
const React = require('react');
const { jsx } = require('react/jsx-runtime');
const { JSDOM } = require('jsdom');
async function load() {
  let source;
  try { source = await readFile(new URL('../desktop/plugin.js', import.meta.url), 'utf8'); }
  catch { assert.fail('Desktop settings plugin must exist'); }
  source = source.replace(/^import .*;$/gm, '').replace(/export default /, 'globalThis.plugin = ').replace(/export /g, '');
  const context = vm.createContext({ ...React, jsx, Button: 'button', Input: 'input', Textarea: 'textarea', SETTINGS_PLUGINS_AREA: 'settings.plugins', console });
  vm.runInContext(source + '\nglobalThis.Model = FavoritesModel;', context);
  return context;
}
const empty = () => ({ name: '', language: '', gender: '', voice_id: '' });
const voice = { name: 'Synthetic', language: 'de', gender: 'female', voice_id: '11111111-1111-4111-8111-111111111111' };
const state = (profile = 'test-a') => ({ favorites: [voice, empty(), empty(), empty(), empty()], profile, selection: { voice_id: voice.voice_id, language: 'de', provider: 'other' }, catalog_scope: 'shared-local-user' });
test('opt-in native Settings registration and five-row UI read without writes', async () => {
  const c = await load();
  assert.equal(c.plugin.id, 'cartesia-voice');
  assert.equal(c.plugin.defaultEnabled, false);
  const calls = []; let page; const backend = state(); backend.selection.voice_id = '';
  c.plugin.register({ registerSettingsPage(p) { page = p; }, rest: async (path, opts) => {
    calls.push([path, opts]);
    if (path === '/selection') backend.selection.voice_id = opts.body.voice_id;
    if (opts?.method === 'PUT') backend.favorites = opts.body.favorites;
    return structuredClone(backend);
  } });
  assert.equal(page.title, 'Cartesia Voice');
  const dom = new JSDOM('<div id="root"></div>');
  globalThis.window = dom.window; globalThis.document = dom.window.document; globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  const { createRoot } = require('react-dom/client');
  const root = createRoot(document.getElementById('root'));
  await React.act(async () => { root.render(page.render()); });
  assert.equal(document.querySelectorAll('tbody tr').length, 5);
  assert.equal(document.querySelectorAll('input[type=radio]').length, 5);
  assert.match(document.body.textContent, /test-a/);
  assert.match(document.body.textContent, /other/);
  assert.equal(calls.length, 1);
  assert.equal(calls[0][0], '/favorites');
  assert.equal(document.querySelectorAll('input[type=radio]:disabled').length, 4);
  // Real React DOM click: choose a saved radio, then verify the REST payload.
  const radio = document.querySelector('input[type=radio]');
  await React.act(async () => { radio.click(); });
  const writes = calls.filter(([, opts]) => opts?.method);
  assert.equal(writes.length, 1);
  assert.equal(writes[0][0], '/selection');
  assert.equal(writes[0][1].method, 'POST');
  assert.equal(writes[0][1].body.voice_id, voice.voice_id);
  assert.equal(writes[0][1].body.expected_profile, 'test-a');
  assert.equal('provider' in writes[0][1].body, false);
  // Explicit native Button click performs save + verification readback.
  await React.act(async () => { [...document.querySelectorAll('button')].find(b => b.textContent === 'Save favorites').click(); });
  assert.equal(calls.filter(([, opts]) => opts?.method === 'PUT').length, 1);
  assert.match(document.body.textContent, /Favorites saved/);
  await React.act(async () => { root.unmount(); }); dom.window.close();
});

test('draft edits never select, invalid imports preserve drafts, saved-only radios', async () => {
  const { Model } = await load(); const calls = [];
  const model = new Model({ rest: async (path, opts) => { calls.push([path, opts]); return state(); } });
  await model.reload();
  model.edit(0, 'name', 'Draft');
  assert.equal(Boolean(model.selectable(0)), false);
  await model.select(0);
  assert.equal(calls.length, 1);
  model.importJSON('[{}]');
  assert.match(model.error, /Exactly five/);
  assert.equal(model.rows[0].name, 'Draft');
  model.importJSON(JSON.stringify([voice, empty(), empty(), empty(), empty()]));
  assert.equal(Boolean(model.selectable(0)), true);
  model.edit(1, 'voice_id', 'not-a-uuid');
  await model.save();
  assert.match(model.error, /Slot 2/);
  assert.equal(calls.length, 1);
});

test('profile scope changes fail closed before writes, including server races', async () => {
  const { Model } = await load(); let profile = 'test-a'; const writes = [];
  const model = new Model({ rest: async (path, opts) => {
    if (opts) { writes.push(opts); throw new Error('expected_profile mismatch'); }
    return state(profile);
  } });
  await model.reload(); profile = 'test-b';
  await model.select(0);
  assert.equal(writes.length, 0);
  assert.equal(model.saved, null);
  assert.match(model.error, /Reload required/);
  await model.reload();
  assert.equal(model.saved.profile, 'test-b');
  await model.select(0);
  assert.equal(writes.length, 1);
  assert.equal(writes[0].body.expected_profile, 'test-b');
  assert.equal(model.saved, null);
});

test('out-of-order loads and disposed requests cannot overwrite newer state', async () => {
  const { Model } = await load(); const pending = [];
  const model = new Model({ rest: () => new Promise(resolve => pending.push(resolve)) });
  const first = model.reload(); const second = model.reload();
  pending[1](state('test-b')); await second;
  pending[0](state('test-a')); await first;
  assert.equal(model.saved.profile, 'test-b');
  const third = model.reload(); model.dispose(); pending[2](state('test-c')); await third;
  assert.equal(model.saved, null);
});

test('save verifies values independently of backend JSON property order', async () => {
  const { Model } = await load();
  const backend = state();
  const model = new Model({ rest: async (path, opts) => {
    if (opts?.method === 'PUT') backend.favorites = opts.body.favorites.map(row => ({ voice_id: row.voice_id, gender: row.gender, language: row.language, name: row.name }));
    return structuredClone(backend);
  } });
  await model.reload(); model.edit(0, 'name', 'Renamed'); await model.save();
  assert.equal(model.error, '');
  assert.equal(model.message, 'Favorites saved.');
});

test('fallback registers only the supported Settings contribution area', async () => {
  const c = await load(); let contribution;
  c.plugin.register({ register(value) { contribution = value; } });
  assert.equal(contribution.area, 'settings.plugins');
  assert.equal(contribution.id, 'favorites');
});
