import { api } from '../api.js';
import { h, render, pageHead, loading, fmtClock, switchEl, emptyState } from '../ui.js';

export async function logs(ctx) {
  render(ctx.app, loading());
  let level = 'ALL', auto = true, rows = [];
  const body = h('tbody'); const info = h('div', { class: 'muted small' });
  const sel = h('select', { 'aria-label': 'Filter by level', style: { width: 'auto' } }, ['ALL', 'INFO', 'WARNING', 'ERROR'].map((l) => h('option', { value: l }, l === 'ALL' ? 'All levels' : l)));
  sel.addEventListener('change', () => { level = sel.value; draw(); });
  const wrap = h('div');
  function draw() {
    const shown = rows.filter((r) => level === 'ALL' || r.level === level || (level === 'ERROR' && r.level === 'CRITICAL')).slice().reverse();
    info.textContent = shown.length + ' of ' + rows.length + ' lines (newest first)';
    if (!shown.length) { render(wrap, emptyState('No log lines to show.')); return; }
    render(body, shown.map((r) => h('tr', { class: 'log-' + r.level }, h('td', { style: { whiteSpace: 'nowrap' } }, fmtClock(r.ts)), h('td', null, h('strong', null, r.level)), h('td', { class: 'mono' }, r.msg))));
    render(wrap, h('div', { class: 'table-wrap' }, h('table', null, h('thead', null, h('tr', null, h('th', null, 'Time'), h('th', null, 'Level'), h('th', null, 'Message'))), body)));
  }
  async function load() { try { rows = await api.get('/logs?count=300'); draw(); } catch (e) { info.textContent = 'Could not load logs: ' + e.message; } }
  render(ctx.app, pageHead('Logs', h('div', { class: 'row' }, sel, switchEl(true, (v) => { auto = v; }, 'Auto-refresh'), h('button', { type: 'button', onclick: load }, 'Refresh now'))), info, wrap);
  await load();
  const t = setInterval(() => { if (auto && !document.hidden) load(); }, 3000);
  ctx.onLeave(() => clearInterval(t));
}
