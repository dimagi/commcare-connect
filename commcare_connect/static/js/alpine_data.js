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

// Totals for the invoice list selection bar.
//
// The template combines this with the shared `selectableTable` mixin, which handles select-all:
//   x-data="{ ...selectableTable(), ...invoiceExportPricing() }"
//
// Define methods here, never getters. Spreading an object runs its getters immediately, before
// `selected` exists, which throws and stops Alpine from starting the component. The template
// calls these with `()`.
function invoiceExportPricing() {
  return {
    clearSelection() {
      this.selected = [];
      this.selectAll = false;
    },

    // Adds up the selected rows. An invoice has no USD amount until an exchange rate has been
    // applied to it, so those are counted separately rather than added in as zero.
    selectionTotals() {
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
          return totals;
        },
        { usd: 0, unpriced: 0 },
      );
    },

    selectedTotalUsd() {
      return this.selectionTotals().usd.toLocaleString(undefined, {
        minimumFractionDigits: 2,
        maximumFractionDigits: 2,
      });
    },

    selectedUnpricedCount() {
      return this.selectionTotals().unpriced;
    },

    // Rows the user can select, which is however many this page of the table shows.
    selectableRowCount() {
      return document.querySelectorAll('input[name="row_select"]').length;
    },
  };
}
window.invoiceExportPricing = invoiceExportPricing;
