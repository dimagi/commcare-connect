# OCS Conversations in Connect — Technical Design

**Ticket:** CCCT-2467
**Date:** 2026-06-19
**Status:** Draft (open decisions — see Open Decisions)

## Summary

Connect lets Network Managers (NMs) assign tasks to frontline workers (FLWs). Today a
"task" is effectively a CommCare re-learn activity tracked via xform submissions. This
feature adds a second kind of task: an **OCS (Open Chat Studio) conversation**. When a user
assigns an OCS task type to an FLW, Connect triggers a chatbot conversation in OCS for that
FLW. When the FLW finishes the conversation, OCS calls back to Connect to mark the task
complete.

Concretely this requires:

- **OAuth2-based** authentication to OCS, via django-allauth, using the **logged-in user's**
  connected OCS account (no static API key, no background service credential).
- A `type` discriminator on `TaskType` (Re-Learn vs OCS) plus an OCS chatbot id.
- An OCS API client (list chatbots, trigger bot) that authenticates with the acting user's
  OAuth token.
- A **synchronous, user-facing** trigger of the OCS bot during task assignment.
- An inbound callback endpoint OCS uses to mark a task completed.
- A completion push notification for all task types.
- UI to configure OCS task types and to connect a user's OCS account.

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
  - Two production callers: `create_task` (`views.py:3525`, single assign) and
    `audit_report_task_action` (`audit/views.py:189`, **bulk** — loops over several task types
    for one worker inside a single `transaction.atomic()`).
  - Completion today: `process_task_modules` (`form_receiver/processor.py:148`) matches an
    incoming form's `<task @id>` to an `AssignedTask` by `task_type.slug` + user, sets
    `status=COMPLETED`, `completed_at`, `xform_id`. Type-agnostic.
- **`AddTaskTypeForm`** / **`EditTaskTypeForm`** (`forms.py:2006`, `forms.py:2081`): the PM
  config forms. `AddTaskTypeForm` populates a `task_unit_id` dropdown from the CommCare
  deliver app (`get_task_units_for_app`), degrading gracefully to a disabled dropdown on
  fetch failure. `save()` sets `app = opportunity.deliver_app`, `slug = task_unit_id`.
- **`TaskTypesConfig`** / **`EditTaskType`** views (`views.py:1252`, `views.py:1294`): PM-only
  config page at `/a/<org_slug>/opportunity/<opp_id>/task_types/`, lists task types via
  `TaskTable` filtered by `app=opportunity.deliver_app`.
- **`AssignedTaskSerializer`** (`api/serializers/mobile.py:299`): exposes `assigned_task_id`,
  `task_name`, `task_description`, `status`, `due_date`, `date_created`.
- **External API pattern** (`commcarehq/api.py`): inline `httpx.Client(base_url=..., headers=...)`
  context manager, `response.raise_for_status()`, failures wrapped in a custom exception.
- **django-allauth** is installed and configured (`config/settings/base.py:85`): `allauth`,
  `allauth.account`, `allauth.socialaccount`, a custom `SocialAccountAdapter`, and
  **`SOCIALACCOUNT_STORE_TOKENS = True`** (so `SocialToken` access/refresh tokens are persisted).
- `ATOMIC_REQUESTS = True` — every request is already a DB transaction.

## Design Decisions (resolved during brainstorming / grilling)

| Decision | Choice |
|---|---|
| **Outbound auth (Connect→OCS)** | **OAuth2 via django-allauth**, using the logged-in user's connected OCS account. No static `X-api-key`, no service credential. |
| Why not `X-api-key` | Reversed in favor of per-user scoped, revocable OAuth tokens; avoids a pasted shared secret. |
| Why user-facing (not background) | A background trigger **is** technically possible by storing and refreshing the user's token (OCS issues refresh tokens — confirmed by the OCS team in PR review). We still chose a **synchronous, user-facing** trigger: it avoids persisting/refreshing long-lived tokens and the refresh-token rotation races that come with background workers, and it gives the assigning user immediate success/failure feedback. |
| Trigger flow | **Synchronous and user-facing** within the assignment request, using the acting user's OCS token. No Celery task, no retry. |
| On trigger failure | The per-assign transaction **rolls back** — no `AssignedTask` persisted; the user sees the error. A failed trigger creates nothing on OCS, so nothing dangles. |
| Bulk assignment transactions | **Refactor the audit bulk path to one transaction per `assign()`** so a failure (or failed trigger) rolls back only that one task, not the batch. |
| Chatbot access mismatch | If the acting user's OCS token can't access the configured chatbot/team, **let the OCS call fail and surface the error** to the user. No pre-validation of team membership. |
| Task identifier sent to OCS / accepted in callback | **UUID** (`assigned_task_id`), not the integer PK. |
| OCS client placement | Single small **util module** `commcare_connect/opportunity/ocs.py`. |
| Completion notification | Fire an FCM push (`"key": "task_completion"`) on completion of **any** task type, via a shared `AssignedTask.mark_completed(...)`. No backfill. |

## OAuth2 Integration (django-allauth)

OCS supports OAuth2 **Authorization Code + PKCE** and **Refresh Token** grants (confirmed from
OCS docs). Endpoints: authorize `https://www.openchatstudio.com/o/authorize/`, token
`/o/token/`, userinfo `/o/userinfo/`. Access tokens last ~1 hour; refresh tokens rotate.
Scopes needed: `chatbots:read` (list), `chatbots:interact` (trigger) and `sessions:read` (session status). 
No OIDC discovery endpoint.

- Add a **custom allauth provider** for OCS (a `Provider` + `OAuth2Adapter` pointing at the
  three endpoints above, requesting the two scopes). allauth has no built-in OCS provider.
- A `SocialApp` holds the OCS `client_id` / `client_secret` (registered once with OCS; redirect
  URI must exactly match Connect's allauth callback).
- Because the trigger is **user-facing and synchronous**, calls happen inside a live request
  with the acting user's session — so we use that user's stored `SocialToken`. If the access
  token is expired, refresh it via the refresh-token grant at call time (allauth stores the
  token; the OCS client performs the refresh and persists the rotated refresh token). No
  background worker ever needs a token, which sidesteps refresh-races and the lack of a
  machine-to-machine grant.

### Consequences (accepted)

1. **Any user who configures or assigns OCS tasks must have connected their OCS account.** If
   not connected, the action is blocked with a "Connect your OCS account" prompt.
2. **No team pre-check.** The chatbot list is fetched with the configuring user's token; the
   trigger runs with the assigning user's token. If those users are in different OCS teams and
   the assigner can't access the configured chatbot, the `trigger_bot` call fails and we show
   the OCS error. We do not attempt to guarantee team consistency.
3. **Rare ambiguous-failure dangling.** If OCS creates a conversation but the HTTP call errors/
   times out before responding, we roll back the `AssignedTask` while the conversation exists on
   OCS. The completion-reconciliation job can't recover this case (no task and no `ocs_session_id`
   were persisted to reconcile against), so it remains unrecoverable but rare; accepted.

## Data Model Changes

All in `commcare_connect/opportunity/models.py` unless noted. **No `OCSApiKey` model** — OAuth
replaces it; credentials live in allauth's `SocialApp` / `SocialToken`.

### `TaskType` additions

```python
class TaskTypeChoices(models.TextChoices):
    RELEARN = "relearn", gettext("Re-Learn")
    OCS = "ocs", gettext("OCS")

# new fields on TaskType
type = models.CharField(max_length=20, choices=TaskTypeChoices.choices, default=TaskTypeChoices.RELEARN)
ocs_chatbot_id = models.CharField(max_length=255, null=True, blank=True)  # OCS "experiment" id (UUID)
```

- Default `relearn` so existing rows are unaffected (no data migration needed).
- For OCS task types: `app` stays `opportunity.deliver_app` (keeps them visible in the
  existing `TaskTable` filter), `slug` is generated from `ocs_chatbot_id`, `case_property`
  stays null. The `(app_id, slug)` unique constraint then prevents the same chatbot being
  added twice to one app.
- `ocs_chatbot_id` is required when `type == ocs` (enforced in the form's `clean()`).

### `AssignedTask` additions

```python
connect_channel_id = models.CharField(max_length=255, null=True, blank=True)  # OCS `channel`
ocs_session_id = models.CharField(max_length=255, null=True, blank=True)       # OCS `session_id`
```

- Both are taken from the OCS `trigger_bot` response and written synchronously at assignment
  time. We store **both** (per PR review): `connect_channel_id` is the messaging channel, and
  `ocs_session_id` lets us cross-correlate an assigned task to its OCS session — needed for the
  completion-reconciliation fallback (below) and any future session lookups.
- `completed_at` and `status` are reused unchanged — for OCS tasks they are set by the
  callback rather than by xform processing.

### Serializer change

`AssignedTaskSerializer` (`api/serializers/mobile.py`) adds three fields (existing six unchanged):

| Field | Source | Type |
|---|---|---|
| `task_type` | `task_type.type` | enum string (`relearn`/`ocs`) |
| `slug` | `task_type.slug` | string |
| `connect_channel_id` | `AssignedTask.connect_channel_id` | string / null |

- `ocs_chatbot_id` is intentionally **not** exposed (the mobile app doesn't need the experiment id).
- Naming note: the new `task_type` field returns the *discriminator*, while `task_name`/
  `task_description` already source from the `task_type` FK. Consider naming it `type` to avoid
  ambiguity (open nit).

## OCS API Client

New util module `commcare_connect/opportunity/ocs.py`. Mirrors the `commcarehq/api.py` style:
inline `httpx.Client`, `raise_for_status()`, failures wrapped in a new `OCSAPIException`. All
requests authenticate with the **acting user's OAuth access token**
(`Authorization: Bearer <access_token>`), refreshing via the refresh-token grant if expired,
and use the `OCS_BASE_URL` setting (default `https://www.openchatstudio.com`).

Endpoints/schemas confirmed against the OCS OpenAPI schema (`/api/schema/`):

```python
def list_chatbots(user) -> list[Chatbot]:
    """GET {base_url}/api/v2/chatbots/ — PAGINATED; follow pages and flatten.
    Each Chatbot has id, name, version_number, versions. Returns (id, name) for the dropdown.
    Auth: user's OCS token (scope chatbots:read)."""

def trigger_bot(user, *, identifier: str, experiment: str, participant_data: dict) -> TriggerBotResult:
    """POST {base_url}/api/trigger_bot — response: {session_id, url, team, channel}.
    Auth: user's OCS token (scope chatbots:interact)."""
```

Notes from the OCS schema:
- List endpoint is `/api/v2/chatbots/` and is **paginated** — `list_chatbots` must page through results.
- `experiment` is a UUID (the chatbot id).
- `trigger_bot` response fields: `session_id`, `url`, `team`, `channel`. We store **both**
  `channel` (→ `connect_channel_id`) and `session_id` (→ `ocs_session_id`).

`trigger_bot` request body:

```json
{
  "identifier": "<flw username>",
  "experiment": "<task_type.ocs_chatbot_id>",
  "platform": "commcare_connect",
  "participant_data": { "connectTaskId": "<assigned_task.assigned_task_id (UUID)>" }
  "start_new_session": true
}
```

## Trigger Flow (Assignment)

Synchronous and user-facing, inside `AssignedTask.assign(...)` (which already opens its own
`transaction.atomic()`).

1. Create the `AssignedTask` row.
2. **If `task_type.type == ocs`:**
   - Call `ocs.trigger_bot(acting_user, ...)` **synchronously** with the payload above, using
     the acting user's OCS token.
   - On success: set `assigned_task.connect_channel_id` and `assigned_task.ocs_session_id`
     from the response and save.
   - On failure (`OCSAPIException` / httpx errors / token-not-connected / chatbot-not-accessible):
     let it propagate so the per-assign atomic block rolls back — **no row persisted**, and the
     user sees the error. A failed trigger created nothing on OCS.
3. **If `task_type.type == relearn`:** unchanged — optional CommCare `case_property` update.
4. `send_task_assignment_notification` FCM push still fires on commit (trigger has already
   succeeded by then for OCS, so the FLW is never told about a task with no conversation).

### Bulk assignment refactor

`audit_report_task_action` (`audit/views.py:189`) currently wraps the whole loop in one
`transaction.atomic()`. Refactor so each `assign()` is its own unit and failures are isolated:

```python
assigned, failed = [], []
for task_type in task_types:
    try:
        AssignedTask.assign(task_type=task_type, opportunity_access=entry.opportunity_access,
                            due_date=due_date, assigned_by=request.user)
        assigned.append(task_type)
    except (TaskAlreadyAssignedError, OCSAPIException, CommCareHQAPIException):
        failed.append(task_type)
```

- `assign()`'s internal `atomic()` is a savepoint under `ATOMIC_REQUESTS`; catching the
  exception *outside* it rolls back only that task and keeps the request transaction valid, so
  successful assigns still commit. The failed task's `on_commit` (registered in the rolled-back
  savepoint) is discarded — no push, no trigger side effects.
- **Partial-success behavior (open nit):** decide whether to mark the entry
  `reviewed`/`TASKS_ASSIGNED` when ≥1 task assigned, and show an "assigned N, skipped M" message.
  Recommended: yes.

## Callback API (Completion)

New DRF endpoint (e.g. `POST /api/task_completed/`) that OCS calls when an FLW finishes a
conversation. (We keep completion as a **direct OCS→Connect callback** — not routed through
CommCare HQ — since the OCS integration is Connect-to-OCS only.)

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
  (404/403) so a valid task id alone cannot complete a task for the wrong user.
- Call `AssignedTask.mark_completed(completed_at=payload value or now())`.
- **Idempotent:** if already completed, return success without changing `completed_at`.
- Validation via a DRF serializer; completion logic in a standalone function (testable without
  HTTP, per repo convention).

**Authentication — RESOLVED (OAuth2, Client Credentials grant).** OCS authenticates to Connect
the same way **CommCare HQ authenticates to the Form Processor** today: OAuth2 with the
**Client Credentials** grant. OCS is registered as an OAuth2 client of Connect (Connect is
already an OAuth2 provider via `oauth2_provider`), and the callback view reuses the
`FormReceiver` pattern — `OAuth2Authentication` + a read/write scope check. This keeps the
inbound auth consistent with the existing HQ→Connect integration and requires no bespoke
key-management. (Confirmed with the OCS team in PR review; OCS will add the matching OAuth
support — tracked in [open-chat-studio#2179](https://github.com/dimagi/open-chat-studio/issues/2179).)

**How OCS decides a conversation is "done" (OCS-side, informational).** The completion callback
is fired from OCS via one of their pipeline mechanisms — custom actions (a bot "tool" the LLM
invokes), Python nodes (external API call), a router node evaluating conversation state, or a
timeout trigger. Choosing/among these is the OCS team's configuration concern, outside Connect's
scope; Connect only needs to receive the resulting callback.

**Completed-task behavior (confirmed in review).** Connect does not clean up completed assigned
tasks today, and the API continues to return them to mobile. OCS channels also remain alive
after completion (no archival mechanism currently). No change to this behavior here.

### Completion reconciliation (fallback for missed callbacks)

The callback is the primary completion signal, but it can be lost (OCS fails to send it, it
doesn't reach us, or Connect errors while consuming it). To get definitive closure on in-flight
OCS tasks, add a **periodic reconciliation** (Celery beat, e.g. daily):

- Find OCS `AssignedTask`s still `assigned` that have an `ocs_session_id`.
- For each, fetch the OCS session status via the session-retrieve endpoint
  (`GET /api/v1/sessions/{id}/` — see OCS docs) using the assigning user's OAuth token.
- If OCS reports the session complete, call `mark_completed(...)` (idempotent with the callback).

This is the one place a background OCS call is acceptable — it's read-only status polling, not
the trigger, so it doesn't reintroduce the dangling-conversation risk. (Suggested by the OCS
team in PR review.)

## Task Completion Notification

An FCM push fires whenever **any** `AssignedTask` (Re-Learn or OCS) transitions to completed,
mirroring the existing assignment push (`send_task_assignment_notification`, `tasks.py:748`).

- New Celery task `send_task_completion_notification(assigned_task_id)` with the same `Message`
  shape but `"key": "task_completion"` and a completion title/body.
- Add a single **`AssignedTask.mark_completed(*, completed_at, xform_id=None, duration=None,
  app_build_id=None, app_build_version=None)`** method that sets `status=COMPLETED` +
  `completed_at` (and the optional form fields), saves, and schedules
  `send_task_completion_notification.delay(pk)` via `transaction.on_commit`.
- Both completion sites call it: `process_task_modules` (`form_receiver/processor.py:148`, the
  Re-Learn/form path) and the OCS callback endpoint.
- Fires **only on the actual transition** to completed — idempotent re-calls do not re-notify.
- Explicit `on_commit` Celery task, no Django signals (codebase convention).
- **No backfill.** Only completions occurring after deploy notify (automatic — the push is wired
  into the completion code path; no migration notifies historical completions). Re-Learn
  completions start notifying going forward but are never backfilled.

## UI Changes

### Connect OCS account

- A user-facing action to connect their OCS account via the allauth OCS provider (standard
  allauth connect flow). Surfaced where a user configures or assigns OCS tasks.
- When a user attempts an OCS action without a connected account (or with an unrefreshable
  token), block it and prompt them to connect/reconnect OCS.

### Add Task Type form (`AddTaskTypeForm`)

- Add a `type` selector (Re-Learn / OCS). Use **Alpine.js** to alternate visible fields,
  consistent with the form's existing `@change` handler on `task_unit_id`.
  - **Re-Learn:** existing fields (`task_unit_id`, `case_property`) — unchanged behavior.
  - **OCS:** an `ocs_chatbot_id` dropdown; `task_unit_id`/`case_property` hidden.
- The OCS dropdown is populated **server-side** from `ocs.list_chatbots(request.user)` using the
  configuring user's OCS token, following the existing `task_unit_id` pattern: on fetch failure
  (including not-connected / token error), disable the dropdown and show an explanatory message.
- `clean()` requires `ocs_chatbot_id` when `type == ocs`. `save()` branches on `type`: OCS sets
  `ocs_chatbot_id`, generates `slug` from the chatbot id, leaves `case_property` null; Re-Learn
  unchanged.

### Info warning when OCS account not connected

- On the Task Types config page, when the user selects the **OCS** type but has not connected an
  OCS account, show an **info warning** prompting them to connect OCS first (and disable the
  chatbot dropdown / OCS submission).

### Task type table (`TaskTable`)

- Add a column/badge indicating **Re-Learn** vs **OCS**, using a predefined Tailwind style class
  (per repo style rules — no raw utility classes).

## Error Handling

- OCS client wraps all transport/HTTP/token errors in `OCSAPIException` (like
  `CommCareHQAPIException`).
- Chatbot fetch failures in the form degrade gracefully (disabled dropdown + message); never 500
  the config page.
- Trigger failure during assignment rolls back that assign's transaction and surfaces an error
  to the user (including OCS access-denied when the chatbot isn't reachable by the user's token).
- Callback rejects unknown task ids and username mismatches; idempotent on repeat calls.

## Testing

Framework: pytest + factory-boy; `pytest-httpx` for OCS HTTP mocking. Test functions over view
responses; prefer fixtures.

- **Models/factories:** OCS-type `TaskTypeFactory` variant, `connect_channel_id` on
  `AssignedTask`, a fixture for a user with a connected OCS `SocialToken`.
- **OCS client:** `list_chatbots` (pagination) and `trigger_bot` — success and failure
  (timeout / non-2xx → `OCSAPIException`); Bearer auth header; token refresh on expiry.
- **`AssignedTask.assign` (OCS):** success stores `connect_channel_id` **and `ocs_session_id`**;
  trigger failure rolls back so no `AssignedTask` exists; not-connected user blocked; Re-Learn
  path unchanged.
- **Completion reconciliation:** the periodic job finds `assigned` OCS tasks with an
  `ocs_session_id`, polls OCS session status, and `mark_completed`s those OCS reports done;
  idempotent with the callback; ignores tasks without a session id.
- **Bulk audit path:** per-assign isolation — one failing task doesn't roll back the others;
  partial-success messaging.
- **Callback logic:** marks completed; username mismatch rejected; optional `completed_at`;
  idempotent.
- **Serializer:** new fields (`task_type`, `connect_channel_id`) present.
- **Completion notification:** `mark_completed` fires `send_task_completion_notification` once on
  transition for both Re-Learn (form) and OCS (callback) paths; no re-notify on idempotent
  re-completion.
- **Form:** type branching; OCS requires `ocs_chatbot_id`; chatbot-load failure degradation;
  not-connected info-warning path.

## Cross-Team Dependency

- **Callback authentication (OCS→Connect)** — *resolved in direction, pending OCS work.* OCS will
  authenticate to Connect's callback via OAuth2 (Client Credentials grant), the same mechanism HQ
  uses for the Form Processor (see Callback API). This depends on OCS adding OAuth support,
  tracked in [open-chat-studio#2179](https://github.com/dimagi/open-chat-studio/issues/2179).
  The callback endpoint can be built against the existing `FormReceiver` OAuth2 pattern once that
  lands.

## Open Decisions

1. **Partial-success UX** in the audit bulk path — mark reviewed if ≥1 assigned + "skipped M"
   message (recommended yes).

## Out of Scope / YAGNI

- A background/async **trigger** or trigger retries (the trigger is user-facing and synchronous).
  Note: read-only completion *reconciliation* polling **is** in scope (see Completion
  reconciliation) — that's status polling, not triggering.
- Guaranteeing OCS team consistency across users (we let mismatched access fail loudly).
