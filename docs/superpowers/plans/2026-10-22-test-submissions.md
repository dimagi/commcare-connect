# Test Submissions Page Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A local-only page, linked from the opportunity dashboard menu, that fires fake deliver / learn-module /
assessment form submissions for any worker of an opportunity through the real form processor, and logs the outcome.

**Architecture:** All logic lives in `form_receiver/test_submissions.py` as pure-ish functions: a settings gate,
username resolution, payload builders (plain dicts shaped like an HQ `ConnectFormRepeater` payload) and
`submit_test_form`, which validates with `XFormSerializer`, calls `process_xform` directly and summarises what was
recorded. A class-based view in `opportunity/` renders the worker table (GET) and returns one log-entry partial per
submission (htmx POST). No models, migrations or fixture rows are added.

**Tech Stack:** Django 5.2, DRF serializer (`XFormSerializer`), htmx, Alpine.js, webpack, pytest + factory-boy.

**Spec:** `docs/superpowers/specs/2026-10-22-test-submissions-design.md`

## Global Constraints

- Gate: enabled only when `settings.DEBUG and settings.ENABLE_TEST_SUBMISSIONS`. `base.py` hard-codes
  `ENABLE_TEST_SUBMISSIONS = False` (never env-read there); `local.py` sets
  `ENABLE_TEST_SUBMISSIONS = env.bool("ENABLE_TEST_SUBMISSIONS", default=False)`.
- No new models, migrations, or writes other than what `process_xform` itself does. Never create claims, links,
  HQ servers, OAuth apps or tokens.
- Production code must not import from `form_receiver/tests/` (factory-boy is a dev-only dependency).
- Payloads carry **no `attachments` key**.
- Every Connect block has `"@xmlns": CCC_LEARN_XMLNS` (`form_receiver/const.py`) and is a **dict**; multiple blocks
  of one type sit under distinct parent keys (`_get_matching_blocks` crashes on a list value).
- Fresh `uuid4` xform `id` (also `metadata.instanceID`) on every build.
- No pre-checks beyond "worker has a username for this app": every other failure is the processor's to report.
- Menu label: `Test submissions (local)`. Page title: `Test submissions`.
- Access: same as the dashboard — `OpportunityObjectMixin` + `OppViewAccessMixin`.
- Post-commit tasks are untouched (normal `ATOMIC_REQUESTS`); the page only surfaces failed requests client-side.
- JS goes in `static/js/test_submissions.js` as a webpack entry; no inline JS. Use predefined style classes from
  `tailwind/tailwind.css` where one exists.

## Review Focus

1. **Opportunity inactive or past `end_date`** — `process_xform` silently does nothing; the log must say
   "accepted, but nothing was recorded", never a success. Test in Task 3.
2. **Worker whose only `ConnectIDUserLink` has null `domain`/`hq_server`** (made by `CreateUserLinkView`) — must
   still resolve via the username suffix. Test in Task 2.
3. **Reusing an entity the worker already has** — second visit for the same `entity_id` must come back as
   `duplicate` (or flagged), proving the override reaches the processor. Test in Task 3.
4. **Same worker has links for both learn and deliver domains** — each form type must use its own app's domain
   username. Test in Task 2.
5. **POSTed ids from another opportunity** (deliver unit, learn module, access) — form validation must reject them,
   not submit for the wrong opportunity. Test in Task 4.

---

## File Structure

| File | Responsibility |
|---|---|
| `commcare_connect/form_receiver/test_submissions.py` (create) | Gate, username lookup, payload builders, submit + outcome |
| `commcare_connect/form_receiver/templatetags/__init__.py`, `test_submissions.py` (create) | `test_submissions_enabled` template tag |
| `commcare_connect/form_receiver/tests/test_test_submissions.py` (create) | Tests for all of the above |
| `commcare_connect/opportunity/forms.py` (modify) | `TestSubmissionForm` |
| `commcare_connect/opportunity/views.py` (modify) | `TestSubmissionsView` |
| `commcare_connect/opportunity/urls.py` (modify) | Gated route |
| `commcare_connect/opportunity/tests/test_test_submission_form.py` (create) | Form validation tests |
| `commcare_connect/templates/opportunity/test_submissions.html` (create) | Page |
| `commcare_connect/templates/opportunity/partials/test_submission_log_entry.html` (create) | One log entry |
| `commcare_connect/templates/opportunity/opportunity_menu.html` (modify) | Menu link |
| `commcare_connect/static/js/test_submissions.js` (create), `webpack/base.config.js` (modify) | Row controls, error log entry |
| `config/settings/base.py`, `config/settings/local.py` (modify) | Setting |
| `commcare_connect/form_receiver/README.md` (modify) | "Local testing" section |

---

### Task 1: Gate setting, gate function and template tag

**Files:**
- Modify: `config/settings/base.py` (CommCareConnect section), `config/settings/local.py` (after the Jira dry-run block)
- Create: `commcare_connect/form_receiver/test_submissions.py`
- Create: `commcare_connect/form_receiver/templatetags/__init__.py`, `commcare_connect/form_receiver/templatetags/test_submissions.py`
- Test: `commcare_connect/form_receiver/tests/test_test_submissions.py`

**Interfaces:**
- Produces: `test_submissions_enabled() -> bool`; `ensure_test_submissions_enabled() -> None` (raises
  `django.http.Http404` when disabled); template tag library `test_submissions` with simple tag
  `test_submissions_enabled` (usage: `{% load test_submissions %}{% test_submissions_enabled as enabled %}`).

- [ ] **Step 1: Write the failing tests**

```python
@pytest.mark.parametrize(
    "debug, enabled, expected",
    [(True, True, True), (True, False, False), (False, True, False), (False, False, False)],
)
def test_test_submissions_enabled(settings, debug, enabled, expected):
    settings.DEBUG = debug
    settings.ENABLE_TEST_SUBMISSIONS = enabled
    assert test_submissions_enabled() is expected


def test_ensure_test_submissions_enabled_raises_404_when_off(settings):
    settings.DEBUG = True
    settings.ENABLE_TEST_SUBMISSIONS = False
    with pytest.raises(Http404):
        ensure_test_submissions_enabled()


def test_setting_defaults_off_without_env():
    # base.py value, i.e. what test settings inherit when .env doesn't set it
    from config.settings import base
    assert base.ENABLE_TEST_SUBMISSIONS is False


@pytest.mark.parametrize("enabled, expected", [(True, "yes"), (False, "no")])
def test_template_tag(settings, enabled, expected):
    settings.DEBUG = True
    settings.ENABLE_TEST_SUBMISSIONS = enabled
    tpl = Template("{% load test_submissions %}{% test_submissions_enabled as on %}{% if on %}yes{% else %}no{% endif %}")
    assert tpl.render(Context()) == expected
```

- [ ] **Step 2: Run to verify they fail**

Run: `pytest commcare_connect/form_receiver/tests/test_test_submissions.py -v`
Expected: FAIL (ImportError / setting missing)

- [ ] **Step 3: Implement**
  - `base.py`: `ENABLE_TEST_SUBMISSIONS = False` with a one-line comment that only `local.py` may turn it on.
  - `local.py`: a commented block like the Jira dry-run one: "Enables the opportunity Test submissions page, which
    fires fake HQ forms through the form processor. Also needs DEBUG." then
    `ENABLE_TEST_SUBMISSIONS = env.bool("ENABLE_TEST_SUBMISSIONS", default=False)`.
  - `test_submissions.py`: module docstring stating it is local-only tooling; the two functions above
    (`getattr(settings, "ENABLE_TEST_SUBMISSIONS", False)` so a missing setting reads as off).
  - Template tag: `register.simple_tag` wrapping `test_submissions_enabled()`.

- [ ] **Step 4: Run to verify they pass** — same command, expected PASS.

- [ ] **Step 5: Commit**

```bash
git add config/settings/base.py config/settings/local.py commcare_connect/form_receiver/test_submissions.py commcare_connect/form_receiver/templatetags commcare_connect/form_receiver/tests/test_test_submissions.py
git commit -m "Add ENABLE_TEST_SUBMISSIONS gate for local test submissions"
```

---

### Task 2: Username lookup and payload builders

**Files:**
- Modify: `commcare_connect/form_receiver/test_submissions.py`
- Test: `commcare_connect/form_receiver/tests/test_test_submissions.py`

**Interfaces:**
- Consumes: nothing from Task 1.
- Produces:

```python
@dataclass(frozen=True)
class SubmissionOverrides:
    entity_id: str | None = None        # None → fresh uuid4 string
    entity_name: str | None = None      # None → "Test entity <first 8 chars of entity_id>"
    visit_time: datetime | None = None  # None → now(); aware datetime = timeStart
    latitude: float | None = None       # both lat and lon set → location "<lat> <lon> 0 10"; else None
    longitude: float | None = None
    deliver_block_count: int = 1

FORM_DURATION = timedelta(minutes=5)    # timeEnd = timeStart + FORM_DURATION

def get_commcare_username(user: User, app: CommCareApp) -> str | None
def build_deliver_form(app: CommCareApp, username: str, deliver_unit: DeliverUnit, overrides: SubmissionOverrides) -> dict
def build_learn_form(app: CommCareApp, username: str, modules: Sequence[LearnModule], overrides: SubmissionOverrides) -> dict
def build_assessment_form(app: CommCareApp, username: str, score: int, overrides: SubmissionOverrides) -> dict
```

Payload shape (the only things to get exactly right; one private `_build_form(app, username, blocks: dict, overrides)` builds the envelope):

```python
{
    "domain": app.cc_domain, "app_id": app.cc_app_id, "id": xform_id,
    "build_id": "test-submission", "received_on": <now iso>,
    "metadata": {"username": username, "timeStart": <iso>, "timeEnd": <iso>, "location": <str|None>,
                 "app_build_version": 1, "instanceID": xform_id, "deviceID": "connect-test-submissions"},
    "form": {"@xmlns": "http://commcareconnect.com/test-submission", "@name": "Test submission",
             "meta": <same dict as metadata>, **blocks},
}
```

Blocks are keyed `"<kind>_<n>"` (n from 1), each `{"<kind>": block}`:
- deliver ×`deliver_block_count`: `{"@xmlns": CCC_LEARN_XMLNS, "@id": du.slug, "name": du.name, "entity_id": …, "entity_name": …}` — every block in one form shares the same entity (as a real multi-unit form does).
- module per `LearnModule`: `{"@xmlns", "@id": m.slug, "name": m.name, "description": m.description, "time_estimate": str(m.time_estimate)}`
- assessment: `{"@xmlns", "@id": "assessment", "user_score": str(score)}`

`get_commcare_username`: links of `user` with `commcare_username__iendswith=f"@{app.cc_domain}.commcarehq.org"`,
ordered so a link whose `hq_server_id == app.hq_server_id` comes first; return its `commcare_username` or `None`.

- [ ] **Step 1: Write the failing tests** (use `opportunity`, `mobile_user_with_connect_link`, `user_with_connectid_link` fixtures; `DeliverUnitFactory`, `LearnModuleFactory`, `ConnectIdUserLinkFactory` from existing factories)

```python
def test_get_commcare_username_matches_domain_suffix(opportunity, mobile_user_with_connect_link):
    # fixture's links have hq_server=None — the CreateUserLinkView case (Review Focus 2)
    assert get_commcare_username(mobile_user_with_connect_link, opportunity.deliver_app) == (
        f"test@{opportunity.deliver_app.cc_domain}.commcarehq.org")

def test_get_commcare_username_per_app_domain(opportunity, user_with_connectid_link):
    # learn and deliver apps have different domains in the fixture (Review Focus 4)
    for app in (opportunity.learn_app, opportunity.deliver_app):
        assert get_commcare_username(user_with_connectid_link, app).endswith(f"@{app.cc_domain}.commcarehq.org")

def test_get_commcare_username_prefers_matching_server(opportunity, mobile_user):
    # two links, same suffix: one on another server, one on the app's server → the app's one wins
    ...

def test_get_commcare_username_none_without_link(opportunity, mobile_user):
    assert get_commcare_username(mobile_user, opportunity.deliver_app) is None

@pytest.mark.parametrize("builder", ["deliver", "learn", "assessment"])
def test_built_payload_passes_serializer(opportunity, builder):
    xform = XFormSerializer(data=payload); assert is_valid(); save()
    assert xform.domain == app.cc_domain and xform.app_id == app.cc_app_id
    assert xform.metadata.username == "u@d.commcarehq.org"
    assert "attachments" not in payload

def test_each_build_has_fresh_xform_id(...)                 # two builds → different "id"; id == metadata.instanceID

def test_deliver_blocks_found_by_processor_jsonpath(...):
    # deliver_block_count=3 → _get_matching_blocks(DELIVER_UNIT_JSONPATH, xform) returns 3 dicts, @id == du.slug,
    # all sharing one entity_id; default entity_id parses as UUID

def test_deliver_overrides_applied(...):
    # entity_id/entity_name/visit_time/lat+lon → block fields, metadata.timeStart == visit_time,
    # timeEnd == visit_time + FORM_DURATION, location == "1.5 2.5 0 10"

def test_location_none_when_only_one_coordinate(...)

def test_learn_form_one_module_block_per_module(...)        # 2 modules → 2 blocks via LEARN_MODULE_JSONPATH, slugs match

def test_assessment_block_score(...)                        # user_score == "73"
```

- [ ] **Step 2: Run to verify they fail** — `pytest commcare_connect/form_receiver/tests/test_test_submissions.py -v`, expected FAIL (ImportError).

- [ ] **Step 3: Implement** the dataclass, constant, lookup and builders in `test_submissions.py` per the Interfaces block.

- [ ] **Step 4: Run to verify they pass** — expected PASS.

- [ ] **Step 5: Commit** — `git commit -m "Build fake HQ form payloads for test submissions"`

---

### Task 3: Submit through the processor and summarise the outcome

**Files:**
- Modify: `commcare_connect/form_receiver/test_submissions.py`
- Test: `commcare_connect/form_receiver/tests/test_test_submissions.py`

**Interfaces:**
- Consumes: builders and `SubmissionOverrides` from Task 2.
- Produces:

```python
@dataclass
class SubmissionOutcome:
    payload: dict
    error: str | None = None                          # ProcessingError message or serializer errors
    visits: list[UserVisit] = field(default_factory=list)
    completed_modules: list[CompletedModule] = field(default_factory=list)
    assessments: list[Assessment] = field(default_factory=list)
    completed_learn_date: datetime | None = None       # access.completed_learn_date after learn forms

    @property
    def recorded_nothing(self) -> bool: ...           # no error and all three lists empty

def submit_test_form(payload: dict, app: CommCareApp, access: OpportunityAccess) -> SubmissionOutcome
```

`submit_test_form`: validate with `XFormSerializer` (invalid → `error=str(serializer.errors)`, nothing processed);
call `process_xform(xform, app.hq_server)` inside `transaction.atomic()` (a savepoint, so a `ProcessingError`
rolls back only this form, like the receiver's 400); catch `ProcessingError` → `error=str(e)`. On success, load
`UserVisit`, `CompletedModule`, `Assessment` by `xform_id=payload["id"]`, and refresh `access` for
`completed_learn_date`. Do not catch other exceptions — a 500 is the real behaviour (spec decision 10).

- [ ] **Step 1: Write the failing tests** (`mobile_user_with_connect_link` has access + claim + claim limits; link a `DeliverUnitFactory(app=opportunity.deliver_app, payment_unit=<one of the opportunity's payment units>)`)

```python
def test_deliver_submission_creates_visit_for_worker_and_unit(...):
    outcome = submit_test_form(build_deliver_form(...), opportunity.deliver_app, access)
    assert outcome.error is None
    [visit] = outcome.visits
    assert visit.user == worker and visit.deliver_unit == du and visit.xform_id == outcome.payload["id"]

def test_reused_entity_is_duplicate(...):                    # Review Focus 3
    # @pytest.mark.parametrize("opportunity", [{"verification_flags": {"duplicate": True}}], indirect=True)
    # two submissions with SubmissionOverrides(entity_id="e-1") → second visit status == VisitValidationStatus.duplicate

def test_missing_claim_returns_processing_error(opportunity, mobile_user):
    # mobile_user has access but no claim; give them a link
    assert "has not claimed" in outcome.error and outcome.visits == []

def test_unlinked_deliver_unit_returns_processing_error(...)  # DeliverUnit with payment_unit=None → "Payment unit is not configured"

def test_inactive_opportunity_records_nothing(...):          # Review Focus 1
    opportunity.active = False; opportunity.save()
    assert outcome.recorded_nothing and outcome.error is None

def test_learn_submission_creates_completed_modules(...)     # 2 LearnModules → 2 CompletedModule; completing all → completed_learn_date set

def test_assessment_submission_records_score(...):           # passing_score from learn_app; score 80 vs 70 → passed True
    # on_commit callbacks don't run in pytest's transaction, so no ConnectID call happens

def test_error_rolls_back_only_this_form(...):              # failed submission leaves earlier successful visit in place
```

- [ ] **Step 2: Run to verify they fail** — expected FAIL (ImportError).
- [ ] **Step 3: Implement** `SubmissionOutcome` and `submit_test_form`.
- [ ] **Step 4: Run to verify they pass** — `pytest commcare_connect/form_receiver/ -v`, expected PASS (existing receiver tests too).
- [ ] **Step 5: Commit** — `git commit -m "Submit test forms through the form processor"`

---

### Task 4: Form, view, route, page, menu link and README

**Files:**
- Modify: `commcare_connect/opportunity/forms.py`, `commcare_connect/opportunity/views.py`, `commcare_connect/opportunity/urls.py`
- Create: `commcare_connect/templates/opportunity/test_submissions.html`, `commcare_connect/templates/opportunity/partials/test_submission_log_entry.html`
- Modify: `commcare_connect/templates/opportunity/opportunity_menu.html`
- Create: `commcare_connect/static/js/test_submissions.js`; Modify: `webpack/base.config.js`
- Modify: `commcare_connect/form_receiver/README.md`
- Test: `commcare_connect/opportunity/tests/test_test_submission_form.py`

**Interfaces:**
- Consumes: `ensure_test_submissions_enabled`, `test_submissions_enabled` tag (Task 1); builders, `SubmissionOverrides`, `get_commcare_username` (Task 2); `submit_test_form`, `SubmissionOutcome` (Task 3).
- Produces:

```python
class TestSubmissionForm(forms.Form):
    FORM_TYPES = [("deliver", "Deliver"), ("learn", "Learn module"), ("assessment", "Assessment")]
    access = forms.ModelChoiceField(queryset=OpportunityAccess.objects.none())
    form_type = forms.ChoiceField(choices=FORM_TYPES)
    deliver_unit = forms.ModelChoiceField(queryset=DeliverUnit.objects.none(), required=False)
    learn_modules = forms.ModelMultipleChoiceField(queryset=LearnModule.objects.none(), required=False)
    score = forms.IntegerField(required=False)
    entity_id = forms.CharField(required=False)       # blank → new entity
    entity_name = forms.CharField(required=False)
    visit_time = forms.DateTimeField(required=False)
    latitude = forms.FloatField(required=False, min_value=-90, max_value=90)
    longitude = forms.FloatField(required=False, min_value=-180, max_value=180)
    deliver_block_count = forms.IntegerField(min_value=1, max_value=10, initial=1)

    def __init__(self, *args, opportunity: Opportunity, **kwargs)  # scopes the three querysets to this opportunity's access / deliver_app / learn_app
    def get_overrides(self) -> SubmissionOverrides
```

`clean()` requires `deliver_unit` for deliver, ≥1 `learn_modules` for learn, `score` for assessment.

`TestSubmissionsView(OpportunityObjectMixin, OppViewAccessMixin, View)` at
`<slug:opp_id>/test_submissions/`, name `test_submissions`:
- `dispatch` calls `ensure_test_submissions_enabled()` first.
- GET renders `test_submissions.html` with: opportunity, both apps, one row per `OpportunityAccess`
  (`select_related("user")`) carrying `deliver_username`, `learn_username` (via `get_commcare_username`) and up to
  20 most recent distinct `(entity_id, entity_name)` from the worker's visits, plus the deliver units, learn modules
  and `learn_app.passing_score`.
- POST: invalid form → log-entry partial showing the form errors; valid → pick app by form type, resolve the
  username (none → log entry "No CommCare username for <domain>"), build, `submit_test_form`, render the partial
  with `outcome`, worker, form type, deliver unit / modules. Log entry links each visit to
  `opportunity:user_visit_details`.
- `urls.py`: append the route inside `if test_submissions_enabled():` (import-time gate; the `dispatch` check covers
  settings overridden after import).

- [ ] **Step 1: Write the failing form tests** (Review Focus 5)

```python
def test_form_rejects_deliver_unit_from_other_opportunity(opportunity, mobile_user): ...   # "deliver_unit" in errors
def test_form_rejects_access_from_other_opportunity(...)
def test_form_rejects_learn_module_from_deliver_app(...)
@pytest.mark.parametrize("form_type, missing", [("deliver", "deliver_unit"), ("learn", "learn_modules"), ("assessment", "score")])
def test_form_requires_fields_per_type(...)
def test_get_overrides_maps_fields(...)                    # blank entity_id → None; lat/lon/time/count passed through
```

- [ ] **Step 2: Run to verify they fail** — `pytest commcare_connect/opportunity/tests/test_test_submission_form.py -v`, expected FAIL.
- [ ] **Step 3: Implement `TestSubmissionForm`** in `opportunity/forms.py`; run the tests, expected PASS.
- [ ] **Step 4: Implement the view, route and templates.**
  - Page per spec decision 7: header (opportunity name, deliver/learn app name + domain + app id, HQ server, back
    link to `opportunity:detail`), table one row per worker. Each row is its own `<form hx-post hx-target="#submission-log" hx-swap="afterbegin">`
    with an Alpine `x-data="{ formType: 'deliver', customise: false }"`: form-type select, controls shown with
    `x-show` per type, ⚙ toggles the Customise panel (entity select from recent entities + "New entity", entity
    name, visit time, lat, lon, block count). Rows whose username for the selected type is missing disable Submit
    and show "No ConnectID link for <domain>".
  - Below the table: `<div id="submission-log">` with an empty-state line "Submissions you make appear here. The log clears when you reload."
  - Log entry partial: time, worker, form type + unit/modules, overrides used; outcome — red `error`; or
    "Accepted, but nothing was recorded — no active opportunity matched this app" when `recorded_nothing`; or per
    visit: status, `flag_reason.flags` descriptions, `over_limit_reasons`, link; per module name; assessment
    score / passing score / passed; `completed_learn_date` when set. Collapsible `<details>` with
    `payload` pretty-printed (`json_script` or `pprint`).
  - Table must fit without horizontal scroll (AGENTS.md); let the HQ username column wrap.
- [ ] **Step 5: Implement `static/js/test_submissions.js`** and add `test_submissions` to `webpack/base.config.js`
  entries; load `bundles/js/test_submissions-bundle.js` in the page's `{% block javascript %}`. The script listens
  for `htmx:responseError` on `#submission-log`'s forms and prepends an entry: "Request failed (HTTP <status>) —
  data may already be committed; check the server log."
- [ ] **Step 6: Add the menu link** at the end of `opportunity_menu.html`'s `<ul>`: `{% load test_submissions %}`,
  `{% test_submissions_enabled as test_submissions_on %}`, and inside `{% if test_submissions_on %}` an `<li>` with
  a top border divider linking to `opportunity:test_submissions`, text `{% translate "Test submissions (local)" %}`.
  The `{% url %}` must sit inside the `if` — the route doesn't exist when the gate is off.
- [ ] **Step 7: README** — add a "Local Testing" section to `form_receiver/README.md` (before "Error Handling"):
  the page, both settings it needs, that it calls `process_xform` directly (skips the `FormReceiver` view and
  OAuth), sends no attachments, and that assessments need ConnectID or a real Celery worker.
- [ ] **Step 8: Verify** — `pytest commcare_connect/form_receiver commcare_connect/opportunity -q` → all pass;
  `npm run build` → succeeds; `prek run -a` → clean.
- [ ] **Step 9: Manual check** — with `ENABLE_TEST_SUBMISSIONS=True` in `.env`, `./manage.py runserver`, open an
  opportunity, use the menu link, submit one of each form type and one deliver with a reused entity; confirm log
  entries and that the visit link opens. Then unset the env var, restart, and confirm the menu item is gone and
  `/a/<org>/opportunity/<opp>/test_submissions/` is a 404.
- [ ] **Step 10: Commit**

```bash
git add commcare_connect/opportunity commcare_connect/templates/opportunity commcare_connect/static/js/test_submissions.js webpack/base.config.js commcare_connect/form_receiver/README.md
git commit -m "Add local Test submissions page to the opportunity dashboard"
```
