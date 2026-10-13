import { route, dispatch } from './router.js';
import { api, getMeta } from './api.js';
import { h, render, act } from './ui.js';
import { subscribe, startPolling, refresh } from './status.js';
import { dashboard } from './pages/dashboard.js';
import { catalog } from './pages/catalog.js';
import { effectEditor } from './pages/effect.js';
import { runlists, runlistEditor } from './pages/runlists.js';
import { schedules, scheduleEditor } from './pages/schedules.js';
import { settings } from './pages/settings.js';
import { logs } from './pages/logs.js';
import { help } from './pages/help.js';

route('dashboard', dashboard);
route('catalog', catalog);
route('effect', effectEditor);
route('runlists', runlists);
route('runlist', runlistEditor);
route('schedules', schedules);
route('schedule', scheduleEditor);
route('settings', settings);
route('logs', logs);
route('help', help);

// ---- navigation toggle on narrow screens
const nav = document.getElementById('nav'), toggle = document.getElementById('nav-toggle');
toggle.addEventListener('click', () => {
  const open = nav.classList.toggle('open');
  toggle.setAttribute('aria-expanded', String(open));
});

// ---- global status bar (every page). Elements are built once and updated in place so focus is never lost.
const bar = document.getElementById('statusbar');
const barDot = h('span', { class: 'dot' }), barConn = h('span', null), barNow = h('span', { class: 'now' }, 'Connecting...');
const barStop = h('button', { class: 'small danger', type: 'button', hidden: true, onclick: act(async () => { await api.post('/control/stop'); await refresh(); }) }, 'Stop');
const barConnWrap = h('span', null, barDot, barConn);
render(bar, barConnWrap, barNow, barStop);
subscribe((st, err) => {
  if (err && !st) {
    barDot.className = 'dot bad'; barConn.textContent = 'Cannot reach the LED program'; barNow.textContent = err.message; barStop.hidden = true;
    return;
  }
  const c = st.controller, playing = st.state !== 'idle';
  barDot.className = 'dot ' + (c.last_error ? 'bad' : (c.host ? 'ok' : 'warn'));
  barConnWrap.title = c.last_error || (c.connected ? 'Controller connected' : 'The program connects to the controller when it has something to send');
  barConn.textContent = c.simulated ? 'Simulated controller' : (c.host ? (c.last_error ? 'Cannot reach ' + c.host : (c.connected ? 'Connected to ' + c.host : 'Controller ' + c.host + ' (ready)')) : 'No controller set');
  if (!playing) barNow.textContent = 'Nothing playing';
  else {
    const it = st.item && st.item.name ? st.item.name : '';
    barNow.textContent = (st.state === 'paused' ? 'Paused: ' : 'Playing: ') + (st.plan || '') + (it && it !== st.plan ? ' / ' + it : '');
  }
  barStop.hidden = !playing;
});
// ---- "Log out" link, only shown when a password is set
fetch('/api/auth', { credentials: 'same-origin' }).then((r) => r.json()).then((a) => {
  const link = document.getElementById('logout');
  if (!a.required) return;
  link.hidden = false;
  link.addEventListener('click', async (e) => {
    e.preventDefault();
    try { await fetch('/api/logout', { method: 'POST', credentials: 'same-origin' }); } catch (err) { /* sign out anyway */ }
    window.location.href = '/login';
  });
}).catch(() => {});
startPolling();
getMeta().catch(() => {});
window.addEventListener('unhandledrejection', (e) => { console.error(e.reason); });
dispatch();
