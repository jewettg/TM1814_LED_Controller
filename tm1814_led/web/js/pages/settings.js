import { api, getMeta, getSettings, invalidate } from '../api.js';
import { h, render, act, toast, pageHead, loading, nextId, switchEl, setDisplayTz } from '../ui.js';
import { colorInput } from '../params.js';
import { refresh } from '../status.js';

export async function settings(ctx) {
  const app = ctx.app; render(app, loading());
  const [cfg, meta] = await Promise.all([getSettings(true), getMeta()]);
  const st = { ...cfg };
  const err = h('div', { class: 'banner error', hidden: true, role: 'alert' });
  const showErr = (e) => { err.hidden = !e; err.textContent = e ? e.message : ''; };

  const F = (label, input, help, id) => h('div', null, h('label', { class: 'field-label', for: id }, label), input, help ? h('div', { class: 'help' }, help) : null);
  const num = (key, label, help, { min, max, step = 1, float = false } = {}) => {
    const id = nextId('s');
    const i = h('input', { type: 'number', id, value: st[key] === null ? '' : st[key], min, max, step });
    i.addEventListener('input', () => { st[key] = i.value === '' ? (float ? null : 0) : Number(i.value); });
    return F(label, i, help, id);
  };

  // ---- controller
  const hostId = nextId('host');
  const host = h('input', { type: 'text', id: hostId, value: st.controller_host, placeholder: 'For example: 192.168.1.50', autocomplete: 'off', spellcheck: 'false' });
  host.addEventListener('input', () => { st.controller_host = host.value.trim(); });
  const found = h('div');
  const testOut = h('div', { 'aria-live': 'polite' });
  const findBtn = h('button', { type: 'button', onclick: act(async () => {
    render(found, h('span', { class: 'muted' }, 'Searching the network (about 2 seconds)...'));
    const list = await api.post('/controller/discover');
    render(found, list.length ? h('div', { class: 'stack' }, h('div', { class: 'small muted' }, 'Found ' + list.length + ' controller' + (list.length === 1 ? '' : 's') + '. Click one to use its address.'),
      list.map((c) => h('button', { type: 'button', style: { width: '100%', justifyContent: 'flex-start' }, onclick: () => { host.value = c.ip; st.controller_host = c.ip; toast('Address filled in. Remember to Save.', 'info', 3000); } },
        c.ip + '  •  ' + c.model + '  •  ' + c.mac)))
      : h('span', { class: 'muted' }, 'No controllers answered. Make sure it is powered on and on the same network, or type the address by hand.'));
  }) }, 'Find controllers');
  const testBtn = h('button', { type: 'button', onclick: act(async () => {
    render(testOut, h('span', { class: 'muted' }, 'Testing...'));
    if (host.value.trim() !== cfg.controller_host) { await save(true); }
    const r = await api.get('/controller/state');
    render(testOut, r.reachable
      ? h('div', { class: 'banner', style: { background: 'var(--success-soft)' } }, 'Connected. ', r.state ? 'Power ' + (r.state.power ? 'on' : 'off') + ', firmware v' + r.state.version + ', ' + r.state.led_count + ' LEDs reported.' : '')
      : h('div', { class: 'banner error' }, 'Could not connect: ' + (r.error || 'no answer')));
  }) }, 'Test connection');

  // ---- idle
  const idleSel = h('select', { id: nextId('idle') }, [['off', 'Turn the lights off'], ['leave', 'Leave them as they are'], ['solid', 'Show a solid color']].map(([v, l]) => h('option', { value: v, selected: st.idle_action === v }, l)));
  const idleColorWrap = h('div', { hidden: st.idle_action !== 'solid' }, h('span', { class: 'field-label' }, 'Idle color'), colorInput(st.idle_color, (v) => { st.idle_color = v; }, 'Idle color'));
  idleSel.addEventListener('change', () => { st.idle_action = idleSel.value; idleColorWrap.hidden = idleSel.value !== 'solid'; });

  // ---- time zone and location
  const tzId = nextId('tz');
  const tz = h('input', { type: 'text', id: tzId, value: st.timezone, list: tzId + '-list', placeholder: 'Blank = this computer\'s time zone', autocomplete: 'off', spellcheck: 'false' });
  tz.addEventListener('input', () => { st.timezone = tz.value.trim(); });
  const tzList = h('datalist', { id: tzId + '-list' }, meta.timezones.map((z) => h('option', { value: z })));
  const latIn = h('input', { type: 'number', id: nextId('lat'), step: 'any', min: -90, max: 90, value: st.latitude === null ? '' : st.latitude });
  const lonIn = h('input', { type: 'number', id: nextId('lon'), step: 'any', min: -180, max: 180, value: st.longitude === null ? '' : st.longitude });
  latIn.addEventListener('input', () => { st.latitude = latIn.value === '' ? null : Number(latIn.value); });
  lonIn.addEventListener('input', () => { st.longitude = lonIn.value === '' ? null : Number(lonIn.value); });
  const geoBtn = h('button', { type: 'button', onclick: (e) => {
    if (!navigator.geolocation) { toast('This browser cannot share its location. Type the numbers instead.', 'error'); return; }
    e.currentTarget.disabled = true; const b = e.currentTarget;
    navigator.geolocation.getCurrentPosition((p) => {
      b.disabled = false; st.latitude = Math.round(p.coords.latitude * 10000) / 10000; st.longitude = Math.round(p.coords.longitude * 10000) / 10000;
      latIn.value = st.latitude; lonIn.value = st.longitude; toast('Location filled in. Remember to Save.', 'info', 3000);
    }, (er) => { b.disabled = false; toast('Could not get your location: ' + er.message + '. Type the numbers instead.', 'error'); }, { timeout: 10000 });
  } }, 'Use my location');

  async function save(quiet) {
    showErr(null);
    const body = {
      controller_host: st.controller_host, controller_port: st.controller_port, led_count: st.led_count, fps: st.fps, min_gap_ms: st.min_gap_ms,
      max_groups: st.max_groups, brightness: st.brightness, idle_action: st.idle_action, idle_color: st.idle_color,
      timezone: st.timezone, latitude: st.latitude, longitude: st.longitude,
    };
    try {
      const r = await api.put('/settings', body);
      Object.assign(cfg, r); invalidate('settings'); setDisplayTz(r.timezone); await refresh();
      if (!quiet) toast('Settings saved', 'success');
    } catch (e) { showErr(e); throw e; }
  }

  render(app, pageHead('Settings'), err,
    h('div', { class: 'stack' },
      h('section', { class: 'card stack' }, h('h2', null, 'Controller'),
        h('div', { class: 'form-grid' },
          F('Controller address', host, 'The IP address or host name of your Wi-Fi LED controller.', hostId),
          num('controller_port', 'Port', 'Almost always 5577.', { min: 1, max: 65535 })),
        h('div', { class: 'row' }, findBtn, testBtn), found, testOut,
        meta.simulated ? h('div', { class: 'banner warn' }, 'This program is running with a simulated controller, so nothing is sent to real lights.') : null),
      h('section', { class: 'card stack' }, h('h2', null, 'Lights'),
        h('div', { class: 'form-grid' },
          num('led_count', 'Number of LEDs', '0 means ask the controller (usually 250 per output).', { min: 0, max: 255 }),
          num('brightness', 'Master brightness (%)', 'Scales everything the lights show.', { min: 0, max: 100 }),
          num('fps', 'Frames per second', 'For animated effects. Higher is smoother but sends more commands (1 to 15).', { min: 1, max: 15 }),
          num('min_gap_ms', 'Pause between commands (ms)', 'Raise this if the controller drops commands.', { min: 5, max: 500 }),
          num('max_groups', 'Most colors per frame', 'Each distinct color in a frame costs one command (2 to 30).', { min: 2, max: 30 })),
        h('div', { class: 'form-grid' },
          F('When nothing is scheduled', idleSel, 'What the lights do after a run list ends or you press Stop.', idleSel.id), idleColorWrap)),
      h('section', { class: 'card stack' }, h('h2', null, 'Time and place'),
        h('div', { class: 'form-grid' }, F('Time zone', tz, 'Start typing to search, for example America/Chicago. Leave blank to use the computer\'s own zone.', tzId), tzList),
        h('div', { class: 'form-grid' }, F('Latitude', latIn, null, latIn.id), F('Longitude', lonIn, null, lonIn.id)),
        h('div', { class: 'row' }, geoBtn),
        h('p', { class: 'help' }, 'Latitude and longitude are needed to work out sunrise and sunset for schedules. Without them, rough default times are used.')),
      h('div', { class: 'row' }, h('button', { class: 'primary', type: 'button', onclick: act(async () => { try { await save(false); } catch (e) { /* shown inline */ } }) }, 'Save settings')),
      h('section', { class: 'card' }, h('h2', null, 'About'),
        h('dl', { class: 'kv' }, h('dt', null, 'Version'), h('dd', null, meta.version), h('dt', null, 'Mode'), h('dd', null, meta.simulated ? 'Simulated controller' : 'Real controller'), h('dt', null, 'LED count'), h('dd', null, String(meta.led_count))),
        h('p', { class: 'help', style: { marginTop: '12px' } }, 'LED Controller runs your lights from this device: it keeps effects, run lists and schedules, and talks to the controller over Wi-Fi. Everything is stored locally.'))));
}
