import { h, render, pageHead } from '../ui.js';

export function help(ctx) {
  const sec = (title, ...kids) => h('section', { class: 'card stack' }, h('h2', null, title), ...kids);
  render(ctx.app, pageHead('Help'),
    h('div', { class: 'stack' },
      sec('Two kinds of effects',
        h('p', null, h('span', { class: 'badge native' }, 'Controller-native'), ' effects run on the controller itself. One command is sent and the controller animates by itself, so they keep running even if this program stops or the network drops. They are smooth at any speed, but they cannot be previewed in the browser.'),
        h('p', null, h('span', { class: 'badge streamed' }, 'Streamed'), ' effects are drawn by this program, frame by frame (about 5 frames per second), and sent to the strip. They need this program to keep running, but they can be previewed here and you can fine-tune every detail, including your own frame-by-frame animations.')),
      sec('Effects',
        h('p', null, 'The Effects page is your catalog. Press Play on lights to try one right away. Edit changes its settings with a live preview; you can also try unsaved changes on the lights before saving. Duplicate an effect to make a variation without touching the original.')),
      sec('Run lists',
        h('p', null, 'A run list is a playlist: effects shown one after another, each for a time you choose. It can loop forever, play once, or shuffle. Reorder items by dragging the handle or using the arrows.')),
      sec('Schedules',
        h('p', null, 'A schedule plays a run list automatically in a time window: for example every day from sunset to 11 pm, or the first Saturday of each month. Times can be a clock time or sunrise and sunset with an offset in minutes. Sunrise and sunset need your latitude and longitude in Settings.'),
        h('p', null, 'If two schedules overlap, the one with the higher priority plays. The Next occurrences panel shows exactly when a schedule will start.')),
      sec('What does Stop do?',
        h('p', null, 'Stop ends what is playing now and puts the lights in their idle state (set in Settings: off, unchanged, or a solid color). It stays off until the next scheduled window begins; it does not disable your schedules. To stop a schedule for good, switch it off on the Schedules page.')),
      sec('Quick tips',
        h('ul', null,
          h('li', null, 'The master brightness slider on the Dashboard dims everything.'),
          h('li', null, 'Power off also stops the current run list.'),
          h('li', null, 'In the frame studio, drag across the strip to select LEDs, then Fill or Gradient. Press the Shortcuts button there to see keyboard shortcuts.'),
          h('li', null, 'If something looks wrong, check the Logs page.')))));
}
