// State for the audit review modal (audit/audit_report_task_modal.html).
function auditTaskModal() {
  return {
    confirm: false,
    saving: false,
    error: '',
    init() {
      // One modal at a time, without interrupting a save (that would lose its table refresh).
      const others = Array.from(
        document.querySelectorAll('[data-audit-task-modal]'),
      ).filter((el) => el !== this.$root);
      if (others.some((el) => window.Alpine.$data(el).saving)) {
        this.$root.remove();
        return;
      }
      others.forEach((el) => el.remove());
    },
    close() {
      if (!this.saving) this.$root.remove();
    },
    startSaving() {
      this.saving = true;
    },
    finishSaving(event) {
      this.saving = false;
      if (event.detail.successful) return;
      this.error =
        event.detail.xhr.responseText || this.$root.dataset.errorFallback;
      this.confirm = false;
    },
  };
}
window.auditTaskModal = auditTaskModal;
