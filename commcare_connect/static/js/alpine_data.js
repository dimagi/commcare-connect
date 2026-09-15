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

// Selection state for the invoice list: which invoices a bulk export covers and what they add
// up to. The USD amount of each row is carried on its checkbox as a data attribute.
function invoiceExportSelection() {
  const checkboxes = () =>
    Array.from(document.querySelectorAll('input[name="row_select"]'));

  return {
    selected: [],
    selectAll: false,

    toggleSelectAll() {
      this.selectAll = !this.selectAll;
      this.selected = this.selectAll
        ? checkboxes().map((box) => box.value)
        : [];
    },

    updateSelectAll() {
      const total = checkboxes().length;
      this.selectAll = total > 0 && this.selected.length === total;
    },

    clearSelection() {
      this.selected = [];
      this.selectAll = false;
    },

    get selectedTotalUsd() {
      const total = this.selected.reduce((sum, id) => {
        const box = document.querySelector(
          `input[name="row_select"][value="${id}"]`,
        );
        return sum + parseFloat(box?.dataset.amountUsd || 0);
      }, 0);
      return total.toLocaleString(undefined, {
        minimumFractionDigits: 2,
        maximumFractionDigits: 2,
      });
    },
  };
}
window.invoiceExportSelection = invoiceExportSelection;
