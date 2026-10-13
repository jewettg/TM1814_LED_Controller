// Canvas drawing for LED strips. Pixels are Uint8Array/Uint8ClampedArray of r,g,b triplets.

/** Decode a "rrggbbrrggbb..." frame string (as returned by /api/preview) into r,g,b bytes. */
export function decodeFrame(hex) {
  const n = hex.length / 6, out = new Uint8Array(n * 3);
  for (let i = 0; i < n * 3; i++) out[i] = parseInt(hex.substr(i * 2, 2), 16);
  return out;
}
export function hexToRgb(hex) {
  const v = parseInt(hex.slice(1), 16);
  return [(v >> 16) & 255, (v >> 8) & 255, v & 255];
}
export function rgbToHex(r, g, b) {
  return '#' + [r, g, b].map((x) => Math.max(0, Math.min(255, Math.round(x))).toString(16).padStart(2, '0')).join('');
}

/**
 * A strip view. mode 'dots' draws glowing LEDs folded into rows so they stay readable;
 * mode 'bars' draws one thin row of color (cheap; used for thumbnails).
 */
export class StripView {
  constructor(canvas, { n = 250, mode = 'dots', rows = 0, minPitch = 7, height = 34 } = {}) {
    this.canvas = canvas; this.n = n; this.mode = mode; this.fixedRows = rows; this.minPitch = minPitch; this.fixedHeight = height;
    this.ctx = canvas.getContext('2d');
    this.px = null;
    this.cssW = 0; this.cssH = 0;
    this.layout = null;
    this._ro = typeof ResizeObserver !== 'undefined' ? new ResizeObserver(() => this.resize()) : null;
    if (this._ro) this._ro.observe(canvas.parentElement || canvas);
    this.resize();
  }
  destroy() { if (this._ro) this._ro.disconnect(); }
  setCount(n) { this.n = n; this.resize(); }

  resize() {
    const parent = this.canvas.parentElement;
    let w = Math.floor((parent ? parent.clientWidth : this.canvas.clientWidth) || this.canvas.clientWidth || 300);
    if (parent && parent.classList.contains('strip-box')) w -= 16;
    if (parent && parent.classList.contains('studio-strip')) w -= 16;
    if (w < 40) w = Math.max(40, w);
    const dpr = window.devicePixelRatio || 1;
    let rows = 1, cols = this.n, pitch;
    if (this.mode === 'dots') {
      const maxCols = Math.max(10, Math.floor(w / this.minPitch));
      rows = this.fixedRows || Math.max(1, Math.ceil(this.n / maxCols));
      cols = Math.ceil(this.n / rows);
      pitch = w / cols;
      this.cssH = Math.ceil(rows * pitch);
    } else {
      pitch = w / this.n; this.cssH = this.fixedHeight;
    }
    this.cssW = w;
    this.layout = { rows, cols, pitch };
    this.canvas.width = Math.round(w * dpr); this.canvas.height = Math.round(this.cssH * dpr);
    this.canvas.style.width = w + 'px'; this.canvas.style.height = this.cssH + 'px';
    this.ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    this.draw();
  }

  /** Map a canvas-relative x,y (CSS px) to an LED index (clamped), snake-free: rows run left to right. */
  indexAt(x, y) {
    const { rows, cols, pitch } = this.layout;
    const c = Math.max(0, Math.min(cols - 1, Math.floor(x / pitch)));
    const r = Math.max(0, Math.min(rows - 1, Math.floor(y / pitch)));
    return Math.max(0, Math.min(this.n - 1, r * cols + c));
  }

  set(px) { this.px = px; this.draw(); }

  /** Optional overlay: highlight an inclusive LED range (selection). */
  setSelection(a, b) { this.sel = a === null || a === undefined ? null : [Math.min(a, b), Math.max(a, b)]; this.draw(); }

  draw() {
    const ctx = this.ctx, { rows, cols, pitch } = this.layout, px = this.px;
    ctx.clearRect(0, 0, this.cssW, this.cssH);
    if (this.mode === 'bars') {
      if (!px) { ctx.fillStyle = '#161b22'; ctx.fillRect(0, 0, this.cssW, this.cssH); return; }
      const w = this.cssW / this.n;
      for (let i = 0; i < this.n; i++) {
        ctx.fillStyle = 'rgb(' + px[i * 3] + ',' + px[i * 3 + 1] + ',' + px[i * 3 + 2] + ')';
        ctx.fillRect(Math.floor(i * w), 0, Math.ceil(w) + 0.5, this.cssH);
      }
      return;
    }
    const rad = pitch * 0.38;
    for (let i = 0; i < this.n; i++) {
      const r = Math.floor(i / cols), c = i % cols;
      const x = (c + 0.5) * pitch, y = (r + 0.5) * pitch;
      const R = px ? px[i * 3] : 0, G = px ? px[i * 3 + 1] : 0, B = px ? px[i * 3 + 2] : 0;
      const lum = Math.max(R, G, B);
      if (lum > 24) { // glow halo
        ctx.fillStyle = 'rgba(' + R + ',' + G + ',' + B + ',' + (0.12 + 0.18 * lum / 255).toFixed(2) + ')';
        ctx.beginPath(); ctx.arc(x, y, rad * 1.55, 0, 6.2832); ctx.fill();
      }
      ctx.fillStyle = lum > 4 ? 'rgb(' + R + ',' + G + ',' + B + ')' : '#1d242e';
      ctx.beginPath(); ctx.arc(x, y, rad, 0, 6.2832); ctx.fill();
    }
    if (this.sel) { // selection overlay: ring around selected LEDs
      ctx.strokeStyle = '#ffffff'; ctx.lineWidth = Math.max(1, pitch * 0.12);
      for (let i = this.sel[0]; i <= this.sel[1] && i < this.n; i++) {
        const r = Math.floor(i / cols), c = i % cols;
        ctx.beginPath(); ctx.arc((c + 0.5) * pitch, (r + 0.5) * pitch, rad * 1.25, 0, 6.2832); ctx.stroke();
      }
    }
    void rows;
  }
}

/** Plays a list of hex frames on a StripView at a given fps. */
export class FramePlayer {
  constructor(view, { onFrame = null } = {}) {
    this.view = view; this.frames = []; this.decoded = []; this.fps = 5; this.i = 0; this.timer = null; this.playing = false; this.onFrame = onFrame;
  }
  load(frames, fps) {
    this.frames = frames || []; this.decoded = new Array(this.frames.length); this.fps = fps || 5; this.i = 0;
    this.show(0);
  }
  show(i) {
    if (!this.frames.length) return;
    this.i = i % this.frames.length;
    if (!this.decoded[this.i]) this.decoded[this.i] = decodeFrame(this.frames[this.i]);
    this.view.set(this.decoded[this.i]);
    if (this.onFrame) this.onFrame(this.i);
  }
  play() {
    if (this.playing) return;
    this.playing = true;
    const tick = () => {
      if (!this.playing) return;
      if (!document.hidden) this.show(this.i + 1);
      this.timer = setTimeout(tick, 1000 / this.fps);
    };
    this.timer = setTimeout(tick, 1000 / this.fps);
  }
  pause() { this.playing = false; clearTimeout(this.timer); }
  stop() { this.pause(); }
}
