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
  let sendMac = false, macIn = null;       // the controller ID is only sent when it was changed here
  const testOut = h('div', { 'aria-live': 'polite' });
  const findBtn = h('button', { type: 'button', onclick: act(async () => {
    render(found, h('span', { class: 'muted' }, 'Searching the network (about 2 seconds)...'));
    const list = await api.post('/controller/discover');
    render(found, list.length ? h('div', { class: 'stack' }, h('div', { class: 'small muted' }, 'Found ' + list.length + ' controller' + (list.length === 1 ? '' : 's') + '. Click one to use its address.'),
      list.map((c) => h('button', { type: 'button', style: { width: '100%', justifyContent: 'flex-start' }, onclick: () => { host.value = c.ip; st.controller_host = c.ip; st.controller_mac = c.mac; sendMac = true; if (macIn) macIn.value = c.mac; toast('Address and ID filled in. Remember to Save.', 'info', 3000); } },
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
  // ---- place: type an address, pick the match; only the place name is shown (the coordinates stay behind the scenes)
  const useTz = h('button', { type: 'button', class: 'small', onclick: () => {
    const zone = Intl.DateTimeFormat().resolvedOptions().timeZone;
    if (zone) { tz.value = zone; st.timezone = zone; toast('Time zone set to ' + zone + '. Remember to Save.', 'info', 3000); showSun(); }
  } }, "Use this computer's time zone");
  const addrId = nextId('addr');
  const addrIn = h('input', { type: 'text', id: addrId, placeholder: 'Street address, town or ZIP code', autocomplete: 'street-address', spellcheck: 'false' });
  const placeNow = h('div', { class: 'help' });
  const sunNow = h('div', { class: 'help', 'aria-live': 'polite' });
  const results = h('div', { class: 'stack' });
  function showPlace() {
    placeNow.textContent = st.latitude !== null && st.latitude !== undefined
      ? 'Using: ' + (st.location_name || 'your chosen place') : 'No place chosen yet: sunrise and sunset use rough default times (7:00 am and 7:00 pm).';
  }
  async function showSun() {
    sunNow.textContent = '';
    if (st.latitude === null || st.latitude === undefined) return;
    try {
      const q = new URLSearchParams({ latitude: String(st.latitude), longitude: String(st.longitude) });
      if (st.timezone) q.set('timezone', st.timezone);
      const r = await api.get('/location/sun?' + q.toString());
      const t = (iso) => new Date(iso).toLocaleTimeString([], { hour: 'numeric', minute: '2-digit', timeZone: st.timezone || undefined });
      sunNow.textContent = r.configured ? 'Today there: sunrise ' + t(r.sunrise) + ', sunset ' + t(r.sunset) + ' (' + r.timezone + ')' + (st.timezone ? '' : '. Choose a time zone above if this looks off by hours.') : '';
    } catch (e) { sunNow.textContent = ''; }
  }
  const lookupBtn = h('button', { type: 'button', onclick: act(async () => {
    render(results, h('span', { class: 'muted' }, 'Looking it up...'));
    try {
      const r = await api.post('/location/search', { query: addrIn.value });
      render(results, h('div', { class: 'stack' }, h('div', { class: 'small muted' }, 'Pick the right one:'),
        r.results.map((c) => h('button', { type: 'button', style: { width: '100%', justifyContent: 'flex-start', textAlign: 'left' }, onclick: () => {
          st.latitude = c.latitude; st.longitude = c.longitude; st.location_name = c.name;
          render(results); showPlace(); showSun(); toast('Place chosen. Remember to Save.', 'info', 3000);
        } }, c.name))));
    } catch (e) { render(results, h('div', { class: 'inline-error', role: 'alert' }, e.message)); }
  }) }, 'Look up');
  addrIn.addEventListener('keydown', (e) => { if (e.key === 'Enter') { e.preventDefault(); lookupBtn.click(); } });
  const clearPlace = h('button', { type: 'button', class: 'small', onclick: () => { st.latitude = null; st.longitude = null; st.location_name = ''; render(results); showPlace(); showSun(); } }, 'Clear place');
  showPlace(); showSun();

  // ---- change password
  const pwCur = h('input', { type: 'password', id: nextId('pwc'), autocomplete: 'current-password' });
  const pwNew = h('input', { type: 'password', id: nextId('pwn'), autocomplete: 'new-password' });
  const pwNew2 = h('input', { type: 'password', id: nextId('pwn2'), autocomplete: 'new-password' });
  const pwErr = h('div', { class: 'inline-error', role: 'alert' });
  const pwBtn = h('button', { type: 'button', class: 'primary', onclick: act(async () => {
    pwErr.textContent = '';
    if (pwNew.value.length < 8) { pwErr.textContent = 'The new password must be at least 8 characters.'; return; }
    if (pwNew.value !== pwNew2.value) { pwErr.textContent = 'The two new passwords do not match.'; return; }
    try {
      await api.post('/password', { current: pwCur.value, new: pwNew.value });
      pwCur.value = pwNew.value = pwNew2.value = '';
      toast('Password changed. Every other browser and terminal has been signed out.', 'info', 6000);
    } catch (e) { pwErr.textContent = e.message; }
  }) }, 'Change password');

  // ---- self-healing and alerts
  const txt = (key, label, help, { type = 'text', placeholder = '' } = {}) => {
    const id = nextId('t');
    const i = h('input', { type, id, value: st[key] || '', placeholder, autocomplete: 'off', spellcheck: 'false' });
    i.addEventListener('input', () => { st[key] = i.value.trim(); });
    return F(label, i, help, id);
  };
  const macId = nextId('mac');
  macIn = h('input', { type: 'text', id: macId, value: st.controller_mac || '', placeholder: 'Learned automatically', autocomplete: 'off', spellcheck: 'false' });
  macIn.addEventListener('input', () => { st.controller_mac = macIn.value.trim(); sendMac = true; });
  let smtpPw = '';
  const pwId = nextId('smtppw');
  const smtpPwIn = h('input', { type: 'password', id: pwId, autocomplete: 'new-password', placeholder: st.smtp_password_set ? '(saved - leave blank to keep it)' : '' });
  smtpPwIn.addEventListener('input', () => { smtpPw = smtpPwIn.value; });
  const alertMsg = h('div', { class: 'inline-error', role: 'status' });
  const usage = h('p', { class: 'help', 'aria-live': 'polite' });
  api.get('/health').then((hl) => { usage.textContent = 'Alerts sent in the last 24 hours: ' + hl.alerts.sent_24h + ' of the ' + hl.alerts.limit + ' allowed.' + (hl.alerts.retry_at ? ' A delivery failed recently; the next try is at ' + new Date(hl.alerts.retry_at * 1000).toLocaleTimeString() + '.' : ''); }).catch(() => {});
  const testAlertBtn = h('button', { type: 'button', onclick: act(async () => {
    alertMsg.textContent = ''; alertMsg.style.color = '';
    await save(true);
    try { const r = await api.post('/health/test-alert'); alertMsg.style.color = 'var(--success)'; alertMsg.textContent = 'Sent through ' + r.channels.join(' and ') + '. Check your inbox or phone.'; }
    catch (e) { alertMsg.textContent = e.message; }
  }) }, 'Send a test alert');

  async function save(quiet) {
    showErr(null);
    const body = {
      controller_host: st.controller_host, controller_port: st.controller_port, led_count: st.led_count, fps: st.fps, min_gap_ms: st.min_gap_ms,
      max_groups: st.max_groups, brightness: st.brightness, idle_action: st.idle_action, idle_color: st.idle_color,
      timezone: st.timezone, latitude: st.latitude, longitude: st.longitude, location_name: st.location_name, log_retention_days: st.log_retention_days,
      health_check_sec: st.health_check_sec, alert_after_minutes: st.alert_after_minutes, alert_reminder_hours: st.alert_reminder_hours,
      alert_email: st.alert_email, smtp_host: st.smtp_host, smtp_port: st.smtp_port, smtp_user: st.smtp_user, smtp_tls: st.smtp_tls,
      smtp_from: st.smtp_from, alert_webhook: st.alert_webhook,
      alert_daily_limit: st.alert_daily_limit, alert_min_gap_minutes: st.alert_min_gap_minutes, alert_on_move: st.alert_on_move,
    };
    if (smtpPw) body.smtp_password = smtpPw;             // write-only: sent only when typed
    if (sendMac) body.controller_mac = st.controller_mac;
    try {
      const r = await api.put('/settings', body);
      Object.assign(cfg, r); invalidate('settings'); setDisplayTz(r.timezone); await refresh();
      smtpPw = ''; smtpPwIn.value = ''; sendMac = false; macIn.value = r.controller_mac || ''; st.controller_mac = r.controller_mac; st.smtp_password_set = r.smtp_password_set;
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
          num('log_retention_days', 'Keep logs for (days)', 'Older log lines are deleted automatically. At least 31 days.', { min: 31, max: 3650 }),
          num('max_groups', 'Most colors per frame', 'Each distinct color in a frame costs one command (2 to 30).', { min: 2, max: 30 })),
        h('div', { class: 'form-grid' },
          F('When nothing is scheduled', idleSel, 'What the lights do after a run list ends or you press Stop.', idleSel.id), idleColorWrap)),
      h('section', { class: 'card stack' }, h('h2', null, 'Time and place'),
        h('div', { class: 'form-grid' }, F('Time zone', tz, 'Start typing to search, for example America/Chicago. Leave blank to use the computer\'s own zone.', tzId), tzList),
        h('div', { class: 'row' }, useTz),
        F('Where are your lights?', addrIn, 'Used only to work out sunrise and sunset for schedules. The address is looked up once (through OpenStreetMap) and only the place found is saved.', addrId),
        h('div', { class: 'row' }, lookupBtn, clearPlace), results, placeNow, sunNow),
      h('p', { class: 'help', style: { marginTop: '-8px' } }, 'Address lookup by OpenStreetMap Nominatim (data © OpenStreetMap contributors).'),
      h('section', { class: 'card stack' }, h('h2', null, 'Self-healing and alerts'),
        h('p', { class: 'help' }, 'The program checks that the controller is answering and, if its network address changes (for example after a router restart), finds it again by its ID and follows it automatically. Every problem is recorded on the Dashboard. To be told about a problem, set up an email or a webhook below.'),
        h('div', { class: 'form-grid' }, F('Controller ID (MAC address)', macIn, 'What identifies your controller even if its address changes. Learned automatically; clear it only if you replaced the controller.', macId),
          num('health_check_sec', 'Check every (seconds)', 'How often to check the controller while all is well (10 to 600). It checks every 10 seconds while something is wrong.', { min: 10, max: 600 })),
        h('div', { class: 'form-grid' }, num('alert_after_minutes', 'Alert me after (minutes)', 'Send one alert when the controller has been unreachable this long.', { min: 1, max: 1440 }),
          num('alert_reminder_hours', 'Remind me every (hours)', 'While it stays down.', { min: 1, max: 168 })),
        h('h3', { style: { margin: '8px 0 0' } }, 'Keeping alerts to the important ones'),
        h('p', { class: 'help' }, 'Only a controller that stays unreachable, its recovery, and the watchdog finding the program unhealthy are sent. Routine things (a brief drop that fixes itself, an address change that was followed) are only recorded in the log. These limits keep a free push plan such as ntfy.sh (250 messages a day) safe even if something misbehaves.'),
        h('div', { class: 'form-grid' }, num('alert_daily_limit', 'Most alerts in 24 hours', 'A hard cap on everything that is sent (1 to 200). Test alerts count too.', { min: 1, max: 200 }),
          num('alert_min_gap_minutes', 'Minutes between "down" alerts', 'If the connection keeps dropping and returning, a new "down" alert waits this long after the last one.', { min: 1, max: 1440 })),
        h('div', null, switchEl(st.alert_on_move, (v) => { st.alert_on_move = v; }, 'Also tell me when the controller changed address and was followed automatically (not critical)')),
        usage,
        h('h3', { style: { margin: '8px 0 0' } }, 'Email'),
        h('div', { class: 'form-grid' }, txt('alert_email', 'Send alerts to', 'One or more addresses, separated by commas.', { placeholder: 'you@example.com' }), txt('smtp_from', 'From address', 'Optional.')),
        h('div', { class: 'form-grid' }, txt('smtp_host', 'Mail server', 'For example smtp.gmail.com (it needs port 587 and an app password, not your normal password), or your own relay.'), num('smtp_port', 'Port', '587 (STARTTLS) is right for Gmail and most providers; 465 is SSL. Port 25 is blocked by almost every home internet provider.', { min: 1, max: 65535 })),
        h('div', { class: 'form-grid' }, txt('smtp_user', 'Mail user name', 'Leave blank for a relay that needs no login.'), F('Mail password', smtpPwIn, 'Saved on this computer and never shown again.', pwId)),
        h('div', null, switchEl(st.smtp_tls, (v) => { st.smtp_tls = v; }, 'Use encryption (STARTTLS) when the server offers it')),
        h('h3', { style: { margin: '8px 0 0' } }, 'Webhook (phone notifications)'),
        txt('alert_webhook', 'Webhook address', 'An http(s) address that receives the alert as a POST, for example https://ntfy.sh/your-secret-topic (then install the free ntfy app on your phone).', { placeholder: 'https://ntfy.sh/your-secret-topic' }),
        h('div', { class: 'row' }, testAlertBtn), alertMsg),
      h('div', { class: 'row' }, h('button', { class: 'primary', type: 'button', onclick: act(async () => { try { await save(false); } catch (e) { /* shown inline */ } }) }, 'Save settings')),
      h('section', { class: 'card stack' }, h('h2', null, 'Password'),
        h('p', { class: 'help' }, 'Change the password for this web page and the command line. You stay signed in here; every other browser and terminal must sign in again with the new one.'),
        h('div', { class: 'form-grid' }, F('Current password', pwCur, null, pwCur.id), h('div')),
        h('div', { class: 'form-grid' }, F('New password', pwNew, 'At least 8 characters.', pwNew.id), F('New password again', pwNew2, null, pwNew2.id)),
        pwErr, h('div', { class: 'row' }, pwBtn)),
      h('section', { class: 'card' }, h('h2', null, 'About'),
        h('dl', { class: 'kv' }, h('dt', null, 'Version'), h('dd', null, meta.version), h('dt', null, 'Mode'), h('dd', null, meta.simulated ? 'Simulated controller' : 'Real controller'), h('dt', null, 'LED count'), h('dd', null, String(meta.led_count))),
        h('p', { class: 'help', style: { marginTop: '12px' } }, 'LED Controller runs your lights from this device: it keeps effects, run lists and schedules, and talks to the controller over Wi-Fi. Everything is stored locally.'))));
}
