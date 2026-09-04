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
  if (event.detail.xhr.responseText)
    el.textContent = event.detail.xhr.responseText;
  el.classList.remove('hidden');
}

// Exposed for the inline hx-on handlers in review_inaccessibility_panel.html
window.reviewFormBeforeRequest = reviewFormBeforeRequest;
window.reviewFormAfterRequest = reviewFormAfterRequest;
window.reviewFormResponseError = reviewFormResponseError;
