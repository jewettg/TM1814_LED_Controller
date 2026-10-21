// Small DOM + formatting helpers shared by all pages. No innerHTML anywhere: text always goes through textContent.

/** Create an element: h('div', {class:'x', onclick: fn}, child, 'text', ...). */
export function h(tag, attrs, ...kids) {
  const el = document.createElement(tag);
  if (attrs) {
    for (const [k, v] of Object.entries(attrs)) {
      if (v === undefined || v === null || v === false) continue;
      if (k === 'class') el.className = v;
      else if (k.startsWith('on') && typeof v === 'function') el.addEventListener(k.slice(2), v);
      else if (k === 'value') el.value = v;
      else if (k === 'checked' || k === 'disabled' || k === 'selected' || k === 'hidden') el[k] = !!v;
      else if (k === 'dataset') Object.assign(el.dataset, v);
      else if (k === 'style' && typeof v === 'object') Object.assign(el.style, v);
      else el.setAttribute(k, v === true ? '' : v);
    }
  }
  append(el, kids);
  return el;
}
export function append(el, kids) {
  for (const k of kids.flat(Infinity)) {
    if (k === null || k === undefined || k === false) continue;
    el.append(k instanceof Node ? k : document.createTextNode(String(k)));
  }
  return el;
}
export function clear(el) { while (el.firstChild) el.removeChild(el.firstChild); return el; }
export function render(el, ...kids) { clear(el); return append(el, kids); }

let uid = 0;
export const nextId = (p = 'id') => p + '-' + (++uid);

// ---------------------------------------------------------------- toasts
export function toast(message, type = 'info', ms = 4500) {
  const box = document.getElementById('toasts');
  const t = h('div', { class: 'toast ' + type },
    h('span', { class: 'msg' }, message),
    h('button', { type: 'button', 'aria-label': 'Dismiss', onclick: () => t.remove() }, '×'));
  box.append(t);
  if (ms) setTimeout(() => t.remove(), type === 'error' ? Math.max(ms, 7000) : ms);
}
export const toastError = (e) => toast(e && e.message ? e.message : String(e), 'error');

// ---------------------------------------------------------------- dialogs
function openDialog(dlg) {
  document.body.append(dlg);
  dlg.addEventListener('close', () => dlg.remove());
  dlg.showModal();
  return dlg;
}

/** Confirm dialog -> Promise<boolean>. */
export function confirmDialog(title, message, okLabel = 'Delete', danger = true) {
  return new Promise((resolve) => {
    let result = false;
    const ok = h('button', { class: danger ? 'danger solid' : 'primary', type: 'button', onclick: () => { result = true; dlg.close(); } }, okLabel);
    const dlg = h('dialog', null,
      h('h2', null, title), h('p', null, message),
      h('div', { class: 'dialog-actions' },
        h('button', { type: 'button', onclick: () => dlg.close() }, 'Cancel'), ok));
    dlg.addEventListener('close', () => resolve(result));
    openDialog(dlg);
    ok.focus();
  });
}

/** Text prompt -> Promise<string|null>. */
export function promptDialog(title, label, initial = '', okLabel = 'OK') {
  return new Promise((resolve) => {
    let result = null;
    const id = nextId('p');
    const input = h('input', { type: 'text', id, value: initial, maxlength: 120 });
    const submit = () => { const v = input.value.trim(); if (!v) { input.classList.add('invalid'); input.focus(); return; } result = v; dlg.close(); };
    const form = h('form', { onsubmit: (e) => { e.preventDefault(); submit(); } },
      h('h2', null, title), h('label', { class: 'field-label', for: id }, label), input,
      h('div', { class: 'dialog-actions' },
        h('button', { type: 'button', onclick: () => dlg.close() }, 'Cancel'),
        h('button', { type: 'submit', class: 'primary' }, okLabel)));
    const dlg = h('dialog', null, form);
    dlg.addEventListener('close', () => resolve(result));
    openDialog(dlg);
    input.focus(); input.select();
  });
}

/** Generic modal with arbitrary content; returns {dlg, close}. */
export function modal(title, content, { wide = false, actions = null } = {}) {
  const dlg = h('dialog', { class: wide ? 'wide' : '' }, h('h2', null, title), content,
    h('div', { class: 'dialog-actions' }, actions || h('button', { type: 'button', onclick: () => dlg.close() }, 'Close')));
  openDialog(dlg);
  return { dlg, close: () => dlg.close() };
}

// ---------------------------------------------------------------- buttons that run async work
/** Disable a button while fn runs; errors become toasts. Returns the fn's result (or undefined on error). */
export async function busy(btn, fn) {
  const buttons = btn ? [].concat(btn) : [];
  buttons.forEach((b) => { b.disabled = true; });
  try { return await fn(); }
  catch (e) { if (e.name !== 'AbortError') toastError(e); }
  finally { buttons.forEach((b) => { b.disabled = false; }); }
}
/** Click handler factory: onclick: act(async (btn) => ...) */
export function act(fn) {
  return (ev) => { const b = ev.currentTarget; return busy(b, () => fn(b, ev)); };
}

// ---------------------------------------------------------------- formatting
export function fmtDuration(sec) {
  sec = Math.max(0, Math.round(sec || 0));
  const hh = Math.floor(sec / 3600), mm = Math.floor((sec % 3600) / 60), ss = sec % 60;
  if (hh) return hh + ':' + String(mm).padStart(2, '0') + ':' + String(ss).padStart(2, '0');
  return mm + ':' + String(ss).padStart(2, '0');
}
export function fmtDurationWords(sec) {
  sec = Math.round(sec || 0);
  const hh = Math.floor(sec / 3600), mm = Math.floor((sec % 3600) / 60), ss = sec % 60;
  const parts = [];
  if (hh) parts.push(hh + ' h'); if (mm) parts.push(mm + ' min'); if (ss || !parts.length) parts.push(ss + ' s');
  return parts.join(' ');
}

let displayTz = ''; // IANA zone from Settings; '' = this browser's zone
export function setDisplayTz(tz) { displayTz = tz || ''; }
function fmt(opts, d) {
  try { return new Intl.DateTimeFormat(undefined, displayTz ? { ...opts, timeZone: displayTz } : opts).format(d); }
  catch (e) { return new Intl.DateTimeFormat(undefined, opts).format(d); }
}
export function fmtDateTime(iso) {
  return fmt({ weekday: 'short', month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' }, new Date(iso));
}
export function fmtTime(iso) { return fmt({ hour: 'numeric', minute: '2-digit' }, new Date(iso)); }
export function fmtDay(iso) { return fmt({ weekday: 'short', month: 'short', day: 'numeric', year: 'numeric' }, new Date(iso)); }
/** "Fri, Oct 16, 6:12 PM to 11:00 PM" (adds the end date if it falls on another day). */
export function fmtWindow(startIso, endIso) {
  const a = new Date(startIso), b = new Date(endIso);
  const sameDay = fmt({ year: 'numeric', month: 'numeric', day: 'numeric' }, a) === fmt({ year: 'numeric', month: 'numeric', day: 'numeric' }, b);
  return fmtDateTime(startIso) + ' to ' + (sameDay ? fmtTime(endIso) : fmtDateTime(endIso));
}
/** One upcoming schedule window: struck through (with the reason) when a higher-priority schedule overrides it. */
export function windowItem(u) {
  const out = u.status === 'overridden', part = u.status === 'partial';
  const why = (u.overridden_by || []).map((o) => (o.name + ' (priority ' + o.priority + ') ' + fmtTime(o.start) + ' to ' + fmtTime(o.end)));
  return h('li', { class: 'win' + (out ? ' overridden' : '') },
    h('span', { class: out ? 'struck' : '' }, h('strong', null, u.name), ' – ', u.runlist, h('br'), h('span', { class: 'small muted' }, fmtWindow(u.start, u.end))),
    ' ', h('span', { class: 'badge info' }, 'Priority ' + u.priority),
    out ? h('span', { class: 'badge warn', style: { marginLeft: '6px' } }, 'Overridden') : null,
    part ? h('span', { class: 'badge warn', style: { marginLeft: '6px' } }, 'Partly overridden') : null,
    why.length ? h('div', { class: 'small muted' }, (out ? 'Will not run: ' : 'Paused during: ') + why.join('; ')) : null);
}
export function fmtStamp(ts) {  // date and time with seconds, in the display time zone (for logs)
  return fmt({ month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit', second: '2-digit' }, new Date(ts * 1000));
}
export function fmtClock(ts) {
  return new Date(ts * 1000).toLocaleTimeString(undefined, { hour: 'numeric', minute: '2-digit', second: '2-digit' });
}

export function pageHead(title, ...actions) {
  return h('div', { class: 'page-head' }, h('h1', null, title), h('div', { class: 'actions' }, ...actions));
}
const FAMILY_BADGE = {
  native: ['Controller-native', 'Runs on the controller itself; keeps running even if this program stops.'],
  fixed: ['Fixed', 'A still picture painted once; the lights simply stay that way (no animation).'],
  animated: ['Animated', 'Rendered by this program (about 5 frames per second); needs it running.'],
};
// family: 'native' | 'fixed' | 'animated' (a boolean is accepted too: true = native)
export function kindBadge(family) {
  const f = family === true ? 'native' : (family === false || !family ? 'animated' : family);
  const [label, title] = FAMILY_BADGE[f] || FAMILY_BADGE.animated;
  return h('span', { class: 'badge ' + f, title }, label);
}
export function emptyState(text, ...actions) { return h('div', { class: 'empty' }, h('p', null, text), ...actions); }
export function loading(text = 'Loading...') { return h('div', { class: 'loading' }, text); }
export function field(label, input, help, id) {
  return h('div', { class: 'field' }, h('label', { class: 'field-label', for: id }, label), input, help ? h('div', { class: 'help' }, help) : null);
}
export function switchEl(checked, onchange, label) {
  const input = h('input', { type: 'checkbox', checked, onchange: (e) => onchange(e.target.checked) });
  return h('label', { class: 'switch' }, input, h('span', { class: 'track' }), label ? h('span', null, label) : null);
}
export function debounce(fn, ms) {
  let t = null;
  const d = (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); };
  d.cancel = () => clearTimeout(t);
  return d;
}
export const HEX = /^#[0-9a-fA-F]{6}$/;
export function normHex(s) {
  s = String(s || '').trim();
  if (/^[0-9a-fA-F]{6}$/.test(s)) s = '#' + s;
  if (/^#[0-9a-fA-F]{3}$/.test(s)) s = '#' + s.slice(1).split('').map((c) => c + c).join('');
  return HEX.test(s) ? s.toLowerCase() : null;
}
