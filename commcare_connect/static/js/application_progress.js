// Tracks the application form's answered questions as the applicant fills them in, for its progress strip.
// Each question is an element with data-question (and data-required when it must be answered); one with
// data-saved-file already has an uploaded answer.
function applicationProgress() {
  return {
    answered: 0,
    total: 0,
    requiredLeft: 0,
    init() {
      this.update();
      this.$root.addEventListener('input', () => this.update());
      this.$root.addEventListener('change', () => this.update());
    },
    update() {
      const questions = [...this.$root.querySelectorAll('[data-question]')];
      let answered = 0;
      let requiredLeft = 0;
      for (const question of questions) {
        if (isAnswered(question)) {
          answered += 1;
        } else if (question.hasAttribute('data-required')) {
          requiredLeft += 1;
        }
      }
      this.total = questions.length;
      this.answered = answered;
      this.requiredLeft = requiredLeft;
    },
    get percent() {
      return this.total ? Math.round((this.answered / this.total) * 100) : 0;
    },
  };
}

function isAnswered(question) {
  if (question.hasAttribute('data-saved-file')) {
    return true;
  }
  return [...question.querySelectorAll('input, textarea, select')].some(
    (field) =>
      field.type !== 'hidden' &&
      (field.type === 'file'
        ? field.files.length > 0
        : field.value.trim() !== ''),
  );
}

window.applicationProgress = applicationProgress;
