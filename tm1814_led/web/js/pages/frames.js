// Frame studio: editor for the "frames" (custom animation) effect kind.
// Data model (see tm1814_led/effects/frames.py):
//   {background, loop, frames:[{duration, transition, segments:[{start,end,color,color_end?}]}]}
// Each frame = background, then segments painted in order (later wins); color_end makes a linear gradient.
import { h, render, toast, debounce, nextId } from '../ui.js';
import { colorInput } from '../params.js';
import { previewPanel } from '../preview.js';
import { StripView, hexToRgb, rgbToHex } from '../strip.js';

const MAX_FRAMES = 500, MAX_SEGMENTS = 500, UNDO_LIMIT = 60;
const clone = (o) => JSON.parse(JSON.stringify(o));
const lerpC = (a, b, k) => { k = Math.max(0, Math.min(1, k)); return [0, 1, 2].map((i) => Math.trunc(a[i] + (b[i] - a[i]) * k)); }; // trunc matches the server's int()

/** Color of LED i inside a segment (matches the server's rendering). */
function colorAt(seg, i) {
  const c1 = hexToRgb(seg.color);
  if (!seg.color_end) return c1;
  const span = Math.max(1, seg.end - seg.start);
  return lerpC(c1, hexToRgb(seg.color_end), (i - seg.start) / span);
}
/** Render one frame to r,g,b bytes (background first, then segments in order). */
export function renderFrame(frame, bg, n) {
  const px = new Uint8ClampedArray(n * 3), b = hexToRgb(bg);
  for (let i = 0; i < n; i++) { px[i * 3] = b[0]; px[i * 3 + 1] = b[1]; px[i * 3 + 2] = b[2]; }
  for (const seg of frame.segments) {
    const a = Math.max(0, seg.start), e = Math.min(seg.end, n - 1);
    const c1 = hexToRgb(seg.color), c2 = seg.color_end ? hexToRgb(seg.color_end) : null, span = Math.max(1, seg.end - seg.start);
    for (let i = a; i <= e; i++) {
      const c = c2 ? lerpC(c1, c2, (i - seg.start) / span) : c1;
      px[i * 3] = c[0]; px[i * 3 + 1] = c[1]; px[i * 3 + 2] = c[2];
    }
  }
  return px;
}
/** Part of seg that covers [s,e] (inclusive), with gradient end colors recomputed. */
function piece(seg, s, e) {
  const out = { start: s, end: e, color: rgbToHex(...colorAt(seg, s)) };
  if (seg.color_end && e > s) out.color_end = rgbToHex(...colorAt(seg, e));
  return out;
}
/** Remove LEDs a..b from every segment (trimming/splitting as needed). */
function cutRange(segs, a, b) {
  const out = [];
  for (const s of segs) {
    if (s.end < a || s.start > b) { out.push(s); continue; }
    if (s.start < a) out.push(piece(s, s.start, a - 1));
    if (s.end > b) out.push(piece(s, b + 1, s.end));
  }
  return out;
}
function rotateSegs(segs, k, n) {
  const out = [];
  for (const seg0 of segs) {
    if (seg0.start > n - 1) continue;
    const seg = { ...seg0, end: Math.min(seg0.end, n - 1) };
    const L = seg.end - seg.start + 1;
    const s = (((seg.start + k) % n) + n) % n;
    const add = (from, to, off) => { const p = piece(seg, seg.start + from, seg.start + to); p.start = off; p.end = off + (to - from); out.push(p); };
    if (s + L - 1 <= n - 1) add(0, L - 1, s);
    else { const first = n - s; add(0, first - 1, s); add(first, L - 1, 0); }
  }
  return out;
}
function mirrorSegs(segs, n) {
  return segs.filter((s) => s.start <= n - 1).map((s) => {
    const e = Math.min(s.end, n - 1);
    const o = { start: n - 1 - e, end: n - 1 - s.start, color: s.color_end || s.color };
    if (s.color_end) o.color_end = s.color;
    return o;
  });
}
const hsvHex = (h2, s, v) => {
  const f = (k) => { const x = (k + h2 * 6) % 6; return v - v * s * Math.max(0, Math.min(x, 4 - x, 1)); };
  return rgbToHex(f(5) * 255, f(3) * 255, f(1) * 255);
};

function drawThumb(canvas, px, n) {
  if (canvas.width !== n) { canvas.width = n; canvas.height = 1; }
  const ctx = canvas.getContext('2d'), img = ctx.createImageData(n, 1);
  for (let i = 0; i < n; i++) { img.data[i * 4] = px[i * 3]; img.data[i * 4 + 1] = px[i * 3 + 1]; img.data[i * 4 + 2] = px[i * 3 + 2]; img.data[i * 4 + 3] = 255; }
  ctx.putImageData(img, 0, 0);
}

export function frameStudio({ data, ledCount, onChange, playOnLights, getName }) {
  const n = ledCount;
  let d = clone(data);
  let cur = 0, sel = null; // sel = [a, b]
  const undo = [], redo = [];
  let lastKey = null, lastTime = 0;
  let colorA = '#ff0000', colorB = '#0000ff';
  const cards = [];
  const isText = (t) => t && (t.tagName === 'INPUT' && !['checkbox', 'button', 'range', 'color'].includes(t.type) || t.tagName === 'TEXTAREA' || t.tagName === 'SELECT');

  const frame = () => d.frames[cur];
  const snap = () => JSON.stringify({ d, cur });
  function commit(fn, key, { lists = true } = {}) {
    const now = Date.now();
    if (!(key && key === lastKey && now - lastTime < 800)) { undo.push(snap()); if (undo.length > UNDO_LIMIT) undo.shift(); }
    lastKey = key || null; lastTime = now; redo.length = 0;
    fn(); changed({ lists });
  }
  function restore(s) { const o = JSON.parse(s); d = o.d; cur = Math.min(o.cur, d.frames.length - 1); lastKey = null; syncControls(); changed({ lists: true }); }
  function doUndo() { if (!undo.length) return; redo.push(snap()); restore(undo.pop()); }
  function doRedo() { if (!redo.length) return; undo.push(snap()); restore(redo.pop()); }

  // ------------------------------------------------------------ main strip
  const canvas = h('canvas', { role: 'img', 'aria-label': 'Current frame. Click and drag to select LEDs.', tabindex: 0 });
  const stripWrap = h('div', { class: 'studio-strip' }, canvas);
  const view = new StripView(canvas, { n, mode: 'dots', minPitch: 15 });
  let dragging = false, anchor = 0;
  function idxFromEvent(e) { const r = canvas.getBoundingClientRect(); return view.indexAt(e.clientX - r.left, e.clientY - r.top); }
  canvas.addEventListener('pointerdown', (e) => {
    dragging = true; canvas.setPointerCapture(e.pointerId); anchor = idxFromEvent(e); setSel(anchor, anchor); e.preventDefault();
  });
  canvas.addEventListener('pointermove', (e) => { if (dragging) setSel(anchor, idxFromEvent(e)); });
  const endDrag = () => { dragging = false; };
  canvas.addEventListener('pointerup', endDrag); canvas.addEventListener('pointercancel', endDrag);

  const selA = h('input', { type: 'number', min: 0, max: n - 1, value: 0, 'aria-label': 'Selection start LED' });
  const selB = h('input', { type: 'number', min: 0, max: n - 1, value: 0, 'aria-label': 'Selection end LED' });
  const selInfo = h('span', { class: 'muted small' });
  function setSel(a, b) {
    if (a === null) sel = null;
    else { a = Math.max(0, Math.min(n - 1, Math.round(a))); b = Math.max(0, Math.min(n - 1, Math.round(b))); sel = [Math.min(a, b), Math.max(a, b)]; selA.value = sel[0]; selB.value = sel[1]; }
    view.setSelection(sel ? sel[0] : null, sel ? sel[1] : null);
    selInfo.textContent = sel ? (sel[1] - sel[0] + 1) + ' LEDs selected (' + sel[0] + ' to ' + sel[1] + ')' : 'Nothing selected. Click and drag on the strip, or type numbers.';
    highlightSegs();
  }
  const onSelInput = () => { const a = Number(selA.value), b = Number(selB.value); if (!isNaN(a) && !isNaN(b)) setSel(a, b); };
  selA.addEventListener('input', onSelInput); selB.addEventListener('input', onSelInput);

  // ------------------------------------------------------------ tools
  const needSel = () => { if (!sel) { toast('Select some LEDs first: drag across the strip above.', 'error'); return false; } return true; };
  const addSeg = (seg) => { if (frame().segments.length >= MAX_SEGMENTS) { toast('Too many segments in this frame.', 'error'); return; } frame().segments.push(seg); };
  function fill() {
    if (!needSel()) return;
    commit(() => { frame().segments = cutRange(frame().segments, sel[0], sel[1]); addSeg({ start: sel[0], end: sel[1], color: colorA }); });
  }
  function gradient() {
    if (!needSel()) return;
    commit(() => { frame().segments = cutRange(frame().segments, sel[0], sel[1]); addSeg(sel[0] === sel[1] ? { start: sel[0], end: sel[1], color: colorA } : { start: sel[0], end: sel[1], color: colorA, color_end: colorB }); });
  }
  function clearSel() { if (!needSel()) return; commit(() => { frame().segments = cutRange(frame().segments, sel[0], sel[1]); }); }
  const shiftBy = h('input', { type: 'number', min: 1, max: n - 1, value: 1, 'aria-label': 'Shift amount in LEDs' });
  function shift(dir) {
    const k = Math.max(1, Math.min(n - 1, Math.round(Number(shiftBy.value) || 1))) * dir;
    commit(() => { frame().segments = rotateSegs(frame().segments, k, n); });
  }
  function mirror() { commit(() => { frame().segments = mirrorSegs(frame().segments, n); }); }
  const blockIn = h('input', { type: 'number', min: 1, max: 125, value: 10, 'aria-label': 'Random block size in LEDs' });
  function randomize() {
    const size = Math.max(1, Math.round(Number(blockIn.value) || 10));
    const a = sel ? sel[0] : 0, b = sel ? sel[1] : n - 1;
    commit(() => {
      frame().segments = cutRange(frame().segments, a, b);
      for (let s = a; s <= b; s += size) addSeg({ start: s, end: Math.min(b, s + size - 1), color: hsvHex(Math.random(), 0.6 + Math.random() * 0.4, 0.7 + Math.random() * 0.3) });
    });
  }
  const A = colorInput(colorA, (v) => { colorA = v; }, 'Color A'), B = colorInput(colorB, (v) => { colorB = v; }, 'Color B');
  const palette = ['#ff0000', '#ff5a00', '#ffd27a', '#00ff00', '#00b8d9', '#0000ff', '#8c00ff', '#ffffff', '#000000'].map((c) =>
    h('button', { type: 'button', class: 'icon small', title: c, 'aria-label': 'Set color A to ' + c, style: { background: c, borderColor: 'var(--border-strong)' }, onclick: () => { colorA = c; A.set(c); } }));

  const toolsCard = h('section', { class: 'card stack', 'aria-label': 'Tools' },
    h('h2', null, 'Paint'),
    h('div', { class: 'row' }, h('div', null, h('span', { class: 'field-label' }, 'Color A'), A), h('div', null, h('span', { class: 'field-label' }, 'Color B (gradient end)'), B)),
    h('div', { class: 'row tight' }, palette),
    h('div', { class: 'toolbtns' },
      h('button', { type: 'button', class: 'primary', onclick: fill, title: 'Shortcut: F' }, 'Fill selection'),
      h('button', { type: 'button', onclick: gradient, title: 'Shortcut: G' }, 'Gradient A to B'),
      h('button', { type: 'button', onclick: clearSel, title: 'Shortcut: Delete' }, 'Clear selection')),
    h('p', { class: 'help' }, 'Fill, Gradient and Clear act on the selected LEDs. Clear turns them back to the background color.'),
    h('h3', null, 'Whole frame'),
    h('div', { class: 'toolbtns' },
      h('button', { type: 'button', onclick: () => shift(-1) }, '◀ Shift left'),
      h('label', { class: 'inline-field' }, h('span', { class: 'small' }, 'by'), shiftBy, h('span', { class: 'small' }, 'LEDs')),
      h('button', { type: 'button', onclick: () => shift(1) }, 'Shift right ▶'),
      h('button', { type: 'button', onclick: mirror }, 'Mirror')),
    h('div', { class: 'toolbtns' },
      h('button', { type: 'button', onclick: randomize }, 'Randomize colors'),
      h('label', { class: 'inline-field' }, h('span', { class: 'small' }, 'blocks of'), blockIn, h('span', { class: 'small' }, 'LEDs')),
    ),
    h('p', { class: 'help' }, 'Shift wraps around the ends. Randomize paints random colors over the selection, or the whole strip if nothing is selected.'));

  // ------------------------------------------------------------ segments panel
  const segList = h('div', { class: 'seg-list' });
  const segRows = [];
  function highlightSegs() {
    segRows.forEach((r, i) => { const s = frame().segments[i]; r.classList.toggle('selected', !!(sel && s && s.start === sel[0] && s.end === sel[1])); });
  }
  function drawSegs() {
    segRows.length = 0;
    const segs = frame().segments;
    if (!segs.length) { render(segList, h('div', { class: 'muted small', style: { padding: '12px' } }, 'This frame only shows the background color. Select LEDs and press Fill to add a segment.')); return; }
    render(segList, segs.map((s, i) => {
      const num = (key, lo) => {
        const inp = h('input', { type: 'number', min: 0, max: n - 1, value: s[key], 'aria-label': (key === 'start' ? 'Start' : 'End') + ' of segment ' + (i + 1) });
        inp.addEventListener('input', () => { const v = Number(inp.value); if (inp.value === '' || isNaN(v)) return; commit(() => { s[key] = Math.max(0, Math.min(n - 1, Math.round(v))); if (s.start > s.end) { const t = s.start; s.start = s.end; s.end = t; } }, 'seg' + i + key, { lists: false }); });
        return inp;
      };
      const c1 = h('input', { type: 'color', value: s.color, 'aria-label': 'Color of segment ' + (i + 1) });
      c1.addEventListener('input', () => commit(() => { s.color = c1.value; }, 'seg' + i + 'c', { lists: false }));
      const grad = h('input', { type: 'checkbox', checked: !!s.color_end, 'aria-label': 'Gradient for segment ' + (i + 1), title: 'Gradient' });
      const c2 = h('input', { type: 'color', value: s.color_end || s.color, 'aria-label': 'Gradient end color of segment ' + (i + 1), hidden: !s.color_end });
      grad.addEventListener('change', () => commit(() => { if (grad.checked) s.color_end = c2.value; else delete s.color_end; c2.hidden = !grad.checked; }, null, { lists: false }));
      c2.addEventListener('input', () => commit(() => { s.color_end = c2.value; }, 'seg' + i + 'e', { lists: false }));
      const mv = (delta) => commit(() => { const t = segs[i]; segs[i] = segs[i + delta]; segs[i + delta] = t; });
      const row = h('div', { class: 'seg' },
        h('div', { class: 'line' }, h('span', { class: 'badge gray' }, String(i + 1)), num('start'), h('span', null, 'to'), num('end'),
          c1, h('label', { class: 'small', title: 'Make this segment a gradient' }, grad, ' gradient'), c2),
        h('div', { class: 'line' },
          h('button', { type: 'button', class: 'small', onclick: () => setSel(s.start, s.end) }, 'Select'),
          h('button', { type: 'button', class: 'small icon', 'aria-label': 'Move segment ' + (i + 1) + ' earlier in paint order', title: 'Earlier (painted first)', disabled: i === 0, onclick: () => mv(-1) }, '\u2191'),
          h('button', { type: 'button', class: 'small icon', 'aria-label': 'Move segment ' + (i + 1) + ' later in paint order', title: 'Later (painted on top)', disabled: i === segs.length - 1, onclick: () => mv(1) }, '\u2193'),
          h('button', { type: 'button', class: 'small', onclick: () => commit(() => { if (segs.length < MAX_SEGMENTS) segs.splice(i + 1, 0, clone(s)); }) }, 'Duplicate'),
          h('button', { type: 'button', class: 'small danger', 'aria-label': 'Delete segment ' + (i + 1), onclick: () => commit(() => { segs.splice(i, 1); }) }, 'Delete')));
      segRows.push(row);
      return row;
    }));
    highlightSegs();
  }
  const segCard = h('section', { class: 'card stack', 'aria-label': 'Segments' },
    h('h2', null, 'Segments in this frame'),
    h('p', { class: 'help' }, 'A segment is a run of LEDs with one color (or a gradient). Lower rows are painted on top of higher ones.'), segList);

  // ------------------------------------------------------------ timeline
  const timeline = h('div', { class: 'timeline', role: 'list', 'aria-label': 'Frames' });
  const totalInfo = h('span', { class: 'muted small' });
  function updateTotal() {
    const total = d.frames.reduce((a, f) => a + f.duration, 0);
    totalInfo.textContent = d.frames.length + ' frame' + (d.frames.length === 1 ? '' : 's') + ', ' + total.toFixed(2).replace(/\.?0+$/, '') + ' s per pass';
  }
  function drawTimeline() {
    cards.length = 0;
    render(timeline, d.frames.map((f, i) => {
      const cv = h('canvas', { class: 'thumb', 'aria-hidden': 'true', style: { imageRendering: 'pixelated' } });
      drawThumb(cv, renderFrame(f, d.background, n), n);
      const dur = h('input', { type: 'number', min: 0.05, max: 3600, step: 0.05, value: f.duration, 'aria-label': 'Duration of frame ' + (i + 1) + ' in seconds' });
      dur.addEventListener('input', () => { const v = Number(dur.value); if (!(v >= 0.05)) return; commit(() => { f.duration = v; }, 'dur' + i, { lists: false }); updateTotal(); });
      const tr = h('select', { 'aria-label': 'Transition after frame ' + (i + 1) }, h('option', { value: 'cut', selected: f.transition === 'cut' }, 'Cut'), h('option', { value: 'fade', selected: f.transition === 'fade' }, 'Fade'));
      tr.addEventListener('change', () => commit(() => { f.transition = tr.value; }));
      const mvF = (delta) => commit(() => { const t = d.frames[i]; d.frames[i] = d.frames[i + delta]; d.frames[i + delta] = t; cur = i + delta; });
      const card = h('div', { class: 'fr' + (i === cur ? ' current' : ''), role: 'listitem', 'aria-label': 'Frame ' + (i + 1) },
        h('div', { class: 'no' }, h('span', null, 'Frame ' + (i + 1)), h('span', { class: 'muted' }, f.segments.length + ' seg')),
        cv,
        h('div', { class: 'ctl' }, dur, h('span', null, 's'), tr),
        h('div', { class: 'ctl' },
          h('button', { type: 'button', 'aria-label': 'Move frame ' + (i + 1) + ' left', disabled: i === 0, onclick: (e) => { e.stopPropagation(); mvF(-1); } }, '←'),
          h('button', { type: 'button', 'aria-label': 'Move frame ' + (i + 1) + ' right', disabled: i === d.frames.length - 1, onclick: (e) => { e.stopPropagation(); mvF(1); } }, '→'),
          h('button', { type: 'button', 'aria-label': 'Duplicate frame ' + (i + 1), title: 'Duplicate', onclick: (e) => { e.stopPropagation(); dupFrame(i); } }, 'Dup'),
          h('button', { type: 'button', class: 'danger', 'aria-label': 'Delete frame ' + (i + 1), disabled: d.frames.length <= 1, onclick: (e) => { e.stopPropagation(); delFrame(i); } }, 'Del')));
      card.addEventListener('click', () => { if (cur !== i) { cur = i; cards.forEach((c, j) => c.classList.toggle('current', j === i)); changed({ lists: false, timeline: false }); drawSegs(); } });
      cards.push(card);
      return card;
    }));
    updateTotal();
  }
  function addFrame() {
    if (d.frames.length >= MAX_FRAMES) { toast('Frame limit reached.', 'error'); return; }
    commit(() => { d.frames.splice(cur + 1, 0, { duration: frame().duration, transition: 'cut', segments: [] }); cur += 1; });
  }
  function dupFrame(i = cur) {
    if (d.frames.length >= MAX_FRAMES) { toast('Frame limit reached.', 'error'); return; }
    commit(() => { d.frames.splice(i + 1, 0, clone(d.frames[i])); cur = i + 1; });
  }
  function delFrame(i = cur) {
    if (d.frames.length <= 1) { toast('An animation needs at least one frame.', 'error'); return; }
    commit(() => { d.frames.splice(i, 1); cur = Math.min(cur, d.frames.length - 1); });
  }
  function copyPrev() {
    if (cur === 0) { toast('This is the first frame; there is no previous frame.', 'error'); return; }
    commit(() => { frame().segments = clone(d.frames[cur - 1].segments); });
  }
  const timelineCard = h('section', { class: 'card stack', 'aria-label': 'Frames' },
    h('div', { class: 'row' }, h('h2', { style: { margin: 0 } }, 'Frames'), totalInfo, h('span', { class: 'spacer' }),
      h('button', { type: 'button', class: 'primary', onclick: addFrame, title: 'Shortcut: N' }, '+ Add frame'),
      h('button', { type: 'button', onclick: () => dupFrame(), title: 'Shortcut: Ctrl+D' }, 'Duplicate frame'),
      h('button', { type: 'button', onclick: copyPrev }, 'Copy previous frame here'),
      h('button', { type: 'button', class: 'danger', onclick: () => delFrame() }, 'Delete frame')),
    timeline,
    h('p', { class: 'help' }, 'Each frame shows for its duration. Fade blends it into the next frame during that time; Cut switches instantly. Click a frame to edit it.'));

  // ------------------------------------------------------------ top toolbar
  const undoBtn = h('button', { type: 'button', onclick: doUndo, title: 'Undo (Ctrl+Z)' }, 'Undo');
  const redoBtn = h('button', { type: 'button', onclick: doRedo, title: 'Redo (Ctrl+Y)' }, 'Redo');
  const loopSel = h('select', { id: nextId('loop') }, [['loop', 'Loop (repeat forever)'], ['pingpong', 'Ping-pong (forward, then back)'], ['once', 'Once (hold the last frame)']].map(([v, l]) => h('option', { value: v }, l)));
  loopSel.addEventListener('change', () => commit(() => { d.loop = loopSel.value; }));
  const bgCtl = colorInput(d.background, (v) => commit(() => { d.background = v; }, 'bg'), 'Background color');
  function syncControls() { loopSel.value = d.loop; bgCtl.set(d.background); }
  syncControls();
  const helpPanel = h('div', { class: 'pop-panel', hidden: true, role: 'dialog', 'aria-label': 'Keyboard shortcuts' },
    h('strong', null, 'Keyboard shortcuts'),
    h('table', null, [['Ctrl/Cmd + Z', 'Undo'], ['Ctrl/Cmd + Y', 'Redo'], ['[ and ]', 'Previous / next frame'], ['N', 'Add frame'], ['Ctrl/Cmd + D', 'Duplicate frame'],
      ['Ctrl/Cmd + A', 'Select all LEDs'], ['F', 'Fill selection'], ['G', 'Gradient'], ['Delete', 'Clear selection'], ['Space', 'Play / pause preview'], ['Esc', 'Deselect']]
      .map(([k, v]) => h('tr', null, h('td', null, h('kbd', null, k)), h('td', null, v)))),
    h('p', { class: 'help' }, 'Shortcuts work when you are not typing in a box.'));
  const helpBtn = h('button', { type: 'button', 'aria-expanded': 'false', onclick: () => { helpPanel.hidden = !helpPanel.hidden; helpBtn.setAttribute('aria-expanded', String(!helpPanel.hidden)); } }, 'Shortcuts');
  const frameLabel = h('strong');
  const topCard = h('section', { class: 'card', 'aria-label': 'Animation settings' },
    h('div', { class: 'row' }, undoBtn, redoBtn, h('span', { class: 'spacer' }), frameLabel, h('span', { class: 'spacer' }),
      h('label', { class: 'inline-field' }, h('span', { class: 'small' }, 'Loop'), loopSel),
      h('div', { class: 'inline-field' }, h('span', { class: 'small' }, 'Background'), bgCtl),
      h('div', { class: 'pop' }, helpBtn, helpPanel)));

  const stripCard = h('section', { class: 'card stack', 'aria-label': 'Strip editor' },
    h('div', { class: 'row' }, h('h2', { style: { margin: 0 } }, 'Current frame'),
      h('span', { class: 'spacer' }),
      h('label', { class: 'inline-field' }, h('span', { class: 'small' }, 'LED'), selA), h('label', { class: 'inline-field' }, h('span', { class: 'small' }, 'to'), selB),
      h('button', { type: 'button', onclick: () => setSel(0, n - 1), class: 'small' }, 'Select all'),
      h('button', { type: 'button', onclick: () => setSel(null), class: 'small' }, 'Deselect')),
    stripWrap, selInfo,
    h('p', { class: 'help' }, 'Your strip has ' + n + ' LEDs, shown folded into rows from left to right. LED 0 is the top left.'));

  // ------------------------------------------------------------ playback (server-rendered preview)
  const stale = h('span', { class: 'badge warn', hidden: true }, 'Out of date');
  const pv = previewPanel({
    n, showControls: false,
    getRequest: () => {
      const total = d.frames.reduce((a, f) => a + f.duration, 0) * (d.loop === 'pingpong' ? 2 : 1);
      const small = d.frames.some((f) => f.duration < 0.2);
      const fps = small ? 15 : 10;
      return { kind: 'frames', params: { data: d }, seconds: Math.max(1, Math.min(60, Math.ceil(total))), fps };
    },
  });
  const playAnim = h('button', { type: 'button', class: 'primary', onclick: async () => { pv.setPlaying(true); stale.hidden = true; await pv.runNow(); } }, 'Play animation');
  const pauseAnim = h('button', { type: 'button', onclick: () => { playing = !playing; pv.setPlaying(playing); pauseAnim.textContent = playing ? 'Pause' : 'Resume'; } }, 'Pause');
  let playing = true;
  const lightsBtn = h('button', { type: 'button', onclick: async (e) => { e.currentTarget.disabled = true; try { await playOnLights(); } finally { e.currentTarget.disabled = false; } } }, 'Play on lights');
  const playCard = h('section', { class: 'card stack', 'aria-label': 'Animation playback' },
    h('div', { class: 'row' }, h('h2', { style: { margin: 0 } }, 'Play the animation'), stale, h('span', { class: 'spacer' }), playAnim, pauseAnim, lightsBtn),
    pv.el,
    h('p', { class: 'help' }, 'Play animation asks the program to render the whole animation (about 10 frames per second, up to one minute) and plays it here. On the lights it runs as a streamed effect.'));

  // ------------------------------------------------------------ refresh logic
  function drawStrip() { view.set(renderFrame(frame(), d.background, n)); }
  function changed({ lists = true, timeline = true } = {}) {
    drawStrip();
    frameLabel.textContent = 'Frame ' + (cur + 1) + ' of ' + d.frames.length;
    undoBtn.disabled = !undo.length; redoBtn.disabled = !redo.length;
    if (lists) { drawSegs(); }
    if (timeline) { if (lists) drawTimeline(); else if (cards[cur]) { drawThumb(cards[cur].querySelector('canvas'), renderFrame(frame(), d.background, n), n); } }
    // background change affects every thumbnail; cheap enough to redraw all
    if (!lists && timeline) { d.frames.forEach((f, i) => { if (i !== cur && cards[i]) drawThumb(cards[i].querySelector('canvas'), renderFrame(f, d.background, n), n); }); }
    if (cards[cur]) { const seg = cards[cur].querySelector('.no .muted'); if (seg) seg.textContent = frame().segments.length + ' seg'; }
    stale.hidden = false;
    notify();
  }
  let ready = false;
  const notify = debounce(() => { if (ready) onChange(clone(d)); }, 150);

  // ------------------------------------------------------------ keyboard shortcuts
  function onKey(e) {
    if (!el.isConnected) return;
    const mod = e.ctrlKey || e.metaKey;
    if (e.target && isText(e.target)) return;
    if (document.querySelector('dialog[open]')) return;
    const k = e.key.toLowerCase();
    if (mod && k === 'z' && !e.shiftKey) { e.preventDefault(); doUndo(); }
    else if (mod && (k === 'y' || (k === 'z' && e.shiftKey))) { e.preventDefault(); doRedo(); }
    else if (mod && k === 'd') { e.preventDefault(); dupFrame(); }
    else if (mod && k === 'a') { e.preventDefault(); setSel(0, n - 1); }
    else if (mod || e.altKey) return;
    else if (k === '[') { if (cur > 0) { cur--; changed({ lists: true }); } }
    else if (k === ']') { if (cur < d.frames.length - 1) { cur++; changed({ lists: true }); } }
    else if (k === 'n') addFrame();
    else if (k === 'f') fill();
    else if (k === 'g') gradient();
    else if (k === 'delete' || k === 'backspace') { e.preventDefault(); clearSel(); }
    else if (k === 'escape') setSel(null);
    else if (k === ' ' && e.target.tagName !== 'BUTTON') { e.preventDefault(); playing = !playing; pv.setPlaying(playing); pauseAnim.textContent = playing ? 'Pause' : 'Resume'; }
  }
  document.addEventListener('keydown', onKey);

  const el = h('div', { class: 'stack' }, topCard, stripCard, h('div', { class: 'studio-grid' }, toolsCard, segCard), timelineCard, playCard);
  setSel(null);
  changed({ lists: true });
  stale.hidden = true; ready = true;
  return {
    el,
    destroy() { document.removeEventListener('keydown', onKey); pv.destroy(); view.destroy(); notify.cancel(); },
  };
}
