function reviewFormBeforeRequest(form) {
  form.querySelectorAll('button').forEach((b) => (b.disabled = true));
  document
    .getElementById('review-inaccessibility-error')
    .classList.add('hidden');
}

function reviewFormAfterRequest(form) {
  form.querySelectorAll('button').forEach((b) => (b.disabled = false));
}

function reviewFormResponseError(event) {
  const el = document.getElementById('review-inaccessibility-error');
  const xhr = event.detail.xhr;
  // Only a plain-text body is written for this box. Anything else is an error page, whose
  // markup would otherwise be dumped into the panel, so fall back to the generic message.
  const isPlainText = (xhr.getResponseHeader('Content-Type') || '').startsWith(
    'text/plain',
  );
  const message = isPlainText && xhr.responseText.trim();
  if (message) el.textContent = message;
  el.classList.remove('hidden');
}

// Exposed for the inline hx-on handlers in review_inaccessibility_panel.html
window.reviewFormBeforeRequest = reviewFormBeforeRequest;
window.reviewFormAfterRequest = reviewFormAfterRequest;
window.reviewFormResponseError = reviewFormResponseError;
