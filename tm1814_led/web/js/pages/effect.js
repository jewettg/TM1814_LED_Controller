import { api, getKinds, getMeta } from '../api.js';
import { h, render, act, toast, kindBadge, loading, confirmDialog, promptDialog, nextId } from '../ui.js';
import { buildParamForm } from '../params.js';
import { previewPanel } from '../preview.js';
import { refresh as refreshStatus } from '../status.js';
import { go, dispatch } from '../router.js';
import { frameStudio } from './frames.js';

const clone = (o) => JSON.parse(JSON.stringify(o));

export async function effectEditor(ctx) {
  const app = ctx.app;
  render(app, loading());
  const [kinds, meta] = await Promise.all([getKinds(), getMeta()]);
  const isNew = ctx.route.arg === 'new';
  let effect = null, kindName;
  if (isNew) kindName = ctx.route.query.kind;
  else { effect = await api.get('/effects/' + ctx.route.arg); kindName = effect.kind; }
  const schema = kinds.find((k) => k.kind === kindName);
  if (!schema) { render(app, h('div', { class: 'banner error' }, 'Unknown effect type "' + kindName + '". ', h('a', { href: '#/catalog' }, 'Back to effects'))); return; }

  // working copy of the unsaved state
  const defaults = {}; schema.params.forEach((p) => { defaults[p.key] = clone(p.default); });
  let saved = isNew ? { id: null, name: schema.label, kind: kindName, description: '', params: defaults } : effect;
  let work = { name: saved.name, description: saved.description || '', params: clone(saved.params) };
  let preview = null, studio = null, form = null;

  const nameId = nextId('n');
  const nameIn = h('input', { type: 'text', id: nameId, value: work.name, maxlength: 120 });
  const descIn = h('input', { type: 'text', id: nextId('d'), value: work.description, maxlength: 300, 'aria-label': 'Description' });
  const dirtyBadge = h('span', { class: 'badge warn', hidden: true }, 'Unsaved changes');
  const errBanner = h('div', { class: 'banner error', hidden: true, role: 'alert' });
  nameIn.addEventListener('input', () => { work.name = nameIn.value; markDirty(); });
  descIn.addEventListener('input', () => { work.description = descIn.value; markDirty(); });

  const snapshot = () => JSON.stringify([saved.name, saved.description || '', saved.params]);
  const current = () => JSON.stringify([work.name, work.description, work.params]);
  function markDirty() { dirtyBadge.hidden = current() === snapshot() && !isNew; }
  function showError(e) { errBanner.hidden = !e; errBanner.textContent = e ? e.message : ''; }

  const onParams = () => { work.params = { ...work.params, ...form.values }; showError(null); markDirty(); if (preview) preview.refresh(); };
  const onStudio = (data) => { work.params = { ...work.params, data }; showError(null); markDirty(); };

  // ---- body
  let left, right;
  if (kindName === 'frames') {
    studio = frameStudio({ data: work.params.data, ledCount: meta.led_count || 250, onChange: onStudio,
      playOnLights: async () => { await playOnLights(); }, getName: () => work.name });
    ctx.onLeave(() => studio.destroy());
    left = studio.el; right = null;
  } else {
    form = buildParamForm(schema, work.params, onParams);
    work.params = { ...work.params, ...form.values };
    const req = () => (schema.previewable ? { kind: kindName, params: work.params } : null);
    preview = previewPanel({ n: meta.led_count || 250, getRequest: req, unavailableText: schema.previewable ? '' : 'This animation runs on the controller itself, so it cannot be previewed here. Use "Play on lights" to see it.' });
    ctx.onLeave(() => preview.destroy());
    left = h('div', { class: 'card' }, h('h2', null, 'Settings'), form.el);
    right = h('div', { class: 'card sticky' }, h('h2', null, 'Preview'),
      schema.previewable ? null : h('p', { class: 'muted' }, 'Controller-native animations cannot be previewed in the browser.'), preview.el);
    if (schema.previewable) preview.runNow();
    else preview.view.set(null);
  }

  async function playOnLights() {
    showError(null);
    try {
      await api.post('/control/play', { kind: kindName, params: work.params, name: work.name || 'Editor preview' });
      await refreshStatus(); toast('Playing on the lights', 'success');
    } catch (e) { showError(e); }
  }

  async function save(btn) {
    showError(null);
    const name = nameIn.value.trim();
    if (!name) { nameIn.classList.add('invalid'); nameIn.focus(); showError(new Error('Give the effect a name.')); return; }
    nameIn.classList.remove('invalid');
    try {
      if (isNew || saved.id === null) {
        const c = await api.post('/effects', { name, kind: kindName, params: work.params, description: work.description });
        toast('Saved "' + c.name + '"', 'success'); go('#/effect/' + c.id);
      } else {
        saved = await api.put('/effects/' + saved.id, { name, params: work.params, description: work.description });
        work = { name: saved.name, description: saved.description || '', params: clone(saved.params) };
        nameIn.value = work.name; markDirty(); toast('Saved', 'success');
      }
    } catch (e) { showError(e); }
  }

  const saveBtn = h('button', { class: 'primary', type: 'button', onclick: act(save) }, 'Save');
  const copyBtn = h('button', { type: 'button', onclick: act(async () => {
    const name = await promptDialog('Save as copy', 'Name for the new effect', (nameIn.value.trim() || schema.label) + (isNew ? '' : ' copy'), 'Save copy');
    if (!name) return;
    try {
      const c = await api.post('/effects', { name, kind: kindName, params: work.params, description: work.description });
      toast('Saved copy "' + c.name + '"', 'success'); go('#/effect/' + c.id);
    } catch (e) { showError(e); }
  }) }, 'Save as copy');
  const revertBtn = h('button', { type: 'button', onclick: () => {
    work = { name: saved.name, description: saved.description || '', params: clone(saved.params) };
    nameIn.value = work.name; descIn.value = work.description; showError(null); dispatch();
  } }, 'Revert');
  const playBtn = h('button', { type: 'button', onclick: act(playOnLights) }, 'Play on lights');
  const delBtn = isNew ? null : h('button', { class: 'danger', type: 'button', onclick: act(async () => {
    if (!await confirmDialog('Delete effect?', 'Delete "' + saved.name + '"? This cannot be undone.')) return;
    try { await api.del('/effects/' + saved.id); toast('Deleted', 'success'); go('#/catalog'); } catch (e) { showError(e); }
  }) }, 'Delete');

  const head = h('div', { class: 'page-head' }, h('h1', null, isNew ? 'New effect' : 'Edit effect'),
    h('div', { class: 'row' }, kindBadge(schema.family), h('span', { class: 'badge gray' }, schema.label), dirtyBadge));
  const nameCard = h('div', { class: 'card', style: { marginBottom: 'var(--space-4)' } },
    h('div', { class: 'form-grid' },
      h('div', null, h('label', { class: 'field-label', for: nameId }, 'Name'), nameIn),
      h('div', null, h('label', { class: 'field-label', for: descIn.id }, 'Description (optional)'), descIn)),
    h('p', { class: 'help' }, schema.description),
    h('div', { class: 'row', style: { marginTop: '12px' } }, saveBtn, copyBtn, revertBtn, playBtn, h('span', { class: 'spacer' }), delBtn));

  render(app, h('p', null, h('a', { href: '#/catalog' }, '← All effects')), head, nameCard, errBanner,
    right ? h('div', { class: 'editor-layout' }, left, right) : left);
  markDirty();
  if (isNew) dirtyBadge.hidden = true;
}
