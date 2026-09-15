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

// Pricing extras for the invoice list selection bar. Composed with the shared `selectableTable`
// mixin, which owns the select-all behaviour: `{ ...selectableTable(), ...invoiceExportPricing() }`.
function invoiceExportPricing() {
  return {
    clearSelection() {
      this.selected = [];
      this.selectAll = false;
    },

    // Invoices carry no USD amount until an exchange rate has been applied, so the total covers
    // only the priced ones and the bar reports the rest separately. One pass over the rendered
    // checkboxes, rather than a DOM query per selected id.
    get selectionTotals() {
      const selected = new Set(this.selected.map(String));
      return Array.from(
        document.querySelectorAll('input[name="row_select"]'),
      ).reduce(
        (totals, box) => {
          if (!selected.has(String(box.value))) return totals;
          const amount = box.dataset.amountUsd;
          if (amount) {
            totals.usd += parseFloat(amount);
          } else {
            totals.unpriced += 1;
          }
          totals.rows += 1;
          return totals;
        },
        { usd: 0, unpriced: 0, rows: 0 },
      );
    },

    get selectedTotalUsd() {
      return this.selectionTotals.usd.toLocaleString(undefined, {
        minimumFractionDigits: 2,
        maximumFractionDigits: 2,
      });
    },

    get selectedUnpricedCount() {
      return this.selectionTotals.unpriced;
    },

    // How many rows the user can actually select here, which is one page of the table.
    get selectableRowCount() {
      return document.querySelectorAll('input[name="row_select"]').length;
    },
  };
}
window.invoiceExportPricing = invoiceExportPricing;
