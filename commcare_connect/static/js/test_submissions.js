// Local "Test submissions" page: keeps the submission log readable when a request fails.
// A failed htmx request swaps nothing, so without this a 500 (e.g. a post-commit task that could
// not reach ConnectID) would leave no trace on the page even though the form may be recorded.

function prependLogEntry(log, message) {
  const entry = document.createElement('div');
  entry.className =
    'rounded-lg border border-red-200 p-3 text-sm flex flex-col gap-1';
  const text = document.createElement('p');
  text.className = 'text-red-600';
  text.textContent = message;
  entry.appendChild(text);
  log.prepend(entry);
}

document.addEventListener('htmx:responseError', (event) => {
  if (!event.detail.elt.closest('[data-test-submission-form]')) {
    return;
  }
  const status = event.detail.xhr.status;
  prependLogEntry(
    document.getElementById('submission-log'),
    `Request failed (HTTP ${status}) — data may already be committed; check the server log.`,
  );
});
