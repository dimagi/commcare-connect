import 'tom-select/dist/css/tom-select.min.css';
import TomSelect from 'tom-select';

document.addEventListener('DOMContentLoaded', () => {
  document.querySelectorAll('[data-tomselect]').forEach((el) => {
    el.removeAttribute('class');
    let plugins = [];
    if (!el.hasAttribute('data-tomselect:no-remove-button')) {
      plugins.push('remove_button');
    }
    new TomSelect(el, {
      plugins: plugins,
    });
  });
});
