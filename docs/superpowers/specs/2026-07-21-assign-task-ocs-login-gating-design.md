# Design: OCS-connect gating on the assign-tasks page

**Date:** 2026-07-21
**Branch:** `cs/CCCT-2553-ui-for-task-types`
**Status:** Approved design, pending implementation plan

## Problem

On the "assign tasks to users" page (`AssignedTaskListView` + `create_task` +
`tasks/new_task_modal.html`), a user can pick any active `TaskType` from a
dropdown, including OCS-backed task types (`mode == OCS`, `ocs_chatbot_id` set).
Assigning an OCS task is meant to use the assigning user's Open Chat Studio (OCS)
credentials, so the assigning user must be logged in / connected to OCS.

Today the assign page has **no** OCS-connection awareness. We need to ensure the
request user is connected to a live OCS account when they select an OCS task, and
give them the option to connect if they are not.

The "create task types" page already solves the analogous problem and is the
template for this work.

## Inspiration: how the create-task-types page works

- `AddTaskTypeForm` (`opportunity/forms.py`) has a `mode` radio (`relearn`/`ocs`)
  bound to Alpine `taskMode`. When `ocs` is chosen, an `#ocs-task-section` div
  (`x-show="taskMode === 'ocs'"`) lazy-loads via htmx (`hx-trigger="intersect
  once"`) from `TaskTypeOcsSection` (URL name `task_type_ocs_section`).
- `get_ocs_task_section_context()` (`opportunity/views.py:1360`) checks
  `user_has_connected_ocs(request.user)`; if connected it calls `list_chatbots()`
  which forces a **token refresh** via `_get_valid_token` /
  `refresh_access_token`. A `TokenRefreshError` (dead/expired token) falls back to
  the connect prompt. This is a *live* check, not a mere existence check.
- `_ocs_task_section.html` renders either `ocs/_connect_prompt.html` (an amber
  banner with a "Connect OCS account" button) or the chatbot dropdown.
- `ocs/_connect_prompt.html` submits a bare sibling `<form id="ocs-connect-form">`
  to `{% provider_login_url 'ocs' process='connect' next=next_url %}`. The
  `next_url` is derived from `HX-Current-URL` via `_hx_current_path` so the OAuth
  flow returns to the originating page. The prompt copy already reads
  "...to configure **or assign** OCS tasks" — written to be shared here.

## Approach

Mirror the create page's **htmx + token-refresh** live check, adapted to the
assign page's model (OCS-ness is a property of the selected `TaskType`, chosen
from a dropdown, rather than a live radio; no chatbot dropdown is needed because
the task type already carries `ocs_chatbot_id`).

### 1. Server — live connection check endpoint

Add a view `CreateTaskOcsSection` at URL `assigned_tasks/ocs_section/`
(name `create_task_ocs_section`), analogous to `TaskTypeOcsSection`. It performs
the same live check the create page uses:

```python
def get_create_task_ocs_context(request):
    if not user_has_connected_ocs(request.user):
        return {"ocs_connected": False, "ocs_next_url": _hx_current_path(request)}
    try:
        _get_valid_token(request.user)          # forces refresh; catches dead tokens
    except TokenRefreshError:
        return {"ocs_connected": False, "ocs_next_url": _hx_current_path(request)}
    return {"ocs_connected": True}
```

Renders a small fragment (`tasks/_ocs_connect_section.html`):

- **Connected & live** → an element that sets Alpine `ocsConnected = true` (no
  visible UI).
- **Not connected / dead token** → include the shared `ocs/_connect_prompt.html`
  with `next_url=ocs_next_url`, wrapped so it sets Alpine `ocsConnected = false`.

No chatbot dropdown is rendered — unlike the create page, the assign flow does not
need to choose a chatbot.

`_hx_current_path`, `user_has_connected_ocs`, `_get_valid_token`,
`TokenRefreshError`, and `ocs/_connect_prompt.html` are reused as-is.

### 2. Form / template wiring (Alpine + htmx)

- `CreateTaskForm.__init__` injects an `#ocs-task-section` div after the `task`
  field, containing the `opportunity/_ocs_loading_spinner.html` placeholder and an
  `hx-get` to `create_task_ocs_section`.
- The fetch fires **lazily** — only when an OCS task is first selected (Alpine
  dispatches an event from the `task` select's `@change`; htmx triggers on that
  event). Pure relearn assignments therefore make no OCS API call. While the check
  is in flight, the spinner shows and Save is disabled.
- Alpine state on the modal:
  - `selectedTaskIsOcs` — computed from the chosen option against a JS map of OCS
    `TaskType` ids passed from the view context.
  - `ocsConnected` — set by the htmx fragment once the check completes.
  - Save button: `:disabled="selectedTaskIsOcs && !ocsConnected"`.
- The connect banner is shown only when `selectedTaskIsOcs` is true (an OCS task is
  selected) and the user is not connected.
- `tasks/new_task_modal.html` gains a sibling
  `<form id="ocs-connect-form" method="post">{% csrf_token %}</form>` (as
  `new_task_type_modal.html` has) so the connect button's `form=` + `formaction`
  submit reaches allauth. `next` resolves back to the assigned-task list via the
  existing `HX-Current-URL` / `_hx_current_path` handling.

### 3. Server-side guard (defense in depth)

`create_task` / `CreateTaskForm.clean` rejects assigning an OCS task when
`not user_has_connected_ocs(request.user)`, returning a form error. This ensures
the assignment cannot proceed even if the disabled Save button is bypassed.

## Scope and non-goals

- `AssignedTask.assign` does **not** yet call `trigger_bot` (bot triggering is not
  wired anywhere in the codebase). This change is purely the login-gating UI +
  guard, matching the current requirement. Wiring the actual bot trigger is out of
  scope.
- The submit-time guard is an **existence** check (`user_has_connected_ocs`); the
  live token-refresh robustness lives in the htmx UX check. This matches how the
  create page splits responsibility — its `clean` does not re-verify the connection
  either. A dead-but-existing token that bypasses the disabled button would let the
  assignment through; acceptable because triggering is not yet wired.

## Testing

Following repo conventions (test functions, not view responses):

- Extract `get_create_task_ocs_context(request)` and test it directly for the three
  states: not connected, connected-but-refresh-fails, connected-and-live (mock
  `user_has_connected_ocs` / `_get_valid_token`).
- Test the `CreateTaskForm` / `create_task` server-side guard: assigning an OCS
  task type as an unconnected user yields a validation error; a non-OCS task type
  is unaffected; a connected user succeeds.
- Reuse existing fixtures (`org_user_admin`, `opportunity`, task-type factories);
  add an OCS `TaskType` factory/fixture variant if none exists.

## Files touched (anticipated)

- `commcare_connect/opportunity/views.py` — `CreateTaskOcsSection`,
  `get_create_task_ocs_context`, guard in `create_task`.
- `commcare_connect/opportunity/urls.py` — `create_task_ocs_section` route.
- `commcare_connect/opportunity/forms.py` — `CreateTaskForm` OCS section injection
  + guard in `clean`; expose OCS task-id map.
- `commcare_connect/templates/tasks/new_task_modal.html` — sibling connect form,
  Alpine state, Save disable.
- `commcare_connect/templates/tasks/_ocs_connect_section.html` — new fragment.
- Tests under `commcare_connect/opportunity/tests/`.
