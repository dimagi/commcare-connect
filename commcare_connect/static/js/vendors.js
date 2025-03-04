import '@popperjs/core';
import Alpine from 'alpinejs';
import './htmx';
import 'htmx.org/dist/ext/loading-states';

import flatpickr from 'flatpickr';
import monthSelectPlugin from 'flatpickr/dist/esm/plugins/monthSelect';
import 'flatpickr/dist/flatpickr.css';
import 'flatpickr/dist/plugins/monthSelect/style.css';
window.monthPickr = (id, flatPickrConfig = {}, monthPluginConfig = {}) =>
  flatpickr(id, {
    plugins: [new monthSelectPlugin({ ...monthPluginConfig })],
    ...flatPickrConfig,
  });

window.Alpine = Alpine;
Alpine.start();
