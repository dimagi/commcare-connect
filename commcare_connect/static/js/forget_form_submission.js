// Loaded only on pages answering a POST. Under Cache-Control: no-store, Back or reload to this page would
// re-send the POST behind a "resubmit form?" prompt. Replacing the history entry makes it a plain GET of the
// same URL (the form's action), so views rendering full pages on POST should also answer GET there.
history.replaceState(history.state, '', location.href);
