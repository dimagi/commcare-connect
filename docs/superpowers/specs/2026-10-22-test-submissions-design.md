# Test Submissions page — design decisions

## Goal
A page reachable from the opportunity page ("Test submissions") that lists the opportunity's workers and lets a
developer trigger a fake deliver form submission for any worker + deliver unit, in local test mode only.

## Decisions

1. **DB state** — The page and its plumbing add no new models, migrations or fixture rows. It only reads existing
   workers/apps/deliver units. A triggered submission runs through the real form processor and writes exactly
   what an HQ-forwarded form would (UserVisit, CompletedWork, etc.).
   - **Amended:** one exception — a per-worker button claims the opportunity (or adds missing claim limits),
     mirroring the mobile claim endpoint. See decision 13.

2. **Gate** — Enabled only when `settings.DEBUG and settings.ENABLE_TEST_SUBMISSIONS`.
   - `ENABLE_TEST_SUBMISSIONS = False` hard-coded in `base.py` (so code can always reference it; never env-read
     there). `local.py` reads it from `.env`: `env.bool("ENABLE_TEST_SUBMISSIONS", default=False)`.
   - URL registered only when the gate is on (404 otherwise); view re-checks the gate too.
   - "Test submissions" link on the opportunity page rendered only when the gate is on.

3. **Entry point** — The view builds a payload, validates it with `XFormSerializer`, and calls
   `process_xform(xform, app.hq_server)` directly (the deliver or learn app, per form type). No HTTP, no OAuth
   app/token/HQServer rows. `ProcessingError` is shown on the page as a message.
   - `metadata.username` = the worker's `ConnectIDUserLink.commcare_username` that ends in
     `@{app.cc_domain}.commcarehq.org` (case-insensitive), preferring a link whose `hq_server` is the app's.
     Matching on the username suffix, not `domain`/`hq_server`, because links made by `CreateUserLinkView`
     leave those null and the processor itself only matches on `commcare_username`.
   - Worker with no matching link → listed, submit disabled, reason shown.

4. **No pre-checks** — As close to the real experience as possible. Every worker × deliver unit can be submitted
   (except no-ConnectIDUserLink, where no form can be built). The page reports the real outcome:
   - `ProcessingError` → its message (what HQ would get as a 400).
   - Success → the resulting visit(s): status, flags/flag reasons, over-limit reasons.
   - Inactive / ended opportunity → processor silently ignores the form (real 200 behaviour); the page reports
     "accepted, but no active opportunity matched — nothing was recorded" instead of a fake success.

5. **Payload** — Built by new production code (not `form_receiver/tests/xforms.py`, which needs dev-only
   factory-boy), modelled on `MOCK_FORM` / `DELIVER_UNIT_XML_TEMPLATE`. ConnectFormRepeater shape: **no
   `attachments`** (avoids HQ download on commit; `attachment_missing` fires if that rule is on, as in prod).
   - Always generated: fresh xform `id`/`instanceID`, `domain`/`app_id` from the deliver app, `build_id`,
     `app_build_version`, deliver block `@id`/`name` from the chosen `DeliverUnit`, `@xmlns` = `CCC_LEARN_XMLNS`.
   - Defaults with optional "Customise" overrides per submission:
     - entity: new random `entity_id`/`entity_name` (default) or reuse one of this worker's existing entities
     - visit date/time: now (default, few-minute duration)
     - GPS lat/lon: blank (default)
     - number of deliver blocks in the form: 1 (default)

6. **Access** — Same as the opportunity dashboard: class-based view with `OpportunityObjectMixin` +
   `OppViewAccessMixin` (plus the gate from decision 2).

7. **Layout & interaction**
   - Header: opportunity name, deliver app (name, domain, app_id), HQ server; back link to the dashboard.
   - Table, one row per `OpportunityAccess`: worker name, HQ username (or "no ConnectID link" → row disabled),
     deliver unit dropdown (all `DeliverUnit`s of the deliver app), Submit, ⚙ toggle (Alpine) for the
     row's "Customise" panel.
   - Submit = htmx POST; response is a log entry prepended to a page-level **submission log** (newest first,
     `hx-swap="afterbegin"`). Log lives only in the page — gone on reload, no DB state.
   - Log entry: time, worker, deliver unit, overrides used, outcome (red `ProcessingError` message, or per created
     visit: status, flag reasons, over-limit reasons, link to visit), collapsible raw payload JSON.

8. **Entry link** — Last item in the dashboard hamburger menu (`opportunity/opportunity_menu.html`), behind a
   divider, labelled "Test submissions (local)". Rendered only when a `test_submissions_enabled` template tag
   returns true; the tag and the URL registration share one gate function. (Menu itself is only shown to
   `has_standard` users — accepted.)

9. **Scope** — Three form types: **deliver**, **learn module**, **assessment**. Out of scope: `task` blocks,
   `work_area_update` (needs an attachment, conflicts with decision 5).
   - Each row gets a form-type selector; the row's controls switch with it (Alpine):
     - Deliver → deliver unit dropdown (+ Customise overrides from decision 5)
     - Learn module → multi-select of the learn app's existing `LearnModule`s (one `module` block each)
     - Assessment → `user_score` input, default = learn app `passing_score`
   - Learn/assessment forms use the **learn** app's `cc_domain`/`cc_app_id`/`hq_server`; username comes from the
     `ConnectIDUserLink` for that app's server + domain (resolved per form type, not per row).
   - Visit date/time override also applies to learn/assessment (`timeEnd` drives `CompletedModule.date` /
     `Assessment.date`).
   - Log outcome for learn: modules completed, whether `completed_learn_date` got set. Assessment: score,
     passing score, passed.
   - Note: learning + passing does **not** create an `OpportunityClaim` — claiming is a mobile API call. The page
     does not create claims (decision 1).

10. **Post-commit side effects** — Left as-is: the view runs under the normal `ATOMIC_REQUESTS` transaction and
    `on_commit` tasks (e.g. assessment push notification to ConnectID) behave exactly as in the receiver. Using
    assessments locally needs a reachable ConnectID or a real Celery worker (`CELERY_TASK_ALWAYS_EAGER=False`).
    - So a 500 isn't swallowed: the page's JS handles `htmx:responseError` and prepends a log entry
      "Request failed (HTTP <status>) — data may already be committed; check the server log."

11. **Tests & docs**
    - Gate: unconfigured path — `ENABLE_TEST_SUBMISSIONS` off (and `DEBUG` off) → gate false, menu link not
      rendered, view refuses (404). On → link rendered.
    - Payload builder (pure functions): deliver/learn/assessment payloads pass `XFormSerializer`, carry the right
      domain/app_id/username/blocks, honour overrides (entity reuse, date, GPS, block count), fresh xform id each call.
    - Username resolution: picks the link matching the app's `hq_server` + `cc_domain`; none → disabled reason.
    - End-to-end through `process_xform` with the built payload: deliver creates a `UserVisit` for the right
      worker/deliver unit; missing claim → `ProcessingError` surfaced; learn creates `CompletedModule`s;
      assessment creates `Assessment` (notification task mocked).
    - Outcome summariser (visit status/flags/over-limit, learn/assessment results) tested as a function.
    - `form_receiver/README.md`: short "Local testing" section pointing at the page and its gate.
    - `config/settings/local.py`: commented `ENABLE_TEST_SUBMISSIONS` entry, like the Jira dry-run block.

12. **Code layout**
    - `form_receiver/test_submissions.py`: gate function, payload builders, username resolution, outcome summariser.
    - View (class-based) + URL in `opportunity/` next to the dashboard; URL added only when the gate is on.
    - Templates: `opportunity/test_submissions.html` + `opportunity/partials/test_submission_log_entry.html`.
    - JS: `static/js/test_submissions.js` (row controls, `htmx:responseError` log entry), no inline JS.

13. **Claim button** (added after the first build) — Each worker row has a button that writes a claim, the
    one write the page does besides the processor's.
    - Unclaimed worker → "Claim opportunity": same checks as the mobile claim endpoint (refused when the remaining
      budget can't fund another worker or the opportunity has ended), then `OpportunityClaim` (end_date =
      opportunity end_date) + `OpportunityClaimLimit.create_claim_limits`. HQ user creation is skipped.
    - Claimed worker missing a limit for some payment unit → "Create claim limits": `create_claim_limits` only.
    - Real budget rules: units the remaining budget can't fund get no limit, and the log entry lists them.
    - **Amended:** the button is a "next step" button: **Accept invite** first (as `AcceptInviteView`: access
      + `UserInvite` marked accepted; refused without a `UserInvite`), then claim, then claim limits. Seeded
      accesses are unaccepted, and the Deliver tab lists accepted workers only.
    - **Amended:** a **Start learning** step sits between accept and claim (as `start_learn_app`, minus the HQ
      API call): it creates the learn-domain `ConnectIDUserLink` (`<username>@<domain>.commcarehq.org`, with
      domain + the opportunity's HQ server) and sets `date_learn_started`. Claiming creates the deliver-domain link
      the same way. A worker who has already claimed skips the learn step.
    - The row's button is re-rendered out of band after each click.
    - The submission log card collapses (Alpine toggle; entries keep arriving while collapsed).

14. **Adding workers in test mode** — With the gate on, `add_connect_users` uses `fake_connect_users` instead of
    ConnectID's `fetch_users` (made-up `test_<digits>` users, deterministic per phone number), and `invite_user`
    records the invite without sending the SMS or the ConnectID push. The gate lives in
    `utils/local_testing.py`, free of app imports, so Celery tasks can check it without import cycles.

## Open questions
None.
