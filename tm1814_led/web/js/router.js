// Hash router. Routes: #/name or #/name/arg, with an optional ?query part.
const routes = [];
let current = null; // { cleanup }

export function route(name, handler) { routes.push({ name, handler }); }

export function parseHash() {
  const raw = location.hash.replace(/^#\/?/, '') || 'dashboard';
  const [path, qs] = raw.split('?');
  const parts = path.split('/').filter(Boolean).map(decodeURIComponent);
  return { name: parts[0] || 'dashboard', arg: parts[1], query: Object.fromEntries(new URLSearchParams(qs || '')) };
}
export function go(hash) { if (location.hash === hash) dispatch(); else location.hash = hash; }

// Pages may register cleanup (timers, listeners) via ctx.onLeave(fn).
export async function dispatch() {
  const r = parseHash();
  if (current) { for (const f of current.leave) { try { f(); } catch (e) { console.error(e); } } }
  const leave = [];
  current = { leave };
  const app = document.getElementById('app');
  const found = routes.find((x) => x.name === r.name);
  document.querySelectorAll('.nav a').forEach((a) => {
    const on = found && a.dataset.route === (r.name === 'effect' ? 'catalog' : r.name === 'runlist' ? 'runlists' : r.name === 'schedule' ? 'schedules' : r.name);
    if (on) a.setAttribute('aria-current', 'page'); else a.removeAttribute('aria-current');
  });
  document.getElementById('nav').classList.remove('open');
  document.getElementById('nav-toggle').setAttribute('aria-expanded', 'false');
  app.replaceChildren();
  window.scrollTo(0, 0);
  if (!found) { location.hash = '#/dashboard'; return; }
  const ctx = { route: r, app, onLeave: (f) => leave.push(f), token: current };
  try { await found.handler(ctx); } catch (e) { console.error(e); const d = document.createElement('div'); d.className = 'banner error'; d.textContent = 'Could not load this page: ' + e.message; app.replaceChildren(d); }
}
export const isCurrent = (ctx) => current === ctx.token;
window.addEventListener('hashchange', dispatch);
