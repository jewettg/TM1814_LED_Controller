import { api, getKinds } from '../api.js';
import { h, render, act, toast, pageHead, kindBadge, emptyState, loading, confirmDialog, promptDialog, modal, fmtDuration, fmtDurationWords, nextId, debounce, switchEl } from '../ui.js';
import { go, dispatch } from '../router.js';
import { refresh } from '../status.js';

const clone = (o) => JSON.parse(JSON.stringify(o));

// ------------------------------------------------------------------ list page
export async function runlists(ctx) {
  render(ctx.app, loading());
  let lists = await api.get('/runlists');
  const box = h('div');
  async function reload() { lists = await api.get('/runlists'); draw(); }
  function draw() {
    if (!lists.length) { render(box, emptyState('No run lists yet. A run list plays several effects one after another.', h('a', { class: 'btn primary', href: '#/runlist/new' }, 'Create a run list'))); return; }
    render(box, h('div', { class: 'card-grid' }, lists.map((r) => h('article', { class: 'card stack', 'aria-label': r.name },
      h('div', null, h('div', { style: { fontWeight: 700, fontSize: '1.1rem', wordBreak: 'break-word' } }, r.name),
        h('div', { class: 'muted small' }, r.items.length + ' item' + (r.items.length === 1 ? '' : 's') + ' • ' + fmtDurationWords(r.total_sec) + ' total')),
      h('div', { class: 'row tight' }, h('span', { class: 'badge ' + (r.loop ? 'info' : 'gray') }, r.loop ? 'Loops' : 'Plays once'), r.shuffle ? h('span', { class: 'badge info' }, 'Shuffled') : null),
      r.notes ? h('div', { class: 'help' }, r.notes) : null,
      h('div', { class: 'row tight', style: { marginTop: 'auto' } },
        h('button', { class: 'primary small', type: 'button', disabled: !r.items.length, onclick: act(async () => { await api.post('/control/play', { runlist_id: r.id }); await refresh(); toast('Playing "' + r.name + '"', 'success'); }) }, 'Play'),
        h('a', { class: 'btn small', href: '#/runlist/' + r.id }, 'Edit'),
        h('button', { class: 'small', type: 'button', onclick: act(async () => {
          const name = await promptDialog('Duplicate run list', 'Name for the copy', r.name + ' copy', 'Duplicate'); if (!name) return;
          await api.post('/runlists/' + r.id + '/duplicate', { name }); toast('Duplicated', 'success'); await reload();
        }) }, 'Duplicate'),
        h('button', { class: 'small', type: 'button', onclick: act(async () => {
          const name = await promptDialog('Rename run list', 'New name', r.name, 'Rename'); if (!name || name === r.name) return;
          await api.put('/runlists/' + r.id, { name }); toast('Renamed', 'success'); await reload();
        }) }, 'Rename'),
        h('button', { class: 'small danger', type: 'button', onclick: act(async () => {
          if (!await confirmDialog('Delete run list?', 'Delete "' + r.name + '"? Schedules that use it must be changed first.')) return;
          await api.del('/runlists/' + r.id); toast('Deleted'); await reload();
        }) }, 'Delete'))))));
  }
  render(ctx.app, pageHead('Run lists', h('a', { class: 'btn primary', href: '#/runlist/new' }, 'New run list')),
    h('p', { class: 'muted' }, 'A run list is an ordered playlist of effects, each shown for a set time. Schedules start run lists automatically.'), box);
  draw();
}

// ------------------------------------------------------------------ editor
export async function runlistEditor(ctx) {
  const app = ctx.app; render(app, loading());
  const isNew = ctx.route.arg === 'new';
  const [effects, kinds] = await Promise.all([api.get('/effects'), getKinds()]);
  const kindMap = Object.fromEntries(kinds.map((k) => [k.kind, k]));
  let saved = isNew ? { id: null, name: '', loop: true, shuffle: false, notes: '', items: [] } : await api.get('/runlists/' + ctx.route.arg);
  let st = clone({ name: saved.name, loop: saved.loop, shuffle: saved.shuffle, notes: saved.notes, items: saved.items });
  const effById = Object.fromEntries(effects.map((e) => [e.id, e]));

  const dirty = h('span', { class: 'badge warn', hidden: true }, 'Unsaved changes');
  const err = h('div', { class: 'banner error', hidden: true, role: 'alert' });
  const total = h('span', { class: 'muted' });
  const list = h('div', { 'aria-label': 'Items in this run list' });
  const snap = () => JSON.stringify({ name: saved.name, loop: saved.loop, shuffle: saved.shuffle, notes: saved.notes, items: saved.items.map((i) => [i.effect_id, i.duration_sec]) });
  const cur = () => JSON.stringify({ name: st.name, loop: st.loop, shuffle: st.shuffle, notes: st.notes, items: st.items.map((i) => [i.effect_id, i.duration_sec]) });
  const touch = () => { dirty.hidden = !isNew && cur() === snap(); total.textContent = st.items.length + ' item' + (st.items.length === 1 ? '' : 's') + ' • ' + fmtDurationWords(st.items.reduce((a, i) => a + i.duration_sec, 0)) + ' total'; };
  const showErr = (e) => { err.hidden = !e; err.textContent = e ? e.message : ''; };

  const nameId = nextId('n');
  const nameIn = h('input', { type: 'text', id: nameId, value: st.name, maxlength: 120, placeholder: 'For example: Halloween show' });
  nameIn.addEventListener('input', () => { st.name = nameIn.value; touch(); });
  const notesIn = h('textarea', { id: nextId('t'), 'aria-label': 'Notes' }, st.notes);
  notesIn.value = st.notes; notesIn.addEventListener('input', () => { st.notes = notesIn.value; touch(); });

  let dragFrom = null;
  function move(i, j) { if (j < 0 || j >= st.items.length || i === j) return; const [x] = st.items.splice(i, 1); st.items.splice(j, 0, x); drawItems(); touch(); }
  function drawItems() {
    if (!st.items.length) { render(list, emptyState('No effects in this run list yet. Use "Add effect" to choose some.')); return; }
    render(list, st.items.map((it, i) => {
      const e = effById[it.effect_id], k = kindMap[it.effect_kind || (e && e.kind)] || { native: false, label: it.effect_kind };
      const dur = h('input', { type: 'number', min: 1, max: 86400, step: 1, value: it.duration_sec, 'aria-label': 'Seconds for ' + it.effect_name });
      const pretty = h('span', { class: 'muted small', style: { minWidth: '4.5em' } }, fmtDuration(it.duration_sec));
      const setDur = (v) => { it.duration_sec = v; dur.value = v; pretty.textContent = fmtDuration(v); touch(); };
      dur.addEventListener('input', () => { const v = Number(dur.value); if (v >= 1) { it.duration_sec = v; pretty.textContent = fmtDuration(v); touch(); } });
      const quick = [['30s', 30], ['1m', 60], ['5m', 300], ['10m', 600]].map(([l, v]) => h('button', { type: 'button', class: 'small', 'aria-label': 'Set ' + it.effect_name + ' to ' + l, onclick: () => setDur(v) }, l));
      const row = h('div', { class: 'rl-item', draggable: 'true' },
        h('span', { class: 'grip', 'aria-hidden': 'true', title: 'Drag to reorder' }, '☰'),
        h('div', { class: 'stack' },
          h('div', { class: 'meta' }, h('span', { class: 'name' }, (i + 1) + '. ' + it.effect_name), kindBadge(k.native), h('span', { class: 'badge gray' }, k.label || it.effect_kind)),
          h('div', { class: 'meta' },
            h('div', { class: 'dur' }, dur, h('span', { class: 'small' }, 'sec'), pretty, quick),
            h('span', { class: 'spacer' }),
            h('div', { class: 'row tight' },
              h('button', { type: 'button', class: 'small icon', 'aria-label': 'Move ' + it.effect_name + ' up', disabled: i === 0, onclick: () => move(i, i - 1) }, '↑'),
              h('button', { type: 'button', class: 'small icon', 'aria-label': 'Move ' + it.effect_name + ' down', disabled: i === st.items.length - 1, onclick: () => move(i, i + 1) }, '↓'),
              h('button', { type: 'button', class: 'small danger', 'aria-label': 'Remove ' + it.effect_name, onclick: () => { st.items.splice(i, 1); drawItems(); touch(); } }, 'Remove')))));
      row.addEventListener('dragstart', (ev) => { dragFrom = i; row.classList.add('dragging'); ev.dataTransfer.effectAllowed = 'move'; try { ev.dataTransfer.setData('text/plain', String(i)); } catch (x) { /* ignore */ } });
      row.addEventListener('dragend', () => { row.classList.remove('dragging'); list.querySelectorAll('.drop-before').forEach((x) => x.classList.remove('drop-before')); });
      row.addEventListener('dragover', (ev) => { if (dragFrom === null) return; ev.preventDefault(); list.querySelectorAll('.drop-before').forEach((x) => x.classList.remove('drop-before')); row.classList.add('drop-before'); });
      row.addEventListener('drop', (ev) => { ev.preventDefault(); const from = dragFrom; dragFrom = null; if (from !== null) move(from, from < i ? i : i); });
      return row;
    }));
  }

  function addPicker() {
    const search = h('input', { type: 'search', placeholder: 'Search effects', 'aria-label': 'Search effects' });
    const results = h('div', { class: 'pick-list' });
    const draw = () => {
      const q = search.value.trim().toLowerCase();
      const found = effects.filter((e) => !q || e.name.toLowerCase().includes(q) || (kindMap[e.kind] || { label: '' }).label.toLowerCase().includes(q));
      render(results, found.length ? found.map((e) => {
        const k = kindMap[e.kind] || { native: false, label: e.kind };
        return h('div', { class: 'pick-row' }, h('span', { class: 'n' }, e.name), kindBadge(k.native),
          h('button', { type: 'button', class: 'small primary', 'aria-label': 'Add ' + e.name, onclick: () => {
            st.items.push({ effect_id: e.id, effect_name: e.name, effect_kind: e.kind, duration_sec: 60 }); drawItems(); touch(); toast('Added "' + e.name + '"', 'success', 1500);
          } }, 'Add'));
      }) : h('div', { class: 'muted', style: { padding: '12px' } }, 'No effects match.'));
    };
    search.addEventListener('input', debounce(draw, 120));
    modal('Add effect', h('div', null, h('p', { class: 'help' }, 'Click Add as many times as you like, then Close. New items default to 1 minute.'), search, results), { wide: true });
    draw(); search.focus();
  }

  async function save() {
    showErr(null);
    const name = st.name.trim();
    if (!name) { nameIn.classList.add('invalid'); nameIn.focus(); showErr(new Error('Give the run list a name.')); return null; }
    nameIn.classList.remove('invalid');
    const body = { name, loop: st.loop, shuffle: st.shuffle, notes: st.notes, items: st.items.map((i) => ({ effect_id: i.effect_id, duration_sec: i.duration_sec })) };
    try {
      if (isNew) { const c = await api.post('/runlists', body); toast('Saved "' + c.name + '"', 'success'); go('#/runlist/' + c.id); return c; }
      saved = await api.put('/runlists/' + saved.id, body);
      st = clone({ name: saved.name, loop: saved.loop, shuffle: saved.shuffle, notes: saved.notes, items: saved.items });
      toast('Saved', 'success'); touch(); drawItems(); return saved;
    } catch (e) { showErr(e); return null; }
  }

  const buttons = [
    h('button', { class: 'primary', type: 'button', onclick: act(save) }, 'Save'),
    h('button', { type: 'button', disabled: isNew, title: isNew ? 'Save first' : '', onclick: act(async () => {
      if (!dirty.hidden) { toast('You have unsaved changes; saving first.', 'info', 2000); const r = await save(); if (!r) return; }
      await api.post('/control/play', { runlist_id: saved.id }); await refresh(); toast('Playing "' + saved.name + '"', 'success');
    }) }, 'Play'),
    h('button', { type: 'button', onclick: () => dispatch() }, 'Revert'),
    isNew ? null : h('button', { class: 'danger', type: 'button', onclick: act(async () => {
      if (!await confirmDialog('Delete run list?', 'Delete "' + saved.name + '"?')) return;
      try { await api.del('/runlists/' + saved.id); toast('Deleted'); go('#/runlists'); } catch (e) { showErr(e); }
    }) }, 'Delete')];

  render(app, h('p', null, h('a', { href: '#/runlists' }, '← All run lists')),
    h('div', { class: 'page-head' }, h('h1', null, isNew ? 'New run list' : 'Edit run list'), dirty),
    err,
    h('div', { class: 'card stack' },
      h('div', { class: 'form-grid' },
        h('div', null, h('label', { class: 'field-label', for: nameId }, 'Name'), nameIn),
        h('div', null, h('label', { class: 'field-label', for: notesIn.id }, 'Notes (optional)'), notesIn)),
      h('div', { class: 'row' },
        switchEl(st.loop, (v) => { st.loop = v; touch(); }, 'Loop (start over when finished)'),
        switchEl(st.shuffle, (v) => { st.shuffle = v; touch(); }, 'Shuffle (random order)')),
      h('p', { class: 'help' }, 'If Loop is off, the run list plays through once and then the lights go idle.')),
    h('div', { class: 'row', style: { margin: 'var(--space-4) 0 var(--space-2)' } }, h('h2', { style: { margin: 0 } }, 'Items'), total, h('span', { class: 'spacer' }),
      h('button', { type: 'button', class: 'primary', onclick: addPicker }, '+ Add effect')),
    list, h('div', { class: 'row', style: { marginTop: 'var(--space-4)' } }, buttons));
  drawItems(); touch();
  if (isNew) dirty.hidden = true;
}
