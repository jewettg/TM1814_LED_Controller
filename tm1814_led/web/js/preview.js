// A live preview panel: draws the strip, loops the frames returned by POST /api/preview.
import { api } from './api.js';
import { h, debounce } from './ui.js';
import { StripView, FramePlayer } from './strip.js';

/**
 * getRequest() -> {kind, params, seconds?, fps?} or null when there is nothing to preview.
 * Call .refresh() whenever the (unsaved) parameters change; calls are debounced by 400 ms.
 */
export function previewPanel({ n, getRequest, defaultSeconds = 20, defaultFps = 5, showControls = true, unavailableText = '', note = '' }) {
  const canvas = h('canvas', { role: 'img', 'aria-label': 'Preview of the effect on the strip' });
  const box = h('div', { class: 'strip-box' }, canvas);
  const msg = h('div', { class: 'help', 'aria-live': 'polite' });
  const err = h('div', { class: 'inline-error', role: 'alert' });
  const view = new StripView(canvas, { n, mode: 'dots' });
  const player = new FramePlayer(view);
  let playing = true, abort = null, seq = 0, destroyed = false;

  const playBtn = h('button', { type: 'button', class: 'small', onclick: () => { playing = !playing; sync(); } }, 'Pause');
  const secIn = h('input', { type: 'number', min: 1, max: 60, step: 1, value: defaultSeconds, 'aria-label': 'Preview length in seconds' });
  const fpsIn = h('input', { type: 'number', min: 1, max: 15, step: 1, value: defaultFps, 'aria-label': 'Preview frames per second' });
  function sync() { playBtn.textContent = playing ? 'Pause' : 'Play'; if (playing) player.play(); else player.pause(); }

  async function run() {
    if (destroyed) return;
    const req = getRequest();
    if (!req) { player.stop(); view.set(null); msg.textContent = unavailableText; err.textContent = ''; return; }
    if (abort) abort.abort();
    abort = new AbortController();
    const my = ++seq;
    msg.textContent = 'Rendering preview...';
    try {
      const r = await api.post('/preview', {
        kind: req.kind, params: req.params,
        seconds: req.seconds || Math.max(1, Math.min(60, Number(secIn.value) || defaultSeconds)),
        fps: req.fps || Math.max(1, Math.min(15, Number(fpsIn.value) || defaultFps)),
      }, { signal: abort.signal });
      if (my !== seq || destroyed) return;
      err.textContent = ''; msg.textContent = '';
      player.load(r.frames, r.fps); sync();
    } catch (e) {
      if (e.name === 'AbortError' || my !== seq) return;
      err.textContent = 'Preview: ' + e.message; msg.textContent = '';
    }
  }
  const refresh = debounce(run, 400);
  secIn.addEventListener('change', refresh); fpsIn.addEventListener('change', refresh);

  const controls = showControls ? h('div', { class: 'preview-controls' }, playBtn,
    h('label', null, 'Length', secIn, 's'), h('label', null, 'Speed', fpsIn, 'fps')) : null;
  const noteEl = note ? h('p', { class: 'preview-note', role: 'note' }, h('strong', null, 'Note: '), note) : null;
  const el = h('div', null, box, controls, noteEl, msg, err);
  return {
    el, refresh, runNow: run, player, view,
    setPlaying(v) { playing = v; sync(); },
    destroy() { destroyed = true; refresh.cancel(); if (abort) abort.abort(); player.stop(); view.destroy(); },
  };
}
