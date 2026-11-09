// Alpine components for the "Add visits" page (opportunity/add_visits_existing_users.html).
//
// Strings go through Django's JavaScript catalog (`gettext` / `interpolate`), which the template
// loads from the `javascript-catalog` URL before this file.

// Switches between the existing and new worker tabs, keeping the active tab in the URL.
function opportunityBudgetComponent(initialTab, availableTabs) {
  const tabs = availableTabs.map((t) => t.key);
  return {
    selectedTab: tabs.includes(initialTab) ? initialTab : 'existing_workers',
    setTab(tab) {
      this.selectedTab = tab;

      const url = new URL(window.location);
      url.searchParams.set('active_tab', tab);
      window.history.pushState({}, '', url);
    },
  };
}
window.opportunityBudgetComponent = opportunityBudgetComponent;

// Form state for changing the visit limit and end date of the selected existing workers.
function handleExistingBudgetComponent(perVisitCosts) {
  return {
    modalOpen: false,
    numberOfVisits: null,
    adjustmentType: null,
    perVisitCosts: perVisitCosts,
    selected_users: [],
    end_date: '',
    openModal() {
      this.modalOpen = true;
    },
    closeModal() {
      this.modalOpen = false;
    },
    isSelected(id) {
      return this.selected_users.includes(id);
    },
    toggleSelectAll() {
      const checkboxes = document.querySelectorAll(
        'input[name="selected_users"]',
      );
      if (this.selected_users.length === checkboxes.length - 1) {
        this.selected_users = [];
      } else {
        this.selected_users = Array.from(checkboxes)
          .filter((cb) => cb.id !== 'select_all_users')
          .map((cb) => cb.value);
      }
    },
    totalVisits() {
      return this.selected_users.length * (parseInt(this.numberOfVisits) || 0);
    },
    calculatedBudget() {
      const numberOfVisits = parseInt(this.numberOfVisits) || 0;
      const selectedCost = this.selected_users.reduce(
        (total, id) => total + (this.perVisitCosts[id] || 0),
        0,
      );
      return numberOfVisits * selectedCost;
    },
    // Rendered with x-html: the placeholders are filled with numbers only, so the markup comes
    // from the translation catalog alone.
    adjustmentSummary() {
      const message =
        this.adjustmentType === 'increase_visits'
          ? gettext(
              'You are about to increase by <b>%(visits)s</b> visit(s) which will result in a budget increase of <b>%(budget)s</b> to this Opportunity.',
            )
          : gettext(
              'You are about to decrease by <b>%(visits)s</b> visit(s) which will result in a budget decrease of <b>%(budget)s</b> to this Opportunity.',
            );
      return interpolate(
        message,
        { visits: this.totalVisits(), budget: this.calculatedBudget() },
        true,
      );
    },
    // Validation methods
    hasnumberOfVisitsOrEndDate() {
      return Boolean(this.numberOfVisits > 0 || this.end_date);
    },
    hasValidVisitsAndAdjustment() {
      if (this.numberOfVisits > 0 && !this.adjustmentType) {
        return false;
      }
      if (this.adjustmentType && !(this.numberOfVisits > 0)) {
        return false;
      }
      return true;
    },
    isFormValid() {
      return (
        this.selected_users.length > 0 &&
        this.hasnumberOfVisitsOrEndDate() &&
        this.hasValidVisitsAndAdjustment()
      );
    },
    getValidationErrors() {
      const errors = [];
      if (this.selected_users.length === 0) {
        errors.push(gettext('Please select at least one worker'));
      }
      if (!this.hasnumberOfVisitsOrEndDate()) {
        errors.push(
          gettext('Please specify either number of visits (>0) or end date'),
        );
      }
      if (!this.hasValidVisitsAndAdjustment()) {
        if (this.numberOfVisits > 0 && !this.adjustmentType) {
          errors.push(
            gettext('Please select an adjustment type (Increase or Decrease)'),
          );
        }
        if (this.adjustmentType && !(this.numberOfVisits > 0)) {
          errors.push(gettext('Please specify the number of visits (>0)'));
        }
      }
      return errors;
    },
  };
}
window.handleExistingBudgetComponent = handleExistingBudgetComponent;
