function organizationWizard() {
  return {
    step: 1,
    totalSteps: 0,
    steps: [],
    titles: [],

    init() {
      this.steps = Array.from(this.$el.querySelectorAll('.wizard-step'));
      this.totalSteps = this.steps.length;
      this.titles = this.steps.map((el) => el.dataset.stepTitle || '');
      const failed = this.steps.findIndex((el) =>
        el.querySelector('[id^="error_"]'),
      );
      if (failed > -1) this.step = failed + 1;
    },

    back() {
      if (this.step > 1) this.step -= 1;
    },

    next() {
      if (!this.reportFirstInvalid(this.step)) return;
      if (this.step < this.totalSteps) this.step += 1;
    },

    onSubmit(event) {
      const failed = this.steps.findIndex((el) => this.firstInvalidIn(el));
      if (failed === -1) return;
      event.preventDefault();
      this.step = failed + 1;
      this.$nextTick(() => this.reportFirstInvalid(this.step));
    },

    reportFirstInvalid(step) {
      const invalid = this.firstInvalidIn(this.steps[step - 1]);
      if (invalid) invalid.reportValidity();
      return !invalid;
    },

    firstInvalidIn(stepEl) {
      return Array.from(
        stepEl.querySelectorAll('input, select, textarea'),
      ).find((el) => !el.disabled && !el.checkValidity());
    },
  };
}
window.organizationWizard = organizationWizard;
