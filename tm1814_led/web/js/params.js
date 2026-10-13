// Schema-driven parameter form. The server describes each parameter (Param.to_dict); we render matching controls.
import { h, nextId, normHex, switchEl } from './ui.js';

/** One color input: native color picker + hex text box, kept in sync. onChange(hex). */
export function colorInput(value, onChange, label) {
  const id = nextId('c');
  const pick = h('input', { type: 'color', value: normHex(value) || '#000000', id, 'aria-label': label || 'Color' });
  const txt = h('input', { type: 'text', value: normHex(value) || value, maxlength: 7, spellcheck: 'false', 'aria-label': (label || 'Color') + ' as hex' });
  pick.addEventListener('input', () => { txt.value = pick.value; txt.classList.remove('invalid'); onChange(pick.value); });
  txt.addEventListener('input', () => {
    const v = normHex(txt.value);
    if (v) { pick.value = v; txt.classList.remove('invalid'); onChange(v); } else txt.classList.add('invalid');
  });
  txt.addEventListener('blur', () => { const v = normHex(txt.value); if (v) txt.value = v; });
  const el = h('div', { class: 'color-row' }, pick, txt);
  el.set = (v) => { pick.value = normHex(v) || '#000000'; txt.value = normHex(v) || v; txt.classList.remove('invalid'); };
  return el;
}

/** Editable list of colors with add / remove / reorder. */
export function colorsEditor(values, min, max, onChange) {
  const list = values.slice();
  const wrap = h('div');
  const emit = () => onChange(list.slice());
  function draw() {
    wrap.replaceChildren();
    const sw = h('div', { class: 'swatches' });
    list.forEach((c, i) => {
      const pick = h('input', { type: 'color', value: normHex(c) || '#000000', 'aria-label': 'Color ' + (i + 1) });
      pick.addEventListener('input', () => { list[i] = pick.value; hexTxt.value = pick.value; emit(); });
      const hexTxt = h('input', { type: 'text', value: c, maxlength: 7, 'aria-label': 'Color ' + (i + 1) + ' hex', style: { width: '78px', minHeight: '28px', fontFamily: 'var(--mono)', fontSize: '.8rem', padding: '0 4px' } });
      hexTxt.addEventListener('input', () => { const v = normHex(hexTxt.value); if (v) { list[i] = v; pick.value = v; hexTxt.classList.remove('invalid'); emit(); } else hexTxt.classList.add('invalid'); });
      sw.append(h('div', { class: 'swatch-item' }, pick, hexTxt, h('div', { class: 'mini' },
        h('button', { type: 'button', class: 'ghost', disabled: i === 0, 'aria-label': 'Move color ' + (i + 1) + ' left', onclick: () => { [list[i - 1], list[i]] = [list[i], list[i - 1]]; draw(); emit(); } }, '←'),
        h('button', { type: 'button', class: 'ghost', disabled: i === list.length - 1, 'aria-label': 'Move color ' + (i + 1) + ' right', onclick: () => { [list[i + 1], list[i]] = [list[i], list[i + 1]]; draw(); emit(); } }, '→'),
        h('button', { type: 'button', class: 'ghost', disabled: list.length <= min, 'aria-label': 'Remove color ' + (i + 1), onclick: () => { list.splice(i, 1); draw(); emit(); } }, '×'))));
    });
    const add = h('button', { type: 'button', disabled: list.length >= max, onclick: () => { list.push(list.length ? list[list.length - 1] : '#ff0000'); draw(); emit(); } }, '+ Add color');
    wrap.append(sw, h('div', { style: { marginTop: '8px' } }, add, h('span', { class: 'help', style: { marginLeft: '10px' } }, list.length + ' of ' + min + '–' + max + ' colors')));
  }
  draw();
  return wrap;
}

function numberControl(p, value, onChange) {
  const isInt = p.type === 'int';
  const step = p.step || (isInt ? 1 : 0.1);
  const hasRange = p.min !== undefined && p.max !== undefined;
  const num = h('input', { type: 'number', value, min: p.min, max: p.max, step, 'aria-label': p.label });
  const set = (v) => { onChange(isInt ? Math.round(v) : v); };
  num.addEventListener('input', () => { if (num.value !== '' && !isNaN(Number(num.value))) { if (range) range.value = num.value; set(Number(num.value)); } });
  let range = null;
  if (hasRange) {
    range = h('input', { type: 'range', min: p.min, max: p.max, step, value, 'aria-label': p.label + ' slider' });
    range.addEventListener('input', () => { num.value = range.value; set(Number(range.value)); });
  }
  return h('div', { class: 'slider-row' }, range, num, p.unit ? h('span', { class: 'unit' }, p.unit) : null);
}

/**
 * Build the form. Returns { el, values, errors(map key -> message setter) }.
 * `values` is a live object mutated as the user edits; onChange(values) fires after each edit.
 * Parameters of type "frames" are skipped (the frame studio handles them).
 */
export function buildParamForm(schema, initial, onChange) {
  const values = {};
  const errBoxes = {};
  const el = h('div');
  for (const p of schema.params) {
    if (p.type === 'frames') continue;
    values[p.key] = initial && initial[p.key] !== undefined && initial[p.key] !== null ? initial[p.key] : p.default;
    const id = nextId('p');
    const emit = (v) => { values[p.key] = v; if (errBoxes[p.key]) errBoxes[p.key].textContent = ''; onChange(values); };
    let control, labelFor = id;
    switch (p.type) {
      case 'int': case 'float': control = numberControl(p, values[p.key], emit); break;
      case 'bool': control = switchEl(!!values[p.key], emit, null); labelFor = null; break;
      case 'choice': {
        const sel = h('select', { id }, p.choices.map((c) => h('option', { value: String(c.value), selected: String(c.value) === String(values[p.key]) }, c.label)));
        sel.addEventListener('change', () => { const c = p.choices.find((x) => String(x.value) === sel.value); emit(c.value); });
        control = sel; break;
      }
      case 'color': control = colorInput(values[p.key], emit, p.label); labelFor = null; break;
      case 'colors': control = colorsEditor(values[p.key], p.min !== undefined ? p.min : 1, p.max !== undefined ? p.max : 64, emit); labelFor = null; break;
      default: control = h('input', { type: 'text', id, value: JSON.stringify(values[p.key]), disabled: true });
    }
    const err = h('div', { class: 'inline-error', role: 'alert' });
    errBoxes[p.key] = err;
    el.append(h('div', { class: 'param' },
      h('label', { class: 'field-label', for: labelFor || undefined }, p.label),
      control, p.help ? h('div', { class: 'help' }, p.help) : null, err));
  }
  return { el, values, errBoxes };
}
