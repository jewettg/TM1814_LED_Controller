// Logs: a persistent history (kept for the retention period set in Settings) with a choosable range, level and search.
import { api } from '../api.js';
import { h, render, pageHead, loading, fmtStamp, switchEl, emptyState, nextId } from '../ui.js';

const RANGES = [['1h', 'Last hour', 3600], ['24h', 'Last 24 hours', 86400], ['7d', 'Last 7 days', 7 * 86400],
  ['30d', 'Last 30 days', 30 * 86400], ['all', 'Everything kept', 0], ['custom', 'Custom range...', 0]];
const LEVELS = [['', 'All messages'], ['alerts', 'Alerts only'], ['WARNING', 'Warnings and errors'], ['ERROR', 'Errors only']];
const LIMITS = [500, 1000, 2000, 5000];

function localInputValue(date) {   // value for <input type="datetime-local">, in this browser's time
  const p = (n) => String(n).padStart(2, '0');
  return date.getFullYear() + '-' + p(date.getMonth() + 1) + '-' + p(date.getDate()) + 'T' + p(date.getHours()) + ':' + p(date.getMinutes());
}

export async function logs(ctx) {
  render(ctx.app, loading());
  const st = { range: '24h', level: '', q: '', limit: 1000, auto: true,
    from: localInputValue(new Date(Date.now() - 86400000)), to: localInputValue(new Date()) };
  let data = null;

  const id = (n) => nextId(n);
  const rangeSel = h('select', { id: id('range'), 'aria-label': 'Time range', style: { width: 'auto' } }, RANGES.map(([v, l]) => h('option', { value: v, selected: st.range === v }, l)));
  const fromIn = h('input', { type: 'datetime-local', id: id('from'), value: st.from, 'aria-label': 'From', style: { width: 'auto' } });
  const toIn = h('input', { type: 'datetime-local', id: id('to'), value: st.to, 'aria-label': 'To', style: { width: 'auto' } });
  const customBox = h('div', { class: 'row', hidden: true }, h('label', null, 'From ', fromIn), h('label', null, 'to ', toIn),
    h('span', { class: 'muted small' }, '(this browser\'s time)'));
  const levelSel = h('select', { id: id('level'), 'aria-label': 'Which messages', style: { width: 'auto' } }, LEVELS.map(([v, l]) => h('option', { value: v }, l)));
  const search = h('input', { type: 'search', placeholder: 'Search the messages', 'aria-label': 'Search messages', style: { width: '16em' } });
  const limitSel = h('select', { 'aria-label': 'Most lines to show', style: { width: 'auto' } }, LIMITS.map((n) => h('option', { value: String(n), selected: n === st.limit }, 'up to ' + n + ' lines')));
  const info = h('div', { class: 'muted small', 'aria-live': 'polite' });
  const warn = h('div', { class: 'inline-note', hidden: true, role: 'status' });
  const wrap = h('div');

  function window_() {
    if (st.range === 'custom') {
      const a = new Date(fromIn.value).getTime(), b = new Date(toIn.value).getTime();
      return { since: Number.isNaN(a) ? null : a / 1000, until: Number.isNaN(b) ? null : b / 1000 };
    }
    const r = RANGES.find((x) => x[0] === st.range);
    return { since: r[2] ? Date.now() / 1000 - r[2] : null, until: null };
  }

  function query() {
    const { since, until } = window_();
    const p = new URLSearchParams({ limit: String(st.limit) });
    if (since != null) p.set('since', String(since));
    if (until != null) p.set('until', String(until));
    if (st.level === 'alerts') p.set('q', 'ALERT');          // alert activity is logged with the word ALERT
    else {
      if (st.level) p.set('level', st.level);
      if (st.q) p.set('q', st.q);
    }
    return p;
  }

  function csv() {
    const esc = (v) => '"' + String(v).replace(/"/g, '""') + '"';
    const lines = ['time,level,logger,message'].concat(data.entries.map((r) => [new Date(r.ts * 1000).toISOString(), r.level, r.logger, r.msg].map(esc).join(',')));
    const a = h('a', { href: URL.createObjectURL(new Blob([lines.join('\n') + '\n'], { type: 'text/csv' })), download: 'tm1814-led-logs.csv' });
    document.body.appendChild(a); a.click(); a.remove();
  }

  function draw() {
    if (!data) return;
    let rows = data.entries.slice().reverse();     // newest first
    if (st.level === 'alerts' && st.q) rows = rows.filter((r) => r.msg.toLowerCase().includes(st.q.toLowerCase()));
    const kept = data.oldest ? 'Oldest line kept: ' + fmtStamp(data.oldest) + '. ' : '';
    info.textContent = 'Showing ' + rows.length + ' of ' + data.total + ' matching line' + (data.total === 1 ? '' : 's') +
      ' (newest first). ' + kept + 'Logs are kept for ' + data.retention_days + ' days; change that in Settings.';
    warn.hidden = !data.truncated;
    warn.textContent = data.truncated ? 'There are more lines than the limit, so only the newest ' + data.limit + ' are shown. Narrow the time range, search, or raise the limit.' : '';
    if (!rows.length) { render(wrap, emptyState('No log lines match this range and filter.')); return; }
    render(wrap, h('div', { class: 'table-wrap' }, h('table', null,
      h('thead', null, h('tr', null, h('th', null, 'Time'), h('th', null, 'Level'), h('th', null, 'Message'))),
      h('tbody', null, rows.map((r) => h('tr', { class: 'log-' + r.level },
        h('td', { style: { whiteSpace: 'nowrap' } }, fmtStamp(r.ts)), h('td', null, h('strong', null, r.level)), h('td', { class: 'mono' }, r.msg)))))));
  }

  async function load() {
    try { data = await api.get('/logs/query?' + query().toString()); draw(); }
    catch (e) { info.textContent = 'Could not load logs: ' + e.message; }
  }

  rangeSel.addEventListener('change', () => { st.range = rangeSel.value; customBox.hidden = st.range !== 'custom'; load(); });
  fromIn.addEventListener('change', load); toIn.addEventListener('change', load);
  levelSel.addEventListener('change', () => { st.level = levelSel.value; load(); });
  limitSel.addEventListener('change', () => { st.limit = Number(limitSel.value); load(); });
  let t = null;
  search.addEventListener('input', () => { clearTimeout(t); t = setTimeout(() => { st.q = search.value.trim(); load(); }, 300); });

  render(ctx.app, pageHead('Logs', h('div', { class: 'row' },
    switchEl(true, (v) => { st.auto = v; }, 'Auto-refresh'), h('button', { type: 'button', onclick: load }, 'Refresh now'),
    h('button', { type: 'button', onclick: () => { if (data) csv(); } }, 'Download as CSV'))),
  h('div', { class: 'card' }, h('div', { class: 'row' }, h('label', null, 'Show ', rangeSel), levelSel, search, limitSel), customBox),
  info, warn, wrap);
  await load();
  const timer = setInterval(() => { if (st.auto && st.range !== 'custom' && !document.hidden) load(); }, 5000);
  ctx.onLeave(() => { clearInterval(timer); clearTimeout(t); });
}
