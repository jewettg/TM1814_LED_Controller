import { api, getMeta } from '../api.js';
import { h, render, act, toast, pageHead, emptyState, loading, confirmDialog, promptDialog, fmtDateTime, fmtWindow, nextId, debounce, switchEl } from '../ui.js';
import { go, dispatch } from '../router.js';

const clone = (o) => JSON.parse(JSON.stringify(o));
const WD = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];
const WD_FULL = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday'];
const MONTHS = ['January', 'February', 'March', 'April', 'May', 'June', 'July', 'August', 'September', 'October', 'November', 'December'];

// ------------------------------------------------------------------ list page
export async function schedules(ctx) {
  render(ctx.app, loading());
  let rows = await api.get('/schedules');
  const box = h('div');
  const nextStart = {};
  async function loadNext() {
    await Promise.all(rows.map(async (s) => {
      try { const o = await api.get('/schedules/' + s.id + '/occurrences?count=1'); nextStart[s.id] = o.windows[0] ? fmtDateTime(o.windows[0].start) : 'none coming up'; }
      catch (e) { nextStart[s.id] = 'unknown'; }
    }));
  }
  async function reload() { rows = await api.get('/schedules'); await loadNext(); draw(); }
  function draw() {
    if (!rows.length) { render(box, emptyState('No schedules yet. A schedule starts a run list automatically at the times you choose.', h('a', { class: 'btn primary', href: '#/schedule/new' }, 'Create a schedule'))); return; }
    render(box, h('div', { class: 'card-grid' }, rows.map((s) => h('article', { class: 'card stack', 'aria-label': s.name },
      h('div', null, h('div', { style: { fontWeight: 700, fontSize: '1.1rem', wordBreak: 'break-word' } }, s.name),
        h('div', { class: 'muted small' }, 'Plays: ' + (s.runlist_name || 'missing run list'))),
      h('div', { class: 'small' }, s.summary),
      h('div', { class: 'row tight' }, h('span', { class: 'badge info' }, 'Priority ' + s.priority), h('span', { class: 'badge ' + (s.enabled ? 'ok' : 'gray') }, s.enabled ? 'Enabled' : 'Disabled')),
      h('div', { class: 'small' }, h('span', { class: 'muted' }, 'Next start: '), s.enabled ? (nextStart[s.id] || '...') : 'disabled'),
      switchEl(s.enabled, async (v) => { try { await api.put('/schedules/' + s.id, { enabled: v }); toast(v ? 'Enabled' : 'Disabled', 'success', 1800); await reload(); } catch (e) { toast(e.message, 'error'); await reload(); } }, 'Enabled'),
      h('div', { class: 'row tight', style: { marginTop: 'auto' } },
        h('a', { class: 'btn small primary', href: '#/schedule/' + s.id }, 'Edit'),
        h('button', { class: 'small', type: 'button', onclick: act(async () => {
          const name = await promptDialog('Duplicate schedule', 'Name for the copy', s.name + ' copy', 'Duplicate'); if (!name) return;
          await api.post('/schedules/' + s.id + '/duplicate', { name }); toast('Duplicated (the copy starts disabled)', 'success'); await reload();
        }) }, 'Duplicate'),
        h('button', { class: 'small', type: 'button', onclick: act(async () => {
          const name = await promptDialog('Rename schedule', 'New name', s.name, 'Rename'); if (!name || name === s.name) return;
          await api.put('/schedules/' + s.id, { name }); toast('Renamed', 'success'); await reload();
        }) }, 'Rename'),
        h('button', { class: 'small danger', type: 'button', onclick: act(async () => {
          if (!await confirmDialog('Delete schedule?', 'Delete "' + s.name + '"?')) return;
          await api.del('/schedules/' + s.id); toast('Deleted'); await reload();
        }) }, 'Delete'))))));
  }
  render(ctx.app, pageHead('Schedules', h('a', { class: 'btn primary', href: '#/schedule/new' }, 'New schedule')),
    h('p', { class: 'muted' }, 'Schedules turn the lights on by themselves. When two schedules overlap, the one with the higher priority wins.'), box);
  draw(); await loadNext(); draw();
}

// ------------------------------------------------------------------ editor
function defaultSpec() {
  return { start_date: null, end_date: null, start_time: '18:00', end_time: '23:00', start_offset: 0, end_offset: 0, repeat: { type: 'daily', interval: 1 } };
}

export async function scheduleEditor(ctx) {
  const app = ctx.app; render(app, loading());
  const isNew = ctx.route.arg === 'new';
  const [runlists, meta] = await Promise.all([api.get('/runlists'), getMeta()]);
  let saved = isNew ? { id: null, name: '', runlist_id: runlists[0] ? runlists[0].id : null, enabled: true, priority: 0, spec: defaultSpec() } : await api.get('/schedules/' + ctx.route.arg);
  let st = clone({ name: saved.name, runlist_id: saved.runlist_id, enabled: saved.enabled, priority: saved.priority, spec: saved.spec });
  const spec = () => st.spec;

  const dirty = h('span', { class: 'badge warn', hidden: true }, 'Unsaved changes');
  const err = h('div', { class: 'banner error', hidden: true, role: 'alert' });
  const showErr = (e) => { err.hidden = !e; err.textContent = e ? e.message : ''; };
  const snapStr = () => JSON.stringify({ n: saved.name, r: saved.runlist_id, e: saved.enabled, p: saved.priority, s: saved.spec });
  const curStr = () => JSON.stringify({ n: st.name, r: st.runlist_id, e: st.enabled, p: st.priority, s: st.spec });
  const changed = () => { dirty.hidden = !isNew && curStr() === snapStr(); previewDeb(); };

  // ---- basics
  const nameId = nextId('n'), rlId = nextId('r'), prId = nextId('p');
  const nameIn = h('input', { type: 'text', id: nameId, value: st.name, maxlength: 120, placeholder: 'For example: Nightly lights' });
  nameIn.addEventListener('input', () => { st.name = nameIn.value; changed(); });
  const rlSel = h('select', { id: rlId }, runlists.length ? runlists.map((r) => h('option', { value: r.id, selected: r.id === st.runlist_id }, r.name)) : h('option', { value: '' }, 'No run lists yet'));
  rlSel.addEventListener('change', () => { st.runlist_id = Number(rlSel.value); changed(); });
  const prIn = h('input', { type: 'number', id: prId, value: st.priority, step: 1 });
  prIn.addEventListener('input', () => { st.priority = Math.round(Number(prIn.value) || 0); changed(); });

  // ---- date range
  const dateBox = h('div');
  function drawDates() {
    const once = spec().repeat.type === 'once';
    const sd = h('input', { type: 'date', id: nextId('sd'), value: spec().start_date || '' });
    const ed = h('input', { type: 'date', id: nextId('ed'), value: spec().end_date || '' });
    sd.addEventListener('input', () => { spec().start_date = sd.value || null; changed(); });
    ed.addEventListener('input', () => { spec().end_date = ed.value || null; changed(); });
    render(dateBox, h('div', { class: 'form-grid' },
      h('div', null, h('label', { class: 'field-label', for: sd.id }, once ? 'Date' : 'First date (optional)'), sd, h('div', { class: 'help' }, once ? 'The one day this schedule runs.' : 'Leave empty to start right away.')),
      once ? null : h('div', null, h('label', { class: 'field-label', for: ed.id }, 'Last date (optional)'), ed, h('div', { class: 'help' }, 'Leave empty to repeat with no end.'))));
  }

  // ---- times
  function timePicker(which, label) {
    const key = which + '_time', offKey = which + '_offset';
    const mode = ['sunrise', 'sunset'].includes(spec()[key]) ? spec()[key] : 'clock';
    const wrap = h('fieldset', { style: { border: '1px solid var(--border)', borderRadius: 'var(--radius-sm)', padding: 'var(--space-3)' } });
    const lg = h('legend', { style: { fontWeight: 700, padding: '0 6px' } }, label);
    const modeSel = h('select', { 'aria-label': label + ' type' },
      [['clock', 'Clock time'], ['sunrise', 'Sunrise'], ['sunset', 'Sunset']].map(([v, l]) => h('option', { value: v, selected: v === mode }, l)));
    const t = h('input', { type: 'time', value: mode === 'clock' ? spec()[key] : '18:00', 'aria-label': label + ' clock time', hidden: mode !== 'clock' });
    const off = spec()[offKey] || 0;
    const mins = h('input', { type: 'number', min: 0, max: 720, step: 5, value: Math.abs(off), 'aria-label': label + ' offset in minutes' });
    const dir = h('select', { 'aria-label': label + ' offset direction' }, h('option', { value: 'after', selected: off >= 0 }, 'after'), h('option', { value: 'before', selected: off < 0 }, 'before'));
    const sunRow = h('div', { class: 'inline-field', hidden: mode === 'clock' }, mins, h('span', { class: 'small' }, 'min'), dir);
    const sunLabel = h('span', { class: 'small muted' }, '');
    const setOff = () => { const m = Math.max(0, Math.min(720, Math.round(Number(mins.value) || 0))); spec()[offKey] = dir.value === 'before' ? -m : m; changed(); };
    modeSel.addEventListener('change', () => {
      if (modeSel.value === 'clock') { spec()[key] = t.value || '18:00'; spec()[offKey] = 0; } else { spec()[key] = modeSel.value; setOffSilent(); }
      t.hidden = modeSel.value !== 'clock'; sunRow.hidden = modeSel.value === 'clock'; changed();
    });
    const setOffSilent = () => { const m = Math.max(0, Math.min(720, Math.round(Number(mins.value) || 0))); spec()[offKey] = dir.value === 'before' ? -m : m; };
    t.addEventListener('input', () => { if (t.value) { spec()[key] = t.value; changed(); } });
    mins.addEventListener('input', setOff); dir.addEventListener('change', setOff);
    wrap.append(lg, h('div', { class: 'time-pick' }, modeSel, t, sunRow), sunLabel);
    return wrap;
  }
  const timesBox = h('div');
  function drawTimes() { render(timesBox, h('div', { class: 'grid cols-2' }, timePicker('start', 'Starts at'), timePicker('end', 'Ends at')),
    h('p', { class: 'help' }, 'If the end time is not later than the start time, the window runs overnight into the next day (for example sunset to sunrise).')); }

  // ---- repeat
  const repBox = h('div'), repSub = h('div');
  function drawRepeat() {
    const rep = spec().repeat;
    const typeSel = h('select', { id: nextId('rt'), 'aria-label': 'Repeat type' }, [
      ['once', 'Once (a single date)'], ['daily', 'Daily'], ['weekly', 'Weekly (choose weekdays)'], ['monthly_day', 'Monthly on a day number'],
      ['monthly_nth', 'Monthly on the Nth weekday (e.g. first Saturday)'], ['yearly', 'Yearly (e.g. Oct 31)']].map(([v, l]) => h('option', { value: v, selected: v === rep.type }, l)));
    typeSel.addEventListener('change', () => {
      const t = typeSel.value;
      const old = spec().start_date;
      spec().repeat = t === 'once' ? { type: 'once' } : t === 'daily' ? { type: 'daily', interval: 1 } : t === 'weekly' ? { type: 'weekly', weekdays: [4, 5], interval: 1 }
        : t === 'monthly_day' ? { type: 'monthly_day', day: 1 } : t === 'monthly_nth' ? { type: 'monthly_nth', nth: 1, weekday: 5 } : { type: 'yearly', month: 10, day: 31 };
      if (t === 'once') spec().end_date = null;
      if (t === 'once' && !old) spec().start_date = new Date().toISOString().slice(0, 10);
      drawDates(); drawSub(); changed();
    });
    render(repBox, h('label', { class: 'field-label', for: typeSel.id }, 'Repeats'), typeSel);
    drawSub();
  }
  function intervalField(unit) {
    const rep = spec().repeat;
    const i = h('input', { type: 'number', min: 1, max: 52, value: rep.interval || 1, 'aria-label': 'Every how many ' + unit });
    i.addEventListener('input', () => { rep.interval = Math.max(1, Math.min(52, Math.round(Number(i.value) || 1))); changed(); });
    return h('label', { class: 'inline-field' }, 'Every', i, unit);
  }
  function drawSub() {
    const rep = spec().repeat;
    if (rep.type === 'once') render(repSub, h('p', { class: 'help' }, 'Runs once, on the date chosen above.'));
    else if (rep.type === 'daily') render(repSub, intervalField('day(s)'));
    else if (rep.type === 'weekly') {
      const chips = h('div', { class: 'chips wd-chips', role: 'group', 'aria-label': 'Weekdays' });
      const paint = () => render(chips, WD.map((n, d) => h('button', { type: 'button', class: 'chip', 'aria-pressed': String(rep.weekdays.includes(d)), 'aria-label': WD_FULL[d], onclick: () => {
        const i = rep.weekdays.indexOf(d); if (i >= 0) rep.weekdays.splice(i, 1); else rep.weekdays.push(d); rep.weekdays.sort(); paint(); changed();
      } }, n)));
      paint();
      render(repSub, chips, h('div', { style: { marginTop: '8px' } }, intervalField('week(s)')), h('p', { class: 'help' }, 'Pick at least one weekday.'));
    } else if (rep.type === 'monthly_day') {
      const sel = h('select', { 'aria-label': 'Day of the month' }, [...Array.from({ length: 31 }, (_, i) => h('option', { value: i + 1, selected: rep.day === i + 1 }, String(i + 1))), h('option', { value: -1, selected: rep.day === -1 }, 'Last day of the month')]);
      sel.addEventListener('change', () => { rep.day = Number(sel.value); changed(); });
      render(repSub, h('label', { class: 'inline-field' }, 'On day', sel, h('span', { class: 'small muted' }, 'of every month')), h('p', { class: 'help' }, 'Months without that day (like the 31st in June) use their last day instead.'));
    } else if (rep.type === 'monthly_nth') {
      const nth = h('select', { 'aria-label': 'Which one' }, [[1, 'first'], [2, 'second'], [3, 'third'], [4, 'fourth'], [-1, 'last']].map(([v, l]) => h('option', { value: v, selected: rep.nth === v }, l)));
      const wd = h('select', { 'aria-label': 'Weekday' }, WD_FULL.map((l, i) => h('option', { value: i, selected: rep.weekday === i }, l)));
      nth.addEventListener('change', () => { rep.nth = Number(nth.value); changed(); }); wd.addEventListener('change', () => { rep.weekday = Number(wd.value); changed(); });
      render(repSub, h('div', { class: 'row' }, 'On the', nth, wd, 'of every month'));
    } else if (rep.type === 'yearly') {
      const mo = h('select', { 'aria-label': 'Month' }, MONTHS.map((l, i) => h('option', { value: i + 1, selected: rep.month === i + 1 }, l)));
      const dy = h('input', { type: 'number', min: 1, max: 31, value: rep.day, 'aria-label': 'Day of the month' });
      mo.addEventListener('change', () => { rep.month = Number(mo.value); changed(); }); dy.addEventListener('input', () => { rep.day = Math.max(1, Math.min(31, Math.round(Number(dy.value) || 1))); changed(); });
      render(repSub, h('div', { class: 'row' }, 'Every year on', mo, dy));
    }
  }

  // ---- presets
  function setSpec(s, name) {
    st.spec = s; if (name && !st.name.trim()) { st.name = name; nameIn.value = name; }
    drawDates(); drawTimes(); drawRepeat(); changed();
  }
  function halloweenSpec() {
    const now = new Date(); let y = now.getFullYear(); if (now > new Date(y, 9, 31, 23, 59)) y += 1;
    return { start_date: y + '-10-25', end_date: y + '-10-31', start_time: 'sunset', end_time: '23:00', start_offset: 0, end_offset: 0, repeat: { type: 'daily', interval: 1 } };
  }
  const presets = h('div', { class: 'row' }, h('span', { class: 'small muted' }, 'Quick presets:'),
    h('button', { type: 'button', class: 'small', onclick: () => setSpec({ ...defaultSpec(), start_time: 'sunset', end_time: '23:00' }, 'Every night') }, 'Every night from sunset to 11 pm'),
    h('button', { type: 'button', class: 'small', onclick: () => setSpec({ ...defaultSpec(), repeat: { type: 'monthly_day', day: 1 } }, 'First of the month') }, 'First of every month'),
    h('button', { type: 'button', class: 'small', onclick: () => setSpec(halloweenSpec(), 'Halloween week') }, 'Halloween week'));

  // ---- live preview of upcoming windows
  const occBox = h('div'); const warnBox = h('div'); const sumBox = h('p', { class: 'muted' });
  let seq = 0, ctl = null;
  async function runPreview() {
    if (ctl) ctl.abort(); ctl = new AbortController(); const my = ++seq;
    try {
      const r = await api.post('/schedules/preview', { spec: spec(), count: 8 }, { signal: ctl.signal });
      if (my !== seq) return;
      sumBox.textContent = r.summary;
      render(warnBox, r.sun_estimated ? h('div', { class: 'banner warn' }, 'Sunrise and sunset times are rough estimates (about 7:00 and 19:00) because no location is set. ', h('a', { href: '#/settings' }, 'Set your latitude and longitude in Settings'), ' for accurate times.') : null);
      render(occBox, r.windows.length ? h('ul', { class: 'occ-list' }, r.windows.map((w) => h('li', null, fmtWindow(w.start, w.end)))) : h('p', { class: 'muted' }, 'No upcoming windows with these settings.'));
    } catch (e) { if (e.name === 'AbortError' || my !== seq) return; sumBox.textContent = ''; render(warnBox, null); render(occBox, h('div', { class: 'inline-error' }, e.message)); }
  }
  const previewDeb = debounce(runPreview, 400);
  ctx.onLeave(() => { previewDeb.cancel(); if (ctl) ctl.abort(); });

  // ---- save etc.
  async function save() {
    showErr(null);
    const name = st.name.trim();
    if (!name) { nameIn.classList.add('invalid'); nameIn.focus(); showErr(new Error('Give the schedule a name.')); return; }
    nameIn.classList.remove('invalid');
    if (!st.runlist_id) { showErr(new Error('Choose a run list. Create one first if there are none.')); return; }
    const body = { name, runlist_id: st.runlist_id, spec: st.spec, enabled: st.enabled, priority: st.priority };
    try {
      if (isNew) { const c = await api.post('/schedules', body); toast('Saved "' + c.name + '"', 'success'); go('#/schedule/' + c.id); }
      else {
        saved = await api.put('/schedules/' + saved.id, body);
        st = clone({ name: saved.name, runlist_id: saved.runlist_id, enabled: saved.enabled, priority: saved.priority, spec: saved.spec });
        toast('Saved', 'success'); changed();
      }
    } catch (e) { showErr(e); }
  }
  const buttons = [
    h('button', { class: 'primary', type: 'button', onclick: act(save) }, 'Save'),
    h('button', { type: 'button', onclick: () => dispatch() }, 'Revert'),
    isNew ? null : h('button', { type: 'button', onclick: act(async () => {
      const name = await promptDialog('Duplicate schedule', 'Name for the copy', saved.name + ' copy', 'Duplicate'); if (!name) return;
      const c = await api.post('/schedules/' + saved.id + '/duplicate', { name }); toast('Duplicated (the copy starts disabled)', 'success'); go('#/schedule/' + c.id);
    }) }, 'Duplicate'),
    isNew ? null : h('button', { class: 'danger', type: 'button', onclick: act(async () => {
      if (!await confirmDialog('Delete schedule?', 'Delete "' + saved.name + '"?')) return;
      try { await api.del('/schedules/' + saved.id); toast('Deleted'); go('#/schedules'); } catch (e) { showErr(e); }
    }) }, 'Delete')];

  render(app, h('p', null, h('a', { href: '#/schedules' }, '← All schedules')),
    h('div', { class: 'page-head' }, h('h1', null, isNew ? 'New schedule' : 'Edit schedule'), dirty), err,
    !runlists.length ? h('div', { class: 'banner warn' }, 'There are no run lists yet. ', h('a', { href: '#/runlist/new' }, 'Create a run list'), ' first, then come back.') : null,
    h('div', { class: 'editor-layout' },
      h('div', { class: 'stack' },
        h('div', { class: 'card stack' }, h('h2', null, 'What and why'),
          h('div', { class: 'form-grid' },
            h('div', null, h('label', { class: 'field-label', for: nameId }, 'Name'), nameIn),
            h('div', null, h('label', { class: 'field-label', for: rlId }, 'Run list to play'), rlSel),
            h('div', null, h('label', { class: 'field-label', for: prId }, 'Priority'), prIn, h('div', { class: 'help' }, 'If two schedules overlap, the higher number wins. 0 is fine for most.'))),
          switchEl(st.enabled, (v) => { st.enabled = v; changed(); }, 'Enabled')),
        h('div', { class: 'card stack' }, h('h2', null, 'When'), presets, repBox, repSub, dateBox, timesBox),
        h('div', { class: 'row' }, buttons)),
      h('div', { class: 'card sticky' }, h('h2', null, 'Next occurrences'), sumBox, warnBox, occBox,
        h('p', { class: 'help' }, 'Times are shown in ' + (meta ? 'the time zone set in Settings, or this device\'s zone if none is set' : 'your time zone') + '.'))));
  drawDates(); drawTimes(); drawRepeat(); changed();
  if (isNew) dirty.hidden = true;
  runPreview();
}
