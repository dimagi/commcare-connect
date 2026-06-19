# OCS Conversations in Connect — Technical Design

**Ticket:** CCCT-2467
**Date:** 2026-06-19
**Status:** Draft (one open decision — see Open Decisions)

## Summary

Connect lets Network Managers (NMs) assign tasks to frontline workers (FLWs). Today a
"task" is effectively a CommCare re-learn activity tracked via xform submissions. This
feature adds a second kind of task: an **OCS (Open Chat Studio) conversation**. When an NM
assigns an OCS task type to an FLW, Connect triggers a chatbot conversation in OCS for that
user. When the FLW finishes the conversation, OCS calls back to Connect to mark the task
complete.

Concretely this requires:

- Per-opportunity storage of an OCS API key.
- A `type` discriminator on `TaskType` (Re-Learn vs OCS) plus an OCS chatbot id.
- An OCS API client (list chatbots, trigger bot).
- A synchronous trigger of the OCS bot during task assignment.
- An inbound callback endpoint OCS uses to mark a task completed.
- UI to configure OCS task types and the OCS API key.

## Background / Current State

Relevant existing code (all in `commcare_connect/opportunity/` unless noted):

- **`TaskType`** (`models.py:263`): `task_type_id` (UUID), non-null `app` FK to `CommCareApp`,
  nullable `opportunity` FK, `slug`, `unit_name`, `name`, `description`, `case_property`,
  `archived`, `is_active`, `duration`. Unique constraint on `(app_id, slug)`.
- **`AssignedTask`** (`models.py:429`): `assigned_task_id` (UUID), `task_type` FK,
  `opportunity_access` FK, `completed_at`, `duration`, `xform_id`, `status`
  (`AssignedTaskStatus` = `assigned` / `completed`), `due_date`, `assigned_by`.
  - `AssignedTask.assign(...)` (`models.py:463`) creates the row inside a
    `transaction.atomic()` block, optionally writes a CommCare HQ case property, and fires
    `send_task_assignment_notification.delay(...)` on commit (an FCM push).
- **`AddTaskTypeForm`** / **`EditTaskTypeForm`** (`forms.py:2006`, `forms.py:2081`): the PM
  config forms. `AddTaskTypeForm` populates a `task_unit_id` dropdown from the CommCare
  deliver app (`get_task_units_for_app`), degrading gracefully to a disabled dropdown on
  fetch failure. `save()` sets `app = opportunity.deliver_app`, `slug = task_unit_id`.
- **`TaskTypesConfig`** / **`EditTaskType`** views (`views.py:1252`, `views.py:1294`): PM-only
  config page at `/a/<org_slug>/opportunity/<opp_id>/task_types/`, lists task types via
  `TaskTable` filtered by `app=opportunity.deliver_app`.
- **`AssignedTaskSerializer`** (`api/serializers/mobile.py:299`): exposes `assigned_task_id`,
  `task_name`, `task_description`, `status`, `due_date`, `date_created`.
- **`HQApiKey`** (`models.py`) + **`HQApiKeyCreateForm`** (`forms.py:72`): existing precedent
  for storing a third-party credential — plaintext `api_key` CharField in the DB, configured
  via a small `ModelForm`.
- **External API pattern** (`commcarehq/api.py`): inline `httpx.Client(base_url=..., headers=...)`
  context manager, `response.raise_for_status()`, failures wrapped in a custom exception.
- `ATOMIC_REQUESTS = True` — every request is already a DB transaction.

## Design Decisions (resolved during brainstorming)

| Decision | Choice |
|---|---|
| OCS API key scope | **Per Opportunity** (one key per opportunity) |
| Trigger flow | **Synchronous** within the assignment request |
| On trigger failure | **Roll back** — no `AssignedTask` is persisted; NM sees an error |
| Task identifier sent to OCS / accepted in callback | **UUID** (`assigned_task_id`), not the integer PK |
| OCS client placement | Single small **util module** `commcare_connect/opportunity/ocs.py` |
| OCS API key config UI | Small `ModelForm` on the Task Types config page, mirroring `HQApiKeyCreateForm` |
| **Outbound auth (Connect→OCS)** | **`X-api-key`** (not OAuth2). OCS supports both; static key chosen for these server-to-server calls. |
| **`OCSApiKey` scope** | **Per-user, referenced per-opportunity** — mirrors `HQApiKey` (user-owned key + `Opportunity.ocs_api_key` FK), not a one-to-one per opportunity. |

## Data Model Changes

All in `commcare_connect/opportunity/models.py`.

### New model: `OCSApiKey`

Modeled **exactly** on `HQApiKey` (`models.py:46`): the key is **owned by a user**, and an
opportunity references which key it uses via a nullable FK ("per-user, scoped per-opportunity").

```python
class OCSApiKey(models.Model):
    api_key = models.CharField(max_length=255, unique=True)  # sent as the X-api-key header
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    date_created = models.DateTimeField(auto_now_add=True)
    # OCS has a single host (openchatstudio.com); base URL comes from a setting
    # (OCS_BASE_URL, default https://www.openchatstudio.com) so tests/staging can override.
    # No per-key server FK (unlike HQApiKey.hq_server) since there is only one OCS host.
```

And on `Opportunity`, mirroring `Opportunity.api_key` (`models.py:109`):

```python
ocs_api_key = models.ForeignKey(OCSApiKey, on_delete=models.DO_NOTHING, null=True, blank=True)
```

- Created via a `HQApiKeyCreateForm`-style form; the view sets `api_key.user = request.user`
  (mirrors `add_api_key`, `views.py:3280`).
- Plaintext storage, matching the `HQApiKey` precedent (not changing the security posture).

### `TaskType` additions

```python
class TaskTypeChoices(models.TextChoices):
    RELEARN = "relearn", gettext("Re-Learn")
    OCS = "ocs", gettext("OCS")

# new fields on TaskType
type = models.CharField(max_length=20, choices=TaskTypeChoices.choices, default=TaskTypeChoices.RELEARN)
ocs_chatbot_id = models.CharField(max_length=255, null=True, blank=True)  # OCS "experiment" id
```

- Default `relearn` so existing rows are unaffected (data migration not required; default
  covers backfill).
- For OCS task types: `app` stays `opportunity.deliver_app` (keeps them visible in the
  existing `TaskTable` filter), `slug` is generated from `ocs_chatbot_id`, `case_property`
  stays null. The `(app_id, slug)` unique constraint then naturally prevents the same chatbot
  being added twice to one app.
- `ocs_chatbot_id` is required when `type == ocs` (enforced in the form's `clean()`).

### `AssignedTask` additions

```python
connect_channel_id = models.CharField(max_length=255, null=True, blank=True)
```

- Stores the channel/session id returned by the OCS `trigger_bot` response, written at
  assignment time. (Exact response field name to be confirmed against the live API during
  implementation.)
- `completed_at` and `status` are reused unchanged — for OCS tasks they are set by the
  callback rather than by xform processing.

### Serializer change

`AssignedTaskSerializer` (`api/serializers/mobile.py`) adds:

- `task_type` — the `TaskType.type` value (`relearn` / `ocs`), so the mobile app can branch.
- `ocs_chatbot_id` — `source="task_type.ocs_chatbot_id"`.
- `connect_channel_id` — from the `AssignedTask`.

## OCS API Client

New util module `commcare_connect/opportunity/ocs.py`. Mirrors the `commcarehq/api.py` style:
inline `httpx.Client`, `raise_for_status()`, failures wrapped in a new `OCSAPIException`.
All requests send the `X-api-key: <ocs_api_key.api_key>` header and use the `OCS_BASE_URL`
setting (default `https://www.openchatstudio.com`) as the base.

Endpoints and schemas confirmed against the OCS OpenAPI schema (`/api/schema/`):

```python
def list_chatbots(ocs_api_key: OCSApiKey) -> list[Chatbot]:
    """GET {base_url}/api/v2/chatbots/ — PAGINATED; follow pages and flatten.
    Each Chatbot has id, name, version_number, versions. Returns (id, name) for the dropdown."""

def trigger_bot(ocs_api_key: OCSApiKey, *, identifier: str, experiment: str,
                participant_data: dict) -> TriggerBotResult:
    """POST {base_url}/api/trigger_bot — response: {session_id, url, team, channel}."""
```

Notes from the OCS schema:
- List endpoint is `/api/v2/chatbots/` (NOT `/api/chatbots/` as the requirements draft said) and
  is **paginated** — `list_chatbots` must page through results.
- `experiment` is a UUID (the chatbot id).
- `trigger_bot` response fields: `session_id`, `url`, `team`, `channel`. Our `connect_channel_id`
  will store **`channel`** (confirm vs `session_id` during implementation — whichever is the
  durable conversation reference Connect later needs).
- `trigger_bot` also accepts `start_new_session` (bool) and `prompt_text`/`message_text`. The
  bare payload (identifier + experiment + participant_data) may not actually *start* a
  conversation; confirm whether we need `start_new_session=true` and/or an initial message.

`trigger_bot` request body:

```json
{
  "identifier": "<flw username>",
  "experiment": "<task_type.ocs_chatbot_id>",
  "platform": "commcare_connect",
  "participant_data": { "taskId": "<assigned_task.assigned_task_id (UUID)>" }
}
```

## Trigger Flow (Assignment)

Extend `AssignedTask.assign(...)`. It already runs inside `transaction.atomic()`.

1. Create the `AssignedTask` row (as today).
2. **If `task_type.type == ocs`:**
   - Resolve `OCSApiKey` for `task_type.opportunity` (the opportunity is reachable via the
     opportunity_access / task_type).
   - Call `ocs.trigger_bot(...)` **synchronously** with the payload above.
   - On success: set `assigned_task.connect_channel_id` from the response and save.
   - On failure (`OCSAPIException`, httpx errors): let it propagate so the atomic block rolls
     back — **no row is persisted** and the NM sees an error in the request.
3. **If `task_type.type == relearn`:** unchanged — optional CommCare `case_property` update.
4. The existing `send_task_assignment_notification` FCM push still fires on commit for both
   types.

Because `ATOMIC_REQUESTS = True` and `assign()` adds its own atomic block, a failed OCS
trigger cleanly leaves no orphaned task.

## Callback API (Completion)

New DRF endpoint (e.g. `POST /api/ocs/task_completed/`) that OCS calls when an FLW finishes a
conversation.

Request body:

```json
{
  "task_id": "<assigned_task_id UUID>",
  "username": "<flw username>",
  "completed_at": "<optional ISO 8601 datetime>"
}
```

Behavior:

- Look up `AssignedTask` by `assigned_task_id == task_id`.
- Verify `assigned_task.opportunity_access.user.username == username`; reject mismatches
  (404/403) so a valid task id alone cannot be completed for the wrong user.
- Set `status = COMPLETED` and `completed_at = payload value or now()`.
- **Idempotent:** if already completed, return success without changing `completed_at`.
- Validation handled by a DRF serializer; the completion logic lives in a standalone
  function (testable without HTTP, per repo convention).

**Authentication — OPEN DECISION.** Recommended direction: a Connect-generated,
opportunity/user-scoped API key sent in a request header, validated server-side. See Open
Decisions.

## UI Changes

### Add Task Type form (`AddTaskTypeForm`)

- Add a `type` selector (Re-Learn / OCS). Use **Alpine.js** to alternate the visible fields,
  consistent with the form's existing `@change` handler on `task_unit_id`.
  - **Re-Learn:** existing fields (`task_unit_id`, `case_property`) — unchanged behavior.
  - **OCS:** an `ocs_chatbot_id` dropdown; `task_unit_id`/`case_property` hidden.
- The OCS dropdown is populated **server-side** from `ocs.list_chatbots()` using the
  opportunity's `OCSApiKey`, following the existing `task_unit_id` pattern: on fetch failure,
  disable the dropdown and show "Failed to load chatbots".
- `clean()` requires `ocs_chatbot_id` when `type == ocs`. `save()` branches on `type`: OCS
  sets `ocs_chatbot_id`, generates `slug` from the chatbot id, leaves `case_property` null;
  Re-Learn unchanged.

### Info warning when no OCSApiKey configured

- On the Task Types config page, when the PM selects the **OCS** type but no `OCSApiKey`
  exists for the opportunity, show an **info warning** prompting them to configure an OCS API
  key first (and disable the chatbot dropdown / submission for OCS).

### OCS API key config

- A small `ModelForm` for `OCSApiKey` (`api_key`, `base_url`), mirroring `HQApiKeyCreateForm`,
  surfaced on the Task Types config page so the PM can set/update the per-opportunity key.

### Task type table (`TaskTable`)

- Add a column/badge indicating **Re-Learn** vs **OCS**, using a predefined Tailwind style
  class (per repo style rules — no raw utility classes).

## Error Handling

- OCS client wraps all transport/HTTP errors in `OCSAPIException` (like `CommCareHQAPIException`).
- Chatbot fetch failures in the form degrade gracefully (disabled dropdown + message); they
  never 500 the config page.
- Trigger failure during assignment rolls back the transaction and surfaces an error message
  to the NM.
- Callback rejects unknown task ids and username mismatches; is idempotent on repeat calls.

## Testing

Framework: pytest + factory-boy; `pytest-httpx` for OCS HTTP mocking. Follow the repo rule of
testing functions over view responses; prefer fixtures.

- **Models/factories:** `OCSApiKeyFactory`, OCS-type `TaskTypeFactory` variant,
  `connect_channel_id` on `AssignedTask`.
- **OCS client:** `list_chatbots` and `trigger_bot` — success and failure (timeout / non-2xx
  → `OCSAPIException`), correct `X-api-key` header and body.
- **`AssignedTask.assign` (OCS):** success path stores `connect_channel_id`; trigger failure
  rolls back so no `AssignedTask` row exists; Re-Learn path unchanged.
- **Callback logic:** marks completed; username mismatch rejected; optional `completed_at`
  honored; idempotent on repeat.
- **Serializer:** new fields (`task_type`, `ocs_chatbot_id`, `connect_channel_id`) present.
- **Form:** type branching; OCS requires `ocs_chatbot_id`; chatbot-load failure degradation;
  missing-`OCSApiKey` info-warning path.

## Open Decisions

1. **Callback authentication.** Leaning toward a Connect-generated, opportunity/user-scoped
   API key in a request header (reliable, narrow security scope). Alternative is reusing the
   OAuth2 pattern from `FormReceiver`. To be finalized before implementing the callback
   endpoint.
2. **`trigger_bot` response field for `connect_channel_id`.** Exact field name to confirm
   against the live OCS API during implementation.

## Out of Scope / YAGNI

- Encrypting the stored OCS API key (matches existing `HQApiKey` plaintext precedent; not
  changing the security posture here).
- Async/retry for the trigger (explicitly chose synchronous-with-rollback).
- Multiple OCS keys per opportunity (one-to-one by design).
