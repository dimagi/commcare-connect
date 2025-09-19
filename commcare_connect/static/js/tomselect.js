import TomSelect from 'tom-select';

document.addEventListener('DOMContentLoaded', () => {
  document.querySelectorAll('[data-tomselect]').forEach((el) => {
    el.removeAttribute('class');
    new TomSelect(el, {
      plugins: ['remove_button'],
    });
  });
});
