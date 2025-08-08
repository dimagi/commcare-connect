/**
 * Centralized registry for custom Alpine.js components used across the project.
 *
 * All components are initialized within the `alpine:init` event to ensure
 * they are registered before Alpine processes the DOM. This prevents
 * reference errors and ensures reliable initialization of dynamic UI elements.
 */

document.addEventListener('alpine:init', () => {
  // used in user_visit_verification_table.html
  Alpine.data('visitVerification', (initialTab, defaultPage) => ({
    activeTab: initialTab,
    page: defaultPage,
    selected: [],
    showBulkApproveModal: false,
    showBulkRejectModal: false,
    selectAll: false,

    init() {
      this.updateVisitStatusMap();
    },

    updateVisitStatusMap() {
      const tableRows = document.querySelectorAll(
        'tbody tr[data-visit-status]',
      );
      this.visitStatusMap = {};
      tableRows.forEach((row) => {
        const visitId = row.getAttribute('data-visit-id');
        const status = row.getAttribute('data-visit-status');
        if (visitId && status) {
          this.visitStatusMap[visitId] = status;
        }
      });
    },

    get selectedStatusCounts() {
      const counts = {
        pending: 0,
        approved: 0,
        rejected: 0,
        duplicate: 0,
        overLimit: 0,
        total: this.selected.length,
      };

      this.selected.forEach((visitId) => {
        const status = this.visitStatusMap[visitId];
        if (status === 'pending') counts.pending++;
        else if (status === 'approved') counts.approved++;
        else if (status === 'rejected') counts.rejected++;
        else if (status === 'duplicate') counts.duplicate++;
        else if (status === 'over_limit') counts.overLimit++;
      });

      return counts;
    },

    updateUrlAndRequest() {
      const url = new URL(window.location);
      const formData = new FormData(this.$refs.visitForm);
      const params = new URLSearchParams();

      for (let [key, value] of formData.entries()) {
        if (value && String(value).trim() !== '') {
          params.set(key, value);
        }
      }

      url.search = params.toString();
      window.history.pushState({}, '', url.toString());
      this.$dispatch('reload_table');

      this.$nextTick(() => {
        this.updateVisitStatusMap();
      });
    },

    toggleSelectAll() {
      this.selectAll = !this.selectAll;
      if (this.selectAll) {
        this.selected = Array.from(
          document.querySelectorAll('input[type=checkbox][x-model=selected]'),
        ).map((cb) => cb.value);
      } else {
        this.selected = [];
      }
    },

    updateSelectAll() {
      const checkboxes = document.querySelectorAll(
        'input[type=checkbox][x-model=selected]',
      );
      this.selectAll =
        checkboxes.length > 0 && this.selected.length === checkboxes.length;
    },
  }));
});
