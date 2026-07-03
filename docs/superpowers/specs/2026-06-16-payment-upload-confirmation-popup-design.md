# Payment Upload Confirmation Popup — Design

**Date:** 2026-06-16
**Status:** Implemented

> This document reflects the design as built. Where the wording or a decision
> changed during implementation, the final behavior is described here.

## Problem

When a user uploads an Excel/CSV sheet to update payments on Connect, the result was
surfaced through the shared export progress bar (`upload_progress_bar.html`): a thin
inline bar that, on completion, offered an "All done! View status." link opening a modal.

We want the payment upload to show the same kind of confirmation popup the **work area
upload** uses — a modal that polls for the task result and then shows a clear success or
failure message.

- **Success text:** `You have successfully processed N payment!` / `… N payments!`
  (singular/plural based on the count).
- **Failure text:** `There was an issue with your upload. Please try again.`
- **While the task runs:** `Processing your payments… Please keep this page open to see the result.`
  (no time estimate, since a large import may take a while; the file is already uploaded
  by this point, so the spinner describes processing, not uploading).

## Decisions

- **N = number of payment records created** — the count of `Payment` rows the import
  creates (`PaymentImportStatus.payments_created` == `len(payment_ids)`). A file with
  multiple rows for one worker counts each payment, so the noun "payment(s)" is literally
  accurate. (This started as "users paid" but was changed during implementation so the
  number matches the word "payments".)
- **Keep existing detail alongside the popup** — on success, still list any usernames that
  were not found; on failure, still show the per-row error detail beneath the generic
  failure headline.
- **Failure detail is human-readable** — each bad row is shown as `"<reason>: <cell, cell, …>"`
  (e.g. `amount must be a number: alice, NOT_A_NUMBER, 2026-06-09, bank, op_b`), rendered as
  a bulleted list. Not a raw Python tuple.
- **Replace the progress bar with a work-area-style modal for payment import only** — the
  shared `export_status` / `upload_progress_bar.html` flow is left untouched because it is
  used by many genuine exports.

## Current State (pre-change reference)

- View: `payment_import` (`opportunity/views.py`) saves the file, calls
  `bulk_update_payments_task.delay(...)`, and redirected to `worker_payments` with
  `?export_task_id={id}`.
- Task: `bulk_update_payments_task` (`opportunity/tasks.py`) funneled success and caught
  `ImportException` both through `set_task_progress(..., is_complete=True)` as one
  `<br>`-joined message — so Celery state was `SUCCESS` in both cases and they were not
  distinguishable from the task result.
- `bulk_update_payments` (`opportunity/visit_import.py`) returned
  `PaymentImportStatus(seen_users, missing_users)`; raised `ImportException(message, rows)`
  on validation errors, where `rows` was a `<br>`-joined string of `str(tuple)`.
- Page shell `opportunity_worker.html` shows a polling spinner whenever `export_task_id`
  is present, polling `export_status` → `render_export_status` → `upload_progress_bar.html`.
- `bulk_update_payments_task` is used **only** by `payment_import`, so its result format
  can change freely.

The work-area pattern we mirrored:
- `import_work_areas_task` returns `{"created": N}` or `{"errors": {...}}`.
- `import_status` (`microplanning/views.py`) reads `AsyncResult`, sets `result_ready` /
  `result_data`, renders `import_work_area_modal.html`.
- The modal shows a spinner while polling (`hx-trigger="every 2s"`), then the
  success/failure content.

## Design

### 1. Task returns structured data

`bulk_update_payments_task` **returns** a structured dict instead of setting a final
progress message (mirrors `import_work_areas_task`). The cache lock stays.

- Success → `{"success": True, "payments_processed": status.payments_created, "missing_users_message": status.get_missing_message() or None}`
- Caught `ImportException` → `{"success": False, "error_detail": e.rows or [e.message]}`
  (`error_detail` is always a **list of strings** — row errors come from `e.rows`; a
  structural error with no rows is wrapped as a one-item list with its message).
- Any uncaught exception → task fails (state `FAILURE`); the status view builds the generic
  failure `result_data`.

Supporting changes in `bulk_update_payments` (`visit_import.py`):
- `PaymentImportStatus` gains a `payments_created: int` field, set to `len(payment_ids)`.
- Invalid rows are raised as `ImportException(message, error_details)` where `error_details`
  is a list of readable strings (`"<reason>: <raw cells joined by ', '>"`). Cells are no
  longer pre-`escape()`d — the template auto-escapes instead.

The intermediate "in progress" state no longer needs `set_task_progress`, because the
modal renders its own spinner while `AsyncResult` is not ready.

### 2. Status endpoint + modal

Mirror `import_status` + `import_work_area_modal.html`.

- New view `payment_import_status(request, org_slug, opp_id)` in `opportunity/views.py`:
  reads `task_id` from query params, checks `AsyncResult.ready()` / `.successful()`, builds
  `result_ready` and `result_data` (passing `opportunity=request.opportunity`), and renders
  the partial. On an unsuccessful task it builds `{"success": False, "error_detail": None}`.
- New URL → name `opportunity:payment_import_status`.
- New template `opportunity/payment_import_status_modal.html` (root id `payment-import-parent`,
  Alpine `x-data="{ open: true }"`):
  - **polling** → spinner with "Processing your payments… Please keep this page open to see the result."
  - **success** → green check + `You have successfully processed N payment(s)!`
    (`{% blocktranslate count %}` / `{% plural %}`), and the missing-users message below it
    when present (rendered with `|safe`, as it is built with `format_html`).
  - **failure** → red icon + `There was an issue with your upload. Please try again.`, and a
    bulleted `<ul>` of the readable error strings when present (auto-escaped, no `|safe`).
  - A Close button (`@click="open = false"`), consistent with the work-area modal.

### 3. Wire-up

- `payment_import` redirects with `?payment_import_task_id={id}` instead of
  `?export_task_id={id}`, so the upload no longer triggers the shared export progress bar.
- The base worker list view adds `payment_import_task_id` to its context (alongside the
  existing `export_task_id`).
- `opportunity_worker.html` shell: when `payment_import_task_id` is present, render the
  payment-import modal, which polls `payment_import_status` every 2s and swaps itself with
  the result. `upload_progress_bar.html` and `export_status` are not modified.

### 4. Testing

- **Task** (`opportunity/tests/test_tasks.py`): `bulk_update_payments_task` returns the
  expected dict for (a) success (count = payment records, distinct from users), (b) success
  with missing users, (c) caught `ImportException` with a readable error list, (d) a
  structural `ImportException` with no rows (message wrapped into a one-item list), and
  (e) an unexpected error that propagates (task FAILURE, file still deleted).
- **Template** (`opportunity/tests/test_views.py`, via `render_to_string`): success text +
  count (plural), singular form ("1 payment", not "1 payments"), success-with-missing-users,
  and failure text + readable error list (asserting the old raw-tuple form is gone).
- **View / wiring** (`opportunity/tests/test_views.py`): the polling endpoint renders the
  spinner; `payment_import` redirects with `payment_import_task_id` (and not
  `export_task_id`); the worker payments shell renders the modal when the param is present.
- **Import logic** (`opportunity/tests/test_visit_import.py`): `bulk_update_payments`
  duplicate-check asserts the error rows are readable strings, not tuples.

## Out of Scope

- The shared `export_status` / `upload_progress_bar.html` flow and all other exports.
- Payment validation rules in `bulk_update_payments` (only the error *formatting* and an
  added `payments_created` count were changed; the validation logic is unchanged).
- Any change to the import upload form/modal (`import_modal.html`).

## Notes / known limitations

- **Local dev must run a real Celery worker (non-eager) to see the popup resolve.** Default
  `config/settings/local.py` sets `CELERY_TASK_ALWAYS_EAGER = True` and does not set
  `CELERY_TASK_STORE_EAGER_RESULT`, so an eager task's result is never written to the result
  backend and the modal's `AsyncResult(...).ready()` polling would spin forever. Production
  runs non-eager with a worker (verified working). The existing work-area upload shares this
  pattern, so this is not specific to this feature.
