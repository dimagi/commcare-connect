// Totals for the invoice list selection bar.
//
// The template combines this with the shared `selectableTable` mixin, which handles selecting and
// clearing rows:
//   x-data="{ ...selectableTable(), ...invoiceExportPricing() }"
//
// Define methods here, never getters. Spreading an object runs its getters immediately, before
// `selected` exists, which throws and stops Alpine from starting the component. The template
// calls these with `()`.
function invoiceExportPricing() {
  return {
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
  };
}
window.invoiceExportPricing = invoiceExportPricing;
