// Adds a row to a Django formset by cloning its empty form, numbered by the current TOTAL_FORMS.
function formsetRows() {
  return {
    addRow() {
      const total = this.$root.querySelector('input[name$="-TOTAL_FORMS"]');
      const row = this.$refs.emptyRow.innerHTML.replaceAll(
        '__prefix__',
        total.value,
      );
      this.$refs.rows.insertAdjacentHTML('beforeend', row);
      total.value = Number(total.value) + 1;
      window.initTomSelectElements();
    },
  };
}
window.formsetRows = formsetRows;
