// Provides state for a filter modal, including resetting form fields to current
// URL params when the modal is closed without applying.
function resetFilterModalState(formId) {
  return {
    showFilterModal: false,
    closeFilter() {
      const params = new URLSearchParams(window.location.search);
      const form = document.getElementById(formId);
      form.querySelectorAll('input[type=date]').forEach((el) => {
        el.value = params.get(el.name) || '';
      });
      form.querySelectorAll('[data-tomselect]').forEach((el) => {
        if (!el.tomselect) return;
        const values = params.getAll(el.name);
        el.tomselect.clear();
        if (values.length) el.tomselect.setValue(values);
      });
      this.showFilterModal = false;
    },
  };
}
window.resetFilterModalState = resetFilterModalState;

// Empties every visible field of a filter form without submitting it, so the user can pick new
// filters before applying. Hidden inputs (carried search, "filters submitted" marker) are kept.
function clearFilterForm(formId) {
  const form = document.getElementById(formId);
  form.querySelectorAll('select, input:not([type=hidden])').forEach((el) => {
    if (el.tomselect) {
      el.tomselect.clear();
      return;
    }
    el.value = '';
    // Let Alpine bindings (x-model) on the field see the change.
    el.dispatchEvent(
      new Event(el.tagName === 'SELECT' ? 'change' : 'input', {
        bubbles: true,
      }),
    );
  });
}
window.clearFilterForm = clearFilterForm;

// Fills a slug field from a name field as the user types, until they edit the slug themselves;
// clearing the slug hands it back to the name. Expects x-ref="name" and x-ref="slug" on the inputs.
function slugAutofill() {
  return {
    slugEdited: false,
    init() {
      // A form re-rendered with errors keeps a slug the user typed, so don't overwrite it.
      const slug = this.$refs.slug.value;
      this.slugEdited = slug !== '' && slug !== slugify(this.$refs.name.value);
    },
    nameChanged() {
      if (!this.slugEdited)
        this.$refs.slug.value = slugify(this.$refs.name.value);
    },
    slugChanged() {
      this.slugEdited = this.$refs.slug.value !== '';
    },
  };
}
window.slugAutofill = slugAutofill;

// Mirrors django.utils.text.slugify, so the suggested slug is the one the server would generate.
function slugify(value) {
  return value
    .normalize('NFKD')
    .replace(/[^\p{ASCII}]/gu, '')
    .toLowerCase()
    .replace(/[^\w\s-]/g, '')
    .replace(/[-\s]+/g, '-')
    .replace(/^[-_]+|[-_]+$/g, '');
}
