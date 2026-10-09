# Optional Deliver Apps and OCS Interview Units

**Jira Ticket:** [CCCT-2861](https://dimagi.atlassian.net/browse/CCCT-2861)

---

## Tech Spec

**Epic:** _Add Epic_
**Ticket Number & Description:** [CCCT-2861](https://dimagi.atlassian.net/browse/CCCT-2861) — Optional Deliver Apps and OCS Interview Units

| Field                      | Details                                                    |
| -------------------------- | ---------------------------------------------------------- |
| **Related Materials**      | [CCCT-2861](https://dimagi.atlassian.net/browse/CCCT-2861) |
| **Mockups / Wireframes**   | _Add link_                                                 |
| **Author**                 | Pawan Verma                                                |
| **Feature Release Path**   | Path 2: New features with stability                        |
| **Release Switch**         | Not required                                               |
| **Implementation Tickets** | 9 tickets, listed in order under Implementation Tickets    |

### Relevant Codebases

- [commcare-connect](https://github.com/dimagi/commcare-connect) — optional deliver app, interview unit and scheduler models, Program Manager UI, form receiver changes, mobile API fields.
- [commcare-android](https://github.com/dimagi/commcare-android) — skip the Deliver app launch after Learn when there is none; show interviews and interview payment units.
- [open-chat-studio](https://github.com/dimagi/open-chat-studio) — configuration only, no code changes: OCS is set up to post interview completions to Connect's form receiver in the HQ form shape.

---

### Introduction

This change lets an opportunity run without a CommCare Deliver app and adds OCS interviews as a paid unit of work. Interviews are modelled as a new kind of `DeliverUnit`, triggered one at a time by a per-opportunity scheduler, and completed through the existing form receiver. This lets them reuse the whole payment, claim-limit and invoicing pipeline instead of building a parallel one.

---

### Solution

**Proposed Solution:**

Interviews are modelled as a new kind of `DeliverUnit`, so they reuse payment units, `UserVisit`, `CompletedWork`, claim limits and invoicing. A per-opportunity scheduler triggers them one at a time through OCS. The first interview triggers when the FLW finishes the Learn app, and Connect claims the opportunity for them at that moment, so no interview is ever paid without a claim. OCS posts each completion to the existing form receiver in the HQ form shape, using a dedicated OAuth application. The work splits into 9 tickets:

| #   | Ticket                                                           | Repo             | Depends on |
| --- | ---------------------------------------------------------------- | ---------------- | ---------- |
| 1   | Make the Deliver app optional                                    | commcare-connect | —          |
| 2   | Android: skip the Deliver app after Learn                        | commcare-android | 1          |
| 3   | Extract the `claim_opportunity` service                          | commcare-connect | 1          |
| 4   | Interview data model                                             | commcare-connect | 1          |
| 5   | Interview schedule page and payment unit linking                 | commcare-connect | 4          |
| 6   | Trigger interviews: auto-claim on Learn completion and beat task | commcare-connect | 3, 5       |
| 7   | Receive interview completions through the form receiver          | commcare-connect | 5, 6       |
| 8   | Mobile API: interview fields                                     | commcare-connect | 4, 7       |
| 9   | Android: show interviews and auto-claimed opportunities          | commcare-android | 8          |

- **External components:** OCS (trigger API, which already exists, and a completion post to Connect that is configured in OCS with no code changes). Mobile app changes in commcare-android.
- **Auto-claim:** `claim_opportunity(access)` is extracted from `ClaimOpportunityView` (opportunity row lock, budget and end-date checks, `OpportunityClaim` and `create_claim_limits`, and `create_hq_user_and_link` on the Deliver app's domain only when the opportunity has a Deliver app). The view and the Learn-completion hook both call it, so manual and automatic claims follow the same budget rules. Only opportunities with no Deliver app are auto-claimed, so auto-claim never makes the HQ call; on opportunities with a Deliver app, the FLW claims manually as today and their interviews start at that point.
- **Security:** the receiver only accepts OCS payloads from the OCS OAuth application with read/write scope. Each payload must reference an existing `InterviewSession` whose FLW matches `metadata.username`, so OCS cannot create visits for arbitrary users or units. Triggering uses the configuring Program Manager's OCS OAuth token (`InterviewSchedule.configured_by`), as OCS tasks do today.
- **Scaling:** the beat task selects due sessions with `select_for_update(skip_locked=True)` and an index on (`status`, `scheduled_for`), and triggers each in its own task. At most one open session exists per FLW per opportunity, so volume is bounded by active FLWs.
- **Limitations:**
  - An interview the FLW never finishes blocks the rest of their schedule until a Program Manager re-triggers or skips it.
  - Triggering depends on one Program Manager's OCS connection.
  - Form deliver units and interview units can't share a payment unit, because their `entity_id`s differ.
- **API changes:** `/api/receiver/` accepts OCS-sourced payloads. The mobile opportunity serializer emits `deliver_app: null`, `unit_type` on payment units and visits, and a new `interviews` list.
- **Error logging:** missing configuration (no OCS account for `configured_by`, unset `OCS_BASE_URL`) is logged and marks the session `failed` without retrying. Transient OCS errors retry with backoff (`autoretry_for` + `retry_backoff`). Receiver payloads with an unknown or mismatched `interview_session_id` raise `ProcessingError` and are logged, as other invalid forms are.

**Monitoring and Alerting Plan:**

- Log every trigger attempt and failure with the session ID and opportunity.
- Log auto-claim failures (budget full, opportunity ended) with the access and the reason.
- On the opportunity page, show a banner to Program Managers when sessions are `failed` because of OCS credentials.
- On the worker page, flag sessions that have been `triggered` for more than 7 days.
- Add Sentry alerts on `ProcessingError` rates from OCS-sourced receiver calls and on `trigger_interview_session` final failures.
- Dashboard counts: sessions by status per opportunity. _Add analytics destination_

**Deployment and Release:**

- Migrations are additive and run with `migrate_multi`; the beat task is registered by data migration.
- Mobile: opportunities with no deliver app are hidden from clients below the new mobile API version (_Add version_), so older apps never see `deliver_app: null`.
- OCS must be configured with the Connect OAuth client and receiver URL before the feature is used.
- Rollback: revert the deploy. Already-recorded interview visits stay as ordinary approved visits.

**Alternative Solutions:**

- **A dedicated completion endpoint** (like `TaskCompletedView`). It's simpler for OCS, but duplicates the visit and payment logic that the form receiver already owns. Rejected in favour of reusing the receiver and existing models.
- **A separate `InterviewUnit` model with its own accrual path.** Rejected because it would need parallel versions of `calculate_completed`, visit listings, exports, invoicing and the mobile payment views, and the two paths would drift.

---

### Implementation Tickets

#### Ticket 1 – Make the Deliver app optional

**Repo:** commcare-connect · **Depends on:** —

**Changes:**

- Add `Opportunity.has_deliver_app` (`deliver_app_id is not None`).
- Guard with it: `get_application` (`commcarehq/views.py`), `OpportunityChangeForm.clean_active`, `sync_learn_modules_and_deliver_units` and its callers, `verification_flags_config`, `UserVisit.hq_link` (returns `None` for interview visits), the HQ link in `user_task_details`, and `ClaimOpportunityView`, which skips `create_hq_user_and_link` when the opportunity has no Deliver app.
- The other callers of the Deliver app's domain (work-area case sync, attachment downloads, usercase updates for relearn tasks) only run when a Deliver app exists, so they are unchanged.
- Hide the Deliver app link (`opportunity_resource_modal.html`), "sync deliver units", verification flags and microplanning when there is no Deliver app.
- `OpportunityInitForm` and `OpportunityInitUpdateForm`: the deliver domain and app fields become optional (with help text); skip `_build_commcare_app("deliver")` when empty; compare learn and deliver apps only when both are set.
- Program API serializer (`program/api/serializers.py`): `deliver_app` becomes `required=False, allow_null=True`; `get_deliver_app` returns `null` when absent.
- `TaskType.app` becomes nullable and task types are scoped by `opportunity`, so OCS task types work without a Deliver app. Relearn task types still require one.
- `OpportunitySerializer` (mobile) and the data export serializer emit `deliver_app: null` when absent.
- Opportunities with no Deliver app are left out of the opportunity list for clients below the new mobile API version (`AcceptHeaderVersioning`; _Add version_).

**Acceptance criteria:**

- Every page and task above works for an opportunity with `deliver_app=None`, with no 500s.
- `get_application` still lists apps for a domain that contains an opportunity without a Deliver app.
- Tests parametrized over "with" and "without Deliver app" using a new `opportunity_without_deliver_app` fixture.
- A Program Manager can create, edit and activate an opportunity with no Deliver app, through the web and the program API.
- OCS task types can be created on an opportunity with no Deliver app.
- Older clients never receive an opportunity with `deliver_app: null`; new clients receive it, and can claim it (no HQ user is created).

#### Ticket 2 – Android: skip the Deliver app after Learn

**Repo:** commcare-android · **Depends on:** 1

**Changes:**

- After the Learn app completes, skip the Deliver app download and launch when `deliver_app` is null.
- Send the new API version header.

**Acceptance criteria:**

- An FLW on an opportunity with no Deliver app finishes Learn and is not prompted to install or open a Deliver app.
- Opportunities with a Deliver app behave as today.

#### Ticket 3 – Extract the `claim_opportunity` service

**Repo:** commcare-connect · **Depends on:** 1

**Changes:**

- Move the body of `ClaimOpportunityView` into `claim_opportunity(access)`: the opportunity row lock, budget and end-date checks, `OpportunityClaim` and `OpportunityClaimLimit.create_claim_limits`, and the HQ user link.
- The Deliver app check is optional: when `has_deliver_app` is true, call `create_hq_user_and_link` on the Deliver app's domain as today; when it is false, skip the call. FLWs already get an HQ user on the Learn domain from `start_learn_app`.
- It returns a result the view maps to its existing responses and error codes.

**Acceptance criteria:**

- `ClaimOpportunityView` responses and error codes are unchanged.
- The service can be called outside a request (from a Celery task or the form receiver).

#### Ticket 4 – Interview data model

**Repo:** commcare-connect · **Depends on:** 1

**Changes:**

- `DeliverUnit`:
  - Add `unit_type` (choices `form` / `ocs_interview`, default `form`).
  - Add `opportunity` (FK, nullable).
  - Make `app` nullable.
  - Add a check constraint: `form` requires `app`; `ocs_interview` requires `opportunity` and no `app`.
- Add `Opportunity.available_deliver_units()`: the Deliver app's form units plus the opportunity's interview units.
- `Opportunity`:
  - Add `cohort_id` (CharField, blank). It is sent to OCS in `participant_data` on every interview trigger.
- New `InterviewSchedule`:
  - `opportunity` (OneToOne)
  - `configured_by` (FK User, `SET_NULL`)
  - `active`
- New `OCSInterview` (one row per interview in the schedule; the scheduler's columns):
  - `schedule` (FK `InterviewSchedule`, `CASCADE`): the schedule this interview belongs to.
  - `deliver_unit` (OneToOne interview `DeliverUnit`, `PROTECT`): the Interview ID column; the OCS interview unit.
  - `ocs_chatbot_id`: the Chatbot column; the OCS chatbot that runs this interview.
  - `prompt_text` (TextField, blank): the Prompt column; sent as `prompt_text` to start the conversation, not shown to the FLW.
  - `session_data` (JSONField, default `dict`): the Session data column; sent as `session_data` on trigger.
  - `order` (PositiveInteger): the Order column; unique per schedule.
  - `next_trigger_days` (PositiveInteger): the Next Trigger column; days after this interview is completed before the next one triggers; unused on the last row.
  - `archived` (Boolean, default `False`): archived interviews are hidden from the schedule and never triggered; existing sessions and visits are kept.
- New `InterviewSession`:
  - `interview_session_id` (UUID)
  - `opportunity_access` (FK)
  - `deliver_unit` (FK)
  - `ocs_interview` (FK `OCSInterview`)
  - `status` (`scheduled`, `triggered`, `completed`, `failed`, `cancelled`)
  - `scheduled_for`
  - `triggered_at`
  - `completed_at`
  - `ocs_session_id`
  - `connect_channel_id`
  - `attempts`
  - `last_error`
  - `user_visit` (OneToOne, nullable)
  - Unique on (`opportunity_access`, `deliver_unit`); index on (`status`, `scheduled_for`).

**Acceptance criteria:**

- Migrations are additive, run with `migrate_multi`, and existing `DeliverUnit` rows backfill to `unit_type=form`.
- The `DeliverUnit` check constraint rejects invalid combinations.
- Constraints enforce one schedule per opportunity, unique interview order per schedule and one session per FLW per interview unit.

#### Ticket 5 – Interview schedule page and payment unit linking

**Repo:** commcare-connect · **Depends on:** 4

**Changes:**

- New class-based view `InterviewScheduleView` (`opportunity:interview_schedule`): Program Manager CRUD for interview units and schedule rows, as an orderable table (Interview, Chatbot, Prompt, Session data, Order, Next Trigger in days, session counts). Session data is edited as JSON and must be an object. The page also edits the opportunity's `cohort_id`. Saving sets `configured_by` to the current Program Manager.
- The chatbot select is a tom-select (`data-tomselect="1"`) loaded from `ocs_api.list_chatbots`. If the Program Manager hasn't connected OCS, show `ocs/_connect_prompt.html`; if `OCS_BASE_URL` is unset, hide the page and nav item.
- `add_payment_unit` / `edit_payment_unit` / `PaymentUnitForm`: choices come from `available_deliver_units()`, grouped as "Form deliver units" and "OCS interviews".
- A payment unit's required and optional units must all be one `unit_type`; mixed payment units are rejected.
- Individual payment = one payment unit per interview unit; set payment = one payment unit with several required interview units.

**Acceptance criteria:**

- A Program Manager can add, reorder and remove interviews and set Next Trigger days.
- Removing an interview that has sessions sets `archived=True` instead of deleting it; an interview with no sessions is deleted.
- Invalid session data JSON (not an object) fails validation with a clear error.
- The page degrades as described when OCS isn't connected or configured.
- A Program Manager can create payment units from interview units, individually or as a set.
- Mixed-type payment units fail validation with a clear error.

#### Ticket 6 – Trigger interviews: auto-claim on Learn completion and beat task

**Repo:** commcare-connect · **Depends on:** 3, 5

**Changes:**

- Learn processing (`update_completed_learn_date` and `process_assessments` in `form_receiver/processor.py`): when an FLW first finishes Learn (`completed_learn_date` set and assessment passed) on an opportunity with an active schedule and no Deliver app, call `claim_opportunity`, then `start_interview_schedule(access)` on commit, which creates the first session scheduled for now.
- If the claim fails (budget full, opportunity ended), no interviews start and the failure is logged.
- Opportunities with a Deliver app are never auto-claimed. `ClaimOpportunityView` calls `start_interview_schedule(access)` on commit after a successful claim when the opportunity has an active schedule.
- Beat task `trigger_due_interview_sessions` (every 5 minutes, registered by data migration) selects due sessions with `select_for_update(skip_locked=True)` and dispatches `trigger_interview_session`.
- `trigger_interview_session` calls `ocs_api.trigger_bot(configured_by, identifier=<username>, experiment=ocs_interview.ocs_chatbot_id, prompt_text=ocs_interview.prompt_text, session_data=ocs_interview.session_data, participant_data={"connectInterviewId": <session uuid>, "cohortId": opportunity.cohort_id})`, stores `ocs_session_id` / `connect_channel_id` and sets `status=triggered`.
- `ocs_api.trigger_bot` gains a `prompt_text` parameter; the current hard-coded training prompt becomes the default, so OCS tasks are unchanged. A blank interview prompt is sent as `None` and gets that default. `cohortId` is omitted when `cohort_id` is blank.
- Archived interviews are skipped: when the next interview in order is archived, scheduling moves to the one after it.
- Transient OCS errors retry with `autoretry_for` + `retry_backoff`; missing configuration marks the session `failed` without retrying.
- Sessions are cancelled when the FLW is suspended or the opportunity ends.
- When a schedule is first saved, the first interview is scheduled for the save time for FLWs who already have a claim, and, on opportunities with no Deliver app, for FLWs who already finished Learn (they are claimed first).
- Worker page: an interview sessions panel with re-trigger and skip actions. Sessions `triggered` for more than 7 days are flagged; skipping cancels the session unpaid and schedules the next one.
- The opportunity page shows a banner when sessions fail because of OCS credentials.

**Acceptance criteria:**

- On an opportunity with no Deliver app, finishing Learn creates a claim and triggers the first interview within one beat interval.
- On an opportunity with a Deliver app, finishing Learn creates no claim; claiming in the app triggers the first interview within one beat interval.
- The trigger call carries the interview's prompt and session data and the opportunity's cohort ID.
- No interview triggers for an FLW without a claim.
- A failed trigger is visible on the worker page and can be re-triggered.

#### Ticket 7 – Receive interview completions through the form receiver

**Repo:** commcare-connect · **Depends on:** 5, 6

**Changes:**

- OCS has a dedicated OAuth application in Connect, used only for receiver access. New setting `OCS_OAUTH_CLIENT_ID`.
- `FormReceiver.post` checks `request.auth.application`: OCS requests go to a new `process_ocs_interview_xform(xform)`; everything else keeps the HQ path.
- Processing finds the `InterviewSession` from the deliver block's `interview_session_id`, checks that its FLW matches `metadata.username`, marks it completed, records the visit and schedules the next interview for `completed_at + next_trigger_days`.
- Extract the shared part of `process_deliver_unit` (claim-limit lock, over-limit check, `UserVisit` and `CompletedWork` creation, `update_payment_accrued_for_user`) into a helper used by both paths.
- For interviews: `entity_id` = the FLW's `User.user_id`, `xform_id` = the OCS session ID, `form_json` = the OCS payload. Status is always `approved` + `review_status=agree` unless over limit or the FLW is suspended. `clean_form_submission`, the blocking-task check and attachment downloads are skipped.
- A missing claim raises `ProcessingError`, as for forms.

`POST /api/receiver/` (existing endpoint):

- Auth: OAuth2 bearer token from the OCS OAuth application, scope read/write.
- Request body (HQ form JSON shape, validated by `XFormSerializer`):

```json
{
  "domain": "ocs",
  "id": "<ocs_session_id>",
  "app_id": "<ocs_chatbot_id>",
  "build_id": null,
  "received_on": "2026-10-09T10:15:00Z",
  "metadata": {
    "timeStart": "2026-10-09T09:50:00Z",
    "timeEnd": "2026-10-09T10:14:30Z",
    "app_build_version": null,
    "username": "<connect username>",
    "location": null
  },
  "form": {
    "@xmlns": "http://commcareconnect.com/data/v1/learn",
    "deliver": {
      "@xmlns": "http://commcareconnect.com/data/v1/learn",
      "@id": "<interview unit slug>",
      "name": "<interview unit name>",
      "entity_id": "<FLW user_id>",
      "entity_name": "<FLW name>",
      "interview_session_id": "<connectInterviewId>"
    },
    "interview": { "summary": "_[Add fields OCS will send]_" }
  }
}
```

- Response: `200` on success, and also on a duplicate (`unique_xform_entity_deliver_unit`, handled as today). `400` when serializer validation fails. Processing errors (unknown or mismatched session) follow the receiver's existing `ProcessingError` handling.

**Acceptance criteria:**

- A completion creates exactly one approved `UserVisit` and updates `CompletedWork` and accrued payment.
- A duplicate post creates nothing and schedules no extra interview.
- A set payment unit pays only after its last required interview.
- HQ form processing is unchanged.

#### Ticket 8 – Mobile API: interview fields

**Repo:** commcare-connect · **Depends on:** 4, 7

**Changes:**

- `OpportunitySerializer`: `payment_units[].unit_type` (`form` | `ocs_interview`) and `interviews[]` (`interview_session_id`, `name`, `status`, `scheduled_for`, `completed_at`, `connect_channel_id`, `order`) for the requesting FLW, prefetched to avoid N+1 queries.
- `UserVisitViewSet`: visits gain `unit_type`.

**Acceptance criteria:**

- An FLW's opportunity response lists their interviews in order with current status.
- Existing fields are unchanged.

#### Ticket 9 – Android: show interviews and auto-claimed opportunities

**Repo:** commcare-android · **Depends on:** 8

**Changes:**

- Add an interviews list with status, which opens the chat via `connect_channel_id`.
- Label interview-based payment units and visits.
- Show auto-claimed opportunities as claimed after Learn, with no Claim step.

**Acceptance criteria:**

- An FLW sees each interview's status and can open the active one.
- Earnings from interviews show alongside other payment units.

---

### Further Considerations

- **Auto-claim scope:** only opportunities with no Deliver app are auto-claimed. On opportunities with a Deliver app, auto-claiming would either skip HQ user creation on the Deliver domain (breaking Deliver forms) or make that HQ call in the form receiver, and would reserve the full per-user budget for FLWs who may never deliver.
- **Auto-claim failures:** if the budget is full when an FLW finishes Learn, they get no claim and no interviews. Confirm whether they should be told in the app, and whether a later budget increase should retry the claim.
- **Mixed payment units:** disallowed in v1, because form visits use a beneficiary `entity_id` and interviews use the FLW. Confirm no program needs "form + interview" sets.
- **OCS credentials:** triggering uses one Program Manager's OCS token. If that Program Manager leaves or disconnects, every trigger fails until another Program Manager re-saves the schedule. Consider an org-level OCS service token.
- **Stalled interviews:** with one-at-a-time triggering, an abandoned interview blocks the rest. v1 flags it after 7 days and lets a Program Manager re-trigger or skip it; skipped interviews are never paid.
- **Existing FLWs:** when a schedule is first saved, FLWs who already have a claim, and (with no Deliver app) FLWs who already finished Learn, get their first interview scheduled for the save time.
- **Payload contract with OCS:** agree the exact `form.interview` fields and the `id` uniqueness rule (`xform_id` max length is 50).
- **Mobile compatibility:** agree the minimum mobile API version for opportunities with no Deliver app.
- **QA:** test opportunities with a Deliver app only, interviews only, and both. Cover Learn finishing through module completion and through the assessment, auto-claim when the budget is full or the opportunity has ended, an FLW who had already claimed manually, duplicate OCS posts, suspended FLWs and over-limit.
