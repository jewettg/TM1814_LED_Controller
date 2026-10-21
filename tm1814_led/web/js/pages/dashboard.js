import { api, getSettings, invalidate, getMeta } from '../api.js';
import { h, render, act, toast, toastError, pageHead, fmtDuration, fmtDurationWords, fmtDateTime, fmtWindow, fmtClock, kindBadge, debounce } from '../ui.js';
import { subscribe, setFast, refresh, getStatus } from '../status.js';
import { StripView, FramePlayer } from '../strip.js';

export async function dashboard(ctx) {
  const app = ctx.app;
  const [runlists, effects, settings, meta] = await Promise.all([api.get('/runlists'), api.get('/effects'), getSettings(true), getMeta()]);
  setFast(true);
  ctx.onLeave(() => setFast(false));

  // ---------- setup banner
  const setup = h('div', { class: 'banner setup', hidden: true },
    h('strong', null, 'Set up your controller'),
    h('p', null, 'No controller address has been entered yet. Open Settings to find your controller on the network and test the connection.'),
    h('a', { class: 'btn primary', href: '#/settings' }, 'Go to Settings'));

  // ---------- now playing
  const npName = h('h2', { style: { margin: 0 } }, 'Nothing playing');
  const npSub = h('div', { class: 'muted' });
  const npBadges = h('div', { class: 'row tight' });
  const npBar = h('div', { style: { width: '0%' } });
  const npProg = h('div', { class: 'progress', role: 'progressbar', 'aria-label': 'Progress of the current effect', 'aria-valuemin': 0, 'aria-valuemax': 100, 'aria-valuenow': 0 }, npBar);
  const npTime = h('div', { class: 'small muted' });
  const call = (path, body) => act(async () => { await api.post(path, body); await refresh(); });
  const bStop = h('button', { class: 'danger', type: 'button', onclick: call('/control/stop') }, 'Stop');
  const bNext = h('button', { type: 'button', onclick: call('/control/next') }, 'Next');
  const bPause = h('button', { type: 'button', onclick: call('/control/pause') }, 'Pause');
  const bResume = h('button', { class: 'primary', type: 'button', onclick: call('/control/resume') }, 'Resume');
  const stripCanvas = h('canvas', { 'aria-label': 'Live view of the strip', role: 'img' });
  const stripBox = h('div', { class: 'strip-box' }, stripCanvas);
  const stripNote = h('div', { class: 'help' });
  const nowCard = h('section', { class: 'card stack', 'aria-labelledby': 'np-h' },
    h('div', { class: 'row' }, h('div', { class: 'stack', style: { flex: '1 1 240px' } }, npName, npSub), npBadges),
    npProg, npTime,
    h('div', { class: 'row' }, bPause, bResume, bNext, bStop),
    h('div', null, stripBox, stripNote));
  npName.id = 'np-h';

  // ---------- quick controls
  const runSel = h('select', { id: 'dash-rl', 'aria-label': 'Run list to play' },
    h('option', { value: '' }, runlists.length ? 'Choose a run list...' : 'No run lists yet'),
    runlists.map((r) => h('option', { value: r.id }, r.name)));
  const playRl = h('button', { class: 'primary', type: 'button', onclick: act(async () => {
    if (!runSel.value) { toast('Pick a run list first.', 'error'); return; }
    await api.post('/control/play', { runlist_id: Number(runSel.value) }); await refresh();
  }) }, 'Play');

  const bright = h('input', { type: 'range', min: 0, max: 100, value: settings.brightness, id: 'dash-bright', 'aria-label': 'Master brightness' });
  const brightVal = h('span', { class: 'unit', style: { minWidth: '3em' } }, settings.brightness + '%');
  const sendBright = debounce(async () => {
    try { await api.post('/control/brightness', { value: Number(bright.value) }); invalidate('settings'); } catch (e) { toastError(e); }
  }, 250);
  bright.addEventListener('input', () => { brightVal.textContent = bright.value + '%'; sendBright(); });

  const colorIn = h('input', { type: 'color', id: 'dash-color', value: '#ff5a00', 'aria-label': 'Solid color' });
  const colorBtn = h('button', { type: 'button', onclick: act(async () => { await api.post('/control/color', { color: colorIn.value }); await refresh(); }) }, 'Show this color');
  const swatchRow = h('div', { class: 'row tight' }, ['#ff0000', '#ff5a00', '#ffd27a', '#00ff00', '#00b8d9', '#0000ff', '#8c00ff', '#ffffff'].map((c) =>
    h('button', { type: 'button', class: 'icon small', 'aria-label': 'Show ' + c, title: c, style: { background: c, borderColor: 'var(--border-strong)' },
      onclick: act(async () => { colorIn.value = c; await api.post('/control/color', { color: c }); await refresh(); }) })));

  const controls = h('section', { class: 'card stack', 'aria-label': 'Quick controls' },
    h('h2', null, 'Quick controls'),
    h('div', null, h('label', { class: 'field-label', for: 'dash-rl' }, 'Run a run list'), h('div', { class: 'row' }, h('div', { style: { flex: '1 1 200px' } }, runSel), playRl)),
    h('div', null, h('label', { class: 'field-label', for: 'dash-bright' }, 'Master brightness'),
      h('div', { class: 'slider-row' }, bright, brightVal),
      h('div', { class: 'help' }, 'Applies to everything the lights show.')),
    h('div', null, h('label', { class: 'field-label', for: 'dash-color' }, 'Quick solid color'),
      h('div', { class: 'row' }, colorIn, colorBtn), swatchRow),
    h('div', null, h('span', { class: 'field-label' }, 'Power'),
      h('div', { class: 'row' },
        h('button', { type: 'button', onclick: act(async () => { await api.post('/control/power', { on: true }); await refresh(); toast('Power on sent'); }) }, 'Power on'),
        h('button', { type: 'button', onclick: act(async () => { await api.post('/control/power', { on: false }); await refresh(); toast('Power off sent'); }) }, 'Power off')),
      h('div', { class: 'help' }, 'Switching power stops whatever is playing.')));

  // ---------- schedules
  const schedBox = h('div');
  const schedCard = h('section', { class: 'card', 'aria-label': 'Schedule' }, h('h2', null, 'Schedule'), schedBox);

  // ---------- controller
  const ctlBox = h('dl', { class: 'kv' });
  const ctlCard = h('section', { class: 'card', 'aria-label': 'Controller' }, h('h2', null, 'Controller'), ctlBox);

  // ---------- connection health: state, why, a "search now" button and the recent incidents
  const healthBox = h('div', { class: 'stack' });
  const ago = (ts) => (ts ? fmtDurationWords(Math.max(0, Date.now() / 1000 - ts)) + ' ago' : 'never');
  async function loadHealth() {
    let hl;
    try { hl = await api.get('/health'); } catch (e) { render(healthBox, h('p', { class: 'muted small' }, 'Could not load: ' + e.message)); return; }
    const colors = { online: 'ok', degraded: 'warn', offline: 'bad', unconfigured: 'warn', unknown: 'warn' };
    const dl = [h('dt', null, 'Connection'), h('dd', null, h('span', { class: 'dot ' + (colors[hl.state] || 'warn') }), ' ' + hl.state + (hl.unreachable_for_sec != null ? ' (not answering for ' + fmtDurationWords(hl.unreachable_for_sec) + ')' : (hl.since ? ' (for ' + fmtDurationWords(Math.max(0, Date.now() / 1000 - hl.since)) + ')' : ''))),
      h('dt', null, 'Last answered'), h('dd', null, ago(hl.last_ok)),
      h('dt', null, 'Controller ID'), h('dd', null, hl.mac ? hl.mac : 'not learned yet (it is learned the first time the controller answers)')];
    if (hl.failures) dl.push(h('dt', null, 'Not answering'), h('dd', null, hl.failures + ' check' + (hl.failures === 1 ? '' : 's') + ' in a row' + (hl.error ? ': ' + hl.error : '')));
    if (hl.diagnosis && hl.diagnosis.summary) dl.push(h('dt', null, 'Likely reason'), h('dd', null, hl.diagnosis.summary));
    dl.push(h('dt', null, 'Alerts'), h('dd', null, hl.alert_channels.length ? hl.alert_channels.join(' and ') + ' (' + hl.alerts.sent_24h + ' of ' + hl.alerts.limit + ' sent in the last 24 hours)' : h('span', null, 'none set up. ', h('a', { href: '#/settings' }, 'Set them up'))));
    const incidents = hl.incidents.length
      ? h('div', { class: 'table-wrap' }, h('table', null, h('thead', null, h('tr', null, h('th', null, 'Started'), h('th', null, 'Lasted'), h('th', null, 'What happened'))),
          h('tbody', null, hl.incidents.slice(0, 6).map((i) => h('tr', null,
            h('td', { style: { whiteSpace: 'nowrap' } }, fmtDateTime(new Date(i.started * 1000).toISOString())),
            h('td', null, i.open ? 'still going' : fmtDurationWords(i.duration_sec)),
            h('td', null, i.resolution || i.summary))))))
      : h('p', { class: 'muted small' }, 'No connection problems recorded.');
    render(healthBox, h('dl', { class: 'kv' }, ...dl), h('div', { class: 'row' }, h('button', { type: 'button', onclick: act(async () => {
      const r = await api.post('/health/recover');
      toast(r.moved ? 'The controller had moved from ' + r.was + ' to ' + r.host + '. Following it now.' : (r.state === 'online' ? 'The controller is answering at ' + r.host + '.' : 'Not found yet. The program keeps looking.'), r.moved || r.state === 'online' ? 'success' : 'error', 6000);
      await loadHealth(); await refresh();
    }) }, 'Search for the controller now')), h('h3', { style: { margin: '8px 0 0' } }, 'Recent connection problems'), incidents);
  }
  const healthCard = h('section', { class: 'card', 'aria-label': 'Connection health' }, h('h2', null, 'Connection health'), healthBox);
  const healthTimer = setInterval(() => { if (!document.hidden) loadHealth(); }, 10000);
  ctx.onLeave(() => clearInterval(healthTimer));
  loadHealth();

  // ---------- logs
  const logBox = h('div', { class: 'small', style: { fontFamily: 'var(--mono)' } });
  const logCard = h('section', { class: 'card', 'aria-label': 'Recent log' },
    h('div', { class: 'row' }, h('h2', { style: { margin: 0, flex: 1 } }, 'Recent activity'), h('a', { href: '#/logs' }, 'All logs')), logBox);

  render(app, pageHead('Dashboard'), setup,
    h('div', { class: 'grid cols-2' }, h('div', { class: 'stack' }, nowCard, schedCard), h('div', { class: 'stack' }, controls, ctlCard, healthCard)),
    h('div', { style: { marginTop: 'var(--space-4)' } }, logCard));

  // ---------- live strip: preview of whatever is playing (looped; it is an illustration, not a mirror)
  const view = new StripView(stripCanvas, { n: meta.led_count || 250, mode: 'dots' });
  const player = new FramePlayer(view);
  ctx.onLeave(() => { player.stop(); view.destroy(); });
  let previewKey = null, previewSeq = 0;
  async function updatePreview(st) {
    const it = st && st.state !== 'idle' ? st.item : null;
    const key = it && it.name ? it.name + '|' + it.kind : '';
    if (key === previewKey) return;
    previewKey = key; const seq = ++previewSeq;
    player.stop(); view.set(null);
    if (!it || !it.name) { stripNote.textContent = 'Nothing is playing.'; return; }
    let kind = it.kind, params = null;
    const eff = effects.find((e) => e.name === it.name && e.kind === it.kind);
    if (eff) params = eff.params;
    else { const m = /^Solid (#[0-9a-f]{6})$/i.exec(it.name); if (m && it.kind === 'solid') params = { color: m[1] }; }
    if (!params || it.kind === 'builtin') {
      stripNote.textContent = it.kind === 'builtin' ? 'This animation runs on the controller itself and cannot be drawn here.' : 'No preview available for this item.';
      return;
    }
    stripNote.textContent = 'Preview of ' + it.name + (it.native ? '' : ' (looped illustration)');
    try {
      const r = await api.post('/preview', { kind, params, seconds: 10, fps: 5 });
      if (seq !== previewSeq) return;
      player.load(r.frames, r.fps); player.play();
    } catch (e) { stripNote.textContent = 'Preview not available: ' + e.message; }
  }

  // ---------- status updates
  function onStatus(st, err) {
    if (!st) return;
    setup.hidden = !st.needs_setup;
    const playing = st.state !== 'idle', it = st.item || {};
    npName.textContent = playing ? (st.plan || 'Playing') : 'Nothing playing';
    npSub.textContent = playing ? (it.name ? 'Now: ' + it.name : '') : 'Pick a run list, an effect, or a color to start.';
    render(npBadges,
      playing ? h('span', { class: 'badge ' + (st.state === 'paused' ? 'warn' : 'ok') }, st.state === 'paused' ? 'Paused' : 'Running') : h('span', { class: 'badge gray' }, 'Idle'),
      playing && it.name ? kindBadge(it.family) : null,
      playing && st.source ? h('span', { class: 'badge info', title: 'Who started this' }, st.source === 'schedule' ? 'From schedule' : 'Started manually') : null);
    if (playing && it.duration) {
      const pct = Math.min(100, 100 * (it.elapsed || 0) / it.duration);
      npBar.style.width = pct + '%'; npProg.setAttribute('aria-valuenow', Math.round(pct));
      npTime.textContent = 'Item ' + (it.index + 1) + ' of ' + it.count + ' • ' + fmtDuration(it.elapsed || 0) + ' / ' + fmtDuration(it.duration);
    } else {
      npBar.style.width = playing ? '100%' : '0%'; npProg.setAttribute('aria-valuenow', playing ? 100 : 0);
      npTime.textContent = playing ? 'Item ' + ((it.index || 0) + 1) + ' of ' + (it.count || 1) + ' • plays until stopped' : '';
    }
    bStop.disabled = !playing; bNext.disabled = !playing || (it.count || 0) < 2 && !st.loop;
    bPause.hidden = !playing || st.state === 'paused'; bResume.hidden = st.state !== 'paused';
    updatePreview(st);

    // schedule
    const parts = [];
    parts.push(st.active_schedule ? h('p', null, h('span', { class: 'badge ok' }, 'Active now'), ' ', st.active_schedule.name)
      : h('p', { class: 'muted' }, 'No schedule is active right now.'));
    if (st.upcoming && st.upcoming.length) {
      parts.push(h('h3', null, 'Coming up'), h('ul', { class: 'occ-list' }, st.upcoming.map((u) =>
        h('li', null, h('strong', null, u.name), ' – ', u.runlist, h('br'), h('span', { class: 'small muted' }, fmtWindow(u.start, u.end))))));
    } else parts.push(h('p', { class: 'muted small' }, 'No upcoming windows. ', h('a', { href: '#/schedules' }, 'Manage schedules')));
    render(schedBox, parts);

    // controller
    const c = st.controller;
    const row = (k, v) => [h('dt', null, k), h('dd', null, v)];
    render(ctlBox,
      row('Address', (c.host || 'not set') + (c.host ? ':' + c.port : '')),
      row('Connection', h('span', null, h('span', { class: 'dot ' + (c.last_error ? 'bad' : 'ok') }), c.last_error ? 'Cannot reach it' : (c.connected ? 'Connected' : 'Ready (connects when needed)'), c.simulated ? h('span', { class: 'badge warn', style: { marginLeft: '8px' } }, 'Simulated') : null)),
      row('LED count', String(c.led_count)),
      row('Power', c.power === null || c.power === undefined ? 'unknown' : (c.power ? 'On' : 'Off')),
      row('Firmware', c.firmware !== null && c.firmware !== undefined ? 'v' + c.firmware + (c.model ? ' (model ' + c.model + ')' : '') : 'unknown'),
      row('Commands per second', String(c.commands_per_second !== undefined ? Number(c.commands_per_second).toFixed(1) : '-')),
      row('Frames per second', st.state !== 'idle' && st.fps_actual ? Number(st.fps_actual).toFixed(1) + ' (target ' + st.fps_target + ')' : '-'),
      row('Last error', c.last_error ? h('span', { style: { color: 'var(--danger)' } }, c.last_error) : 'none'));
    if (document.activeElement !== bright && Number(bright.value) !== c.brightness && c.brightness !== undefined && !sendBright.pending) {
      // keep the slider in step with changes made elsewhere, but never while the user is using it
      if (document.activeElement !== bright) { bright.value = c.brightness; brightVal.textContent = c.brightness + '%'; }
    }
  }
  const unsub = subscribe(onStatus);
  ctx.onLeave(unsub);
  if (!getStatus()) await refresh();

  async function loadLogs() {
    try {
      const rows = await api.get('/logs?count=10');
      render(logBox, rows.length ? rows.map((r) => h('div', { class: 'log-' + r.level, style: { padding: '2px 4px' } }, fmtClock(r.ts) + '  ' + r.level.slice(0, 4) + '  ' + r.msg)) : h('span', { class: 'muted' }, 'Nothing logged yet.'));
    } catch (e) { /* non-fatal */ }
  }
  loadLogs();
  const t = setInterval(loadLogs, 5000);
  ctx.onLeave(() => clearInterval(t));
}
