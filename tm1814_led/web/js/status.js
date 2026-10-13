// Shared /api/status poller. The dashboard asks for fast polling; other pages poll slowly for the status bar.
import { api } from './api.js';
import { setDisplayTz, getSettingsSafe } from './tzsync.js';

let last = null, error = null, timer = null, fast = false, inflight = false;
const subs = new Set();

export const getStatus = () => last;
export function subscribe(fn) { subs.add(fn); if (last || error) fn(last, error); return () => subs.delete(fn); }
export function setFast(v) { fast = v; if (v) refresh(); }

export async function refresh() {
  if (inflight) return last;
  inflight = true;
  try { last = await api.get('/status'); error = null; }
  catch (e) { error = e; }
  finally { inflight = false; }
  subs.forEach((f) => { try { f(last, error); } catch (e) { console.error(e); } });
  return last;
}
export function startPolling() {
  const loop = async () => {
    if (!document.hidden) await refresh();
    timer = setTimeout(loop, fast ? 1500 : 4000);
  };
  clearTimeout(timer); loop();
  document.addEventListener('visibilitychange', () => { if (!document.hidden) refresh(); });
  getSettingsSafe().then((s) => setDisplayTz(s && s.timezone));
}
