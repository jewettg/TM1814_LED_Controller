import { api, getKinds } from '../api.js';
import { h, render, act, toast, pageHead, kindBadge, emptyState, loading, confirmDialog, promptDialog, modal, debounce } from '../ui.js';
import { refresh } from '../status.js';
import { StripView, FramePlayer } from '../strip.js';
import { getMeta } from '../api.js';
import { go } from '../router.js';

// Previews are fetched through a tiny queue so a big catalog does not hammer a Raspberry Pi.
const queue = []; let running = 0; const MAX_PARALLEL = 2;
function enqueue(job) { queue.push(job); pump(); }
function pump() {
  while (running < MAX_PARALLEL && queue.length) {
    const job = queue.shift(); running++;
    job().catch(() => {}).finally(() => { running--; pump(); });
  }
}
const previewCache = new Map(); // effect id + updated -> {frames, fps}

export async function catalog(ctx) {
  const app = ctx.app;
  render(app, loading());
  const [effects, kinds, meta, sections] = await Promise.all([api.get('/effects'), getKinds(), getMeta(), api.get('/sections')]);
  const openSections = new Set(); // sections are collapsed until opened
  const kindMap = Object.fromEntries(kinds.map((k) => [k.kind, k]));
  queue.length = 0;
  ctx.onLeave(() => { queue.length = 0; });

  let search = '', filter = 'all';
  const players = [];
  let observer = null;
  ctx.onLeave(() => { players.forEach((p) => { p.player.stop(); p.view.destroy(); }); if (observer) observer.disconnect(); });

  const grid = h('div');
  const chipsEl = h('div', { class: 'chips', role: 'group', 'aria-label': 'Filter by type' });
  const count = h('span', { class: 'muted small' });

  const searchBox = h('input', { type: 'search', placeholder: 'Search effects', 'aria-label': 'Search effects' });
  searchBox.addEventListener('input', debounce(() => { search = searchBox.value.trim().toLowerCase(); draw(); }, 150));

  function chips() {
    const used = [...new Set(effects.map((e) => e.kind))];
    const items = [['all', 'All'], ['native', 'Controller-native'], ['fixed', 'Fixed'], ['animated', 'Animated'], ...used.map((k) => ['kind:' + k, (kindMap[k] || { label: k }).label])];
    render(chipsEl, items.map(([key, label]) => h('button', { type: 'button', class: 'chip', 'aria-pressed': String(filter === key), onclick: () => { filter = key; chips(); draw(); } }, label)));
  }

  function matches(e) {
    const k = kindMap[e.kind] || { family: 'animated', label: e.kind };
    if (['native', 'fixed', 'animated'].includes(filter) && k.family !== filter) return false;
    if (filter.startsWith('kind:') && e.kind !== filter.slice(5)) return false;
    if (search && !(e.name.toLowerCase().includes(search) || k.label.toLowerCase().includes(search) || (e.description || '').toLowerCase().includes(search))) return false;
    return true;
  }

  function card(e) {
    const k = kindMap[e.kind] || { family: 'animated', native: false, previewable: false, label: e.kind };
    const canvas = h('canvas', { class: 'thumb', 'aria-hidden': 'true' });
    const prev = h('div', { class: 'preview' + (k.previewable ? '' : ' static') }, k.previewable ? canvas : 'Runs on the controller (no preview)');
    const el = h('article', { class: 'card effect-card', 'aria-label': e.name },
      h('div', { class: 'title' }, e.name),
      h('div', { class: 'row tight' }, kindBadge(k.family), h('span', { class: 'badge gray' }, k.label)),
      prev,
      e.description ? h('div', { class: 'help' }, e.description) : null,
      h('div', { class: 'card-actions' },
        h('button', { class: 'primary small', type: 'button', onclick: act(async () => { await api.post('/control/play', { effect_id: e.id }); await refresh(); toast('Playing "' + e.name + '"', 'success'); }) }, 'Play on lights'),
        h('a', { class: 'btn small', href: '#/effect/' + e.id }, 'Edit'),
        h('button', { class: 'small', type: 'button', onclick: act(async () => {
          const name = await promptDialog('Duplicate effect', 'Name for the copy', e.name + ' copy', 'Duplicate');
          if (!name) return;
          const c = await api.post('/effects/' + e.id + '/duplicate', { name });
          toast('Created "' + c.name + '"', 'success'); reload();
        }) }, 'Duplicate'),
        h('button', { class: 'small', type: 'button', onclick: () => moveEffect(e) }, 'Move'),
        h('button', { class: 'small', type: 'button', onclick: act(async () => {
          const name = await promptDialog('Rename effect', 'New name', e.name, 'Rename');
          if (!name || name === e.name) return;
          await api.put('/effects/' + e.id, { name }); toast('Renamed', 'success'); reload();
        }) }, 'Rename'),
        h('button', { class: 'small danger', type: 'button', onclick: act(async () => {
          if (!await confirmDialog('Delete effect?', 'Delete "' + e.name + '"? This cannot be undone.')) return;
          await api.del('/effects/' + e.id); toast('Deleted "' + e.name + '"'); reload();
        }) }, 'Delete')));
    if (k.previewable) {
      const view = new StripView(canvas, { n: meta.led_count || 250, mode: 'bars', height: 34 });
      const player = new FramePlayer(view);
      const entry = { player, view, e, loaded: false, visible: false, el: prev };
      players.push(entry);
      observer.observe(prev);
      prev._entry = entry;
    }
    return el;
  }

  function onIntersect(entries) {
    for (const en of entries) {
      const entry = en.target._entry; if (!entry) continue;
      entry.visible = en.isIntersecting;
      if (en.isIntersecting) {
        if (!entry.loaded) { entry.loaded = true; load(entry); } else entry.player.play();
      } else entry.player.pause();
    }
  }
  function load(entry) {
    const key = entry.e.id + ':' + entry.e.updated;
    const cached = previewCache.get(key);
    const apply = (r) => { entry.player.load(r.frames, r.fps); if (entry.visible) entry.player.play(); };
    if (cached) { apply(cached); return; }
    enqueue(async () => {
      if (!entry.el.isConnected) return;
      try {
        const r = await api.post('/preview', { kind: entry.e.kind, params: entry.e.params, seconds: 8, fps: 5 });
        previewCache.set(key, r); apply(r);
      } catch (err) { entry.el.classList.add('static'); entry.el.textContent = 'No preview'; }
    });
  }

  function draw() {
    players.forEach((p) => { p.player.stop(); p.view.destroy(); }); players.length = 0;
    if (observer) observer.disconnect();
    observer = new IntersectionObserver(onIntersect, { rootMargin: '120px' });
    const shown = effects.filter(matches);
    count.textContent = shown.length + ' of ' + effects.length + ' effects';
    if (!effects.length) { render(grid, emptyState('No effects yet.', h('button', { class: 'primary', type: 'button', onclick: newEffect }, 'New effect'))); return; }
    if (!shown.length) { render(grid, emptyState('No effects match your search or filter.')); return; }
    const builtIn = [['Controller-native', 'native', 'These run on the controller itself and keep going even if this program stops. Built-in animations cannot be previewed here.'],
      ['Fixed', 'fixed', 'Still pictures: painted once and left that way. Nothing moves, and they stay lit even if this program stops.'],
      ['Animated', 'animated', 'These are drawn by this program at about 5 frames per second and need it running. Previews are shown below.']];
    const fam = (e) => (kindMap[e.kind] || {}).family || 'animated';
    const groups = [...builtIn.map(([name, family, help]) => ({ name, help, custom: false, list: shown.filter((e) => e.section === name || (!e.section && fam(e) === family)) })),
      ...custom().map((name) => ({ name, help: '', custom: true, list: shown.filter((e) => e.section === name) }))];
    const searching = !!(search || filter !== 'all');
    render(grid, groups.filter((g) => g.list.length || (g.custom && !searching)).map((g) => {
      const key = g.custom ? 'custom:' + g.name : g.name;
      const total = effects.filter((e) => g.custom ? e.section === g.name : (e.section === g.name || (!e.section && fam(e) === builtIn.find((b) => b[0] === g.name)[1]))).length;
      const det = h('details', { class: 'effect-section' },
        h('summary', { class: 'section-title' }, g.name + ' (' + (searching ? g.list.length + ' of ' + total : g.list.length) + ')'),
        g.custom ? h('div', { class: 'row tight', style: { marginBottom: '8px' } },
          h('button', { class: 'small', type: 'button', onclick: act(() => renameSection(g.name)) }, 'Rename section'),
          h('button', { class: 'small danger', type: 'button', onclick: act(() => removeSection(g.name)) }, 'Delete section')) : null,
        g.help ? h('p', { class: 'help' }, g.help) : null,
        g.list.length ? h('div', { class: 'card-grid' }, g.list.map(card)) : h('p', { class: 'muted' }, 'Empty. Use Move on an effect to put it here.'));
      det.open = openSections.has(key) || (searching && g.list.length > 0);
      det.addEventListener('toggle', () => { if (!searching) { det.open ? openSections.add(key) : openSections.delete(key); } });
      return det;
    }));
  }

  const BUILT_IN = ['Controller-native', 'Fixed', 'Animated'];
  const custom = () => sections.filter((n) => !BUILT_IN.includes(n));
  async function newSection() {
    const name = await promptDialog('New section', 'Section name', '', 'Create');
    if (!name) return null;
    const r = await api.post('/sections', { name }); sections.splice(0, sections.length, ...r);
    return r.find((x) => x.toLowerCase() === name.trim().toLowerCase()) || name;
  }
  async function renameSection(old) {
    const name = await promptDialog('Rename section', 'New name', old, 'Rename');
    if (!name || name === old) return;
    const r = await api.post('/sections/rename', { name: old, new_name: name });
    if (openSections.delete('custom:' + old)) openSections.add('custom:' + name.trim());
    sections.splice(0, sections.length, ...r); toast('Renamed', 'success'); reload();
  }
  async function removeSection(name) {
    if (!await confirmDialog('Delete section?', 'Delete the section "' + name + '"? Its effects are not deleted; they go back to their usual group.')) return;
    const r = await api.post('/sections/delete', { name });
    sections.splice(0, sections.length, ...r); toast('Section deleted'); reload();
  }
  function moveEffect(e) {
    const NEW = '\u0000new';
    const sel = h('select', { 'aria-label': 'Section' },
      ...BUILT_IN.map((name) => h('option', { value: name }, name)),
      ...custom().map((name) => h('option', { value: name }, name)),
      h('option', { value: NEW }, 'New section\u2026'));
    sel.value = e.section || ({ native: 'Controller-native', fixed: 'Fixed' }[(kindMap[e.kind] || {}).family] || 'Animated');
    const body = h('div', null, h('p', { class: 'muted' }, 'Choose the section for "' + e.name + '".'), sel,
      h('div', { class: 'row', style: { marginTop: '12px' } },
        h('button', { class: 'primary', type: 'button', onclick: act(async () => {
          let target = sel.value;
          if (target === NEW) { m.close(); target = await newSection(); if (!target) return; }
          else m.close();
          const own = { native: 'Controller-native', fixed: 'Fixed' }[(kindMap[e.kind] || {}).family] || 'Animated';
          await api.put('/effects/' + e.id, { section: target === own ? '' : target });
          openSections.add(BUILT_IN.includes(target) ? target : 'custom:' + target);
          toast('Moved "' + e.name + '"', 'success'); reload();
        }) }, 'Move')));
    const m = modal('Move effect', body);
  }

  async function reload() {
    const fresh = await api.get('/effects');
    effects.length = 0; effects.push(...fresh); chips(); draw();
  }

  function newEffect() {
    const body = h('div', null, h('p', { class: 'muted' }, 'Pick the type of effect to create. You can adjust every setting afterwards.'),
      ...['Controller-native', 'Fixed', 'Animated'].map((cat) => h('div', null, h('h3', null, cat),
        kinds.filter((k) => k.category === cat).map((k) =>
          h('button', { type: 'button', class: 'kind-option', onclick: () => { m.close(); go('#/effect/new?kind=' + encodeURIComponent(k.kind)); } },
            h('strong', null, k.label, kindBadge(k.family)), h('span', { class: 'd' }, k.description))))));
    const m = modal('New effect', body, { wide: true });
  }

  render(app, pageHead('Effects', h('span', { class: 'row tight' }, h('button', { type: 'button', onclick: act(async () => { if (await newSection()) { toast('Section created', 'success'); reload(); } }) }, 'New section'), h('button', { class: 'primary', type: 'button', onclick: newEffect }, 'New effect'))),
    h('p', { class: 'muted' }, 'An effect is one look for your lights. Add effects to run lists to play them in order, or press Play on lights to try one now.'),
    h('div', { class: 'toolbar' }, searchBox, count), chipsEl, h('div', { style: { height: '8px' } }), grid);
  chips(); draw();
}
