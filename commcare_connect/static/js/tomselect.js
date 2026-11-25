import 'tom-select/dist/css/tom-select.min.css';
import '../css/tomselect-overrides.css';
import TomSelect from 'tom-select';

window.TomSelect = TomSelect;

function initTomSelectElements() {
  document.querySelectorAll('[data-tomselect]').forEach((el) => {
    if (el.tomselect) return;
    el.removeAttribute('class');
    let plugins = [];
    if (!el.hasAttribute('data-tomselect:no-remove-button')) {
      plugins.push('remove_button');
    }
    let settings = {
      plugins: plugins,
      render: {
        option_create: function (data, escape) {
          return (
            "<div class='create'><i class='fa-solid fa-plus'></i> Create <strong>" +
            escape(data.input) +
            '</strong>&hellip;</div>'
          );
        },
      },
    };
    if (el.hasAttribute('data-tomselect:settings')) {
      let data = el.getAttribute('data-tomselect:settings');
      Object.assign(settings, JSON.parse(data));
    }
    if (settings.create === true) {
      // Must match TOMSELECT_NEW_ENTRY_PREFIX in commcare_connect/utils/forms.py
      settings.create = (input) => ({ value: 'new:' + input, text: input });
    }
    const ts = new TomSelect(el, settings);
    ts.on('change', () => {
      el.dispatchEvent(new Event('change', { bubbles: true }));
    });
  });

  document.dispatchEvent(new CustomEvent('tomselect-elements:initialized'));
}

document.addEventListener('DOMContentLoaded', initTomSelectElements);

// Content swapped in by htmx arrives after DOMContentLoaded. Waiting for the settle step
// matters: htmx copies the attributes of the same-id element it is replacing onto the incoming
// one and restores the server-rendered values during settle, which would strip the classes
// tom-select adds if it ran at insertion time.
document.addEventListener('htmx:afterSettle', initTomSelectElements);

// Alpine's <template x-if>/<x-for> content is inert until Alpine mounts it, and mounting fires
// no htmx event, so those subtrees have to call this themselves from x-init.
window.initTomSelectElements = initTomSelectElements;
