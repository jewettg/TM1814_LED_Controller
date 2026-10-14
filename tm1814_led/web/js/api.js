// Thin wrapper around fetch for the JSON API. Errors become ApiError with a friendly message.
export class ApiError extends Error {
  constructor(message, status) { super(message); this.status = status; }
}

function detailText(d) {
  if (!d) return '';
  if (typeof d === 'string') return d;
  if (Array.isArray(d)) { // FastAPI validation errors
    return d.map((e) => (e.loc ? e.loc.filter((x) => x !== 'body').join('.') + ': ' : '') + (e.msg || '')).join('; ');
  }
  return JSON.stringify(d);
}

export async function request(method, path, body, opts = {}) {
  const init = { method, headers: {}, signal: opts.signal };
  if (body !== undefined) { init.headers['Content-Type'] = 'application/json'; init.body = JSON.stringify(body); }
  let res;
  try {
    res = await fetch('/api' + path, init);
  } catch (e) {
    if (e.name === 'AbortError') throw e;
    throw new ApiError('Cannot reach the LED program. Is it running? (' + e.message + ')', 0);
  }
  // 401 normally means "signed out"; for /password it means the current password typed was wrong, so stay on the page
  if (res.status === 401 && path !== '/password') { window.location.href = '/login'; throw new ApiError('Please sign in.', 401); }
  if (res.status === 204) return null;
  let data = null;
  const text = await res.text();
  try { data = text ? JSON.parse(text) : null; } catch (e) { data = null; }
  if (!res.ok) {
    throw new ApiError(detailText(data && data.detail) || res.statusText || ('Error ' + res.status), res.status);
  }
  return data;
}

export const api = {
  get: (p, o) => request('GET', p, undefined, o),
  post: (p, b, o) => request('POST', p, b === undefined ? {} : b, o),
  put: (p, b, o) => request('PUT', p, b, o),
  del: (p, o) => request('DELETE', p, undefined, o),
};

// Small cache for rarely-changing reference data.
const cache = {};
export async function cached(key, loader, force = false) {
  if (force || !cache[key]) cache[key] = loader();
  try { return await cache[key]; } catch (e) { delete cache[key]; throw e; }
}
export const getMeta = (force) => cached('meta', () => api.get('/meta'), force);
export const getKinds = (force) => cached('kinds', () => api.get('/effect-kinds'), force);
export const getSettings = (force) => cached('settings', () => api.get('/settings'), force);
export function invalidate(key) { delete cache[key]; }
