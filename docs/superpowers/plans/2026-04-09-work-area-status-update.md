# Work Area Status Update via XForm — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Enable XForms to update WorkArea status (starting with inaccessibility reports) via a new `work_area_update` block in deliver forms.

**Architecture:** A new `work_area_update` JSONPath + processing function in the form receiver pipeline. The WorkArea model gets an `inaccessibility_reason` field and expanded pghistory tracking. Status transitions are validated against a dict of allowed transitions.

**Tech Stack:** Django 4.2, PostGIS, pghistory, pytest, factory-boy

---

### Task 1: Add `inaccessibility_reason` field to WorkArea model

**Files:**
- Modify: `commcare_connect/microplanning/models.py:43-67`

- [ ] **Step 1: Add field and expand pghistory tracking**

In `commcare_connect/microplanning/models.py`, change the pghistory decorator and add the new field:

```python
@pghistory.track(fields=["expected_visit_count", "work_area_group", "status", "inaccessibility_reason"])
class WorkArea(geo_models.Model):
    work_area_group = geo_models.ForeignKey(WorkAreaGroup, null=True, blank=True, on_delete=geo_models.SET_NULL)
    opportunity = geo_models.ForeignKey(Opportunity, on_delete=geo_models.CASCADE)
    slug = geo_models.SlugField(
        max_length=255,
        help_text=(
            "Unique identifier for the Work Area within an Opportunity. "
            "Automatically generated slugs must be unique per opportunity."
        ),
    )
    centroid = geo_models.PointField(
        srid=SRID, help_text="Centroid of the Work Area as a Point. Use (longitude, latitude) when assigning manually."
    )
    boundary = geo_models.PolygonField(srid=SRID)
    ward = geo_models.SlugField(max_length=255)
    building_count = geo_models.PositiveIntegerField(default=0)
    expected_visit_count = geo_models.PositiveIntegerField(default=0)
    status = geo_models.CharField(
        max_length=50,
        choices=WorkAreaStatus.choices,
        default=WorkAreaStatus.UNASSIGNED,
    )
    inaccessibility_reason = geo_models.TextField(blank=True, default="")
    case_id = geo_models.UUIDField(null=True, blank=True, unique=True)
    case_properties = geo_models.JSONField(default=dict, null=True, blank=True)
```

- [ ] **Step 2: Generate migration**

Run: `./manage.py makemigrations microplanning`

Expected: A new migration file is created with the `inaccessibility_reason` field addition and updated pghistory event model.

- [ ] **Step 3: Apply migration**

Run: `./manage.py migrate`

Expected: Migration applies successfully.

- [ ] **Step 4: Commit**

```bash
git add commcare_connect/microplanning/models.py commcare_connect/microplanning/migrations/
git commit -m "add inaccessibility_reason field to WorkArea, expand pghistory tracking"
```

---

### Task 2: Add XForm template and stub factory for `work_area_update`

**Files:**
- Modify: `commcare_connect/form_receiver/tests/xforms.py`

- [ ] **Step 1: Add XML template and stub factory**

In `commcare_connect/form_receiver/tests/xforms.py`, add the template after `DELIVER_UNIT_XML_TEMPLATE` (after line 85):

```python
WORK_AREA_UPDATE_XML_TEMPLATE = (
    """<data>
<work_area_update xmlns="%s" id="{id}">
    <work_area_id>{work_area_id}</work_area_id>
    <status>{status}</status>
    <reason>{reason}</reason>
</work_area_update>
</data>"""
    % CCC_LEARN_XMLNS
)
```

Add the stub factory at the end of the file (after `DeliverUnitStubFactory`):

```python
class WorkAreaUpdateStubFactory(factory.StubFactory):
    id = factory.Faker("slug")
    work_area_id = factory.Faker("uuid4")
    status = "request_for_inaccessible"
    reason = factory.Faker("sentence")

    @factory.lazy_attribute
    def json(self):
        xml = WORK_AREA_UPDATE_XML_TEMPLATE.format(
            id=self.id,
            work_area_id=self.work_area_id,
            status=self.status,
            reason=self.reason,
        )
        _, block = xml2json(xml)
        return block
```

- [ ] **Step 2: Commit**

```bash
git add commcare_connect/form_receiver/tests/xforms.py
git commit -m "add work_area_update XForm test template and stub factory"
```

---

### Task 3: Write failing tests for `process_work_area_update`

**Files:**
- Modify: `commcare_connect/form_receiver/tests/test_receiver_integration.py`

- [ ] **Step 1: Write the tests**

Add the following imports at the top of `test_receiver_integration.py`:

```python
from commcare_connect.form_receiver.tests.xforms import WorkAreaUpdateStubFactory
from commcare_connect.microplanning.models import WorkAreaStatus
from commcare_connect.microplanning.tests.factories import WorkAreaGroupFactory
```

Add the following tests at the end of the file. Each test follows the existing pattern of using `make_request` to submit a form and asserting the result.

**Note on worker assignment validation:** At the time this plan was written, WorkArea assignment goes through `OpportunityAccess → WorkAreaGroup → WorkArea`. A pending change may assign WorkAreas directly to users. At implementation time, check the current WorkArea model for a direct user/access FK. If it exists, use that for assignment validation instead of the WorkAreaGroup path. Adjust test setup accordingly.

```python
@pytest.mark.django_db
def test_work_area_update_inaccessible(
    mobile_user_with_connect_link: User, api_client: APIClient, opportunity: Opportunity
):
    access = OpportunityAccess.objects.get(user=mobile_user_with_connect_link, opportunity=opportunity)
    work_area_group = WorkAreaGroupFactory(opportunity=opportunity, opportunity_access=access)
    work_area = WorkAreaFactory(
        opportunity=opportunity, work_area_group=work_area_group, status=WorkAreaStatus.NOT_STARTED
    )
    oauth_application = opportunity.hq_server.oauth_application
    stub = WorkAreaUpdateStubFactory(work_area_id=work_area.case_id, status="request_for_inaccessible", reason="Flooding")
    form_json = get_form_json(
        form_block={**stub.json},
        domain=opportunity.deliver_app.cc_domain,
        app_id=opportunity.deliver_app.cc_app_id,
    )

    make_request(api_client, form_json, mobile_user_with_connect_link, oauth_application=oauth_application)

    work_area.refresh_from_db()
    assert work_area.status == WorkAreaStatus.REQUEST_FOR_INACCESSIBLE
    assert work_area.inaccessibility_reason == "Flooding"


@pytest.mark.django_db
def test_work_area_update_wrong_status(
    mobile_user_with_connect_link: User, api_client: APIClient, opportunity: Opportunity
):
    access = OpportunityAccess.objects.get(user=mobile_user_with_connect_link, opportunity=opportunity)
    work_area_group = WorkAreaGroupFactory(opportunity=opportunity, opportunity_access=access)
    work_area = WorkAreaFactory(
        opportunity=opportunity, work_area_group=work_area_group, status=WorkAreaStatus.VISITED
    )
    oauth_application = opportunity.hq_server.oauth_application
    stub = WorkAreaUpdateStubFactory(work_area_id=work_area.case_id, status="request_for_inaccessible", reason="Flooding")
    form_json = get_form_json(
        form_block={**stub.json},
        domain=opportunity.deliver_app.cc_domain,
        app_id=opportunity.deliver_app.cc_app_id,
    )

    make_request(
        api_client, form_json, mobile_user_with_connect_link, expected_status_code=400, oauth_application=oauth_application
    )
    work_area.refresh_from_db()
    assert work_area.status == WorkAreaStatus.VISITED


@pytest.mark.django_db
def test_work_area_update_unassigned_worker(
    mobile_user_with_connect_link: User, api_client: APIClient, opportunity: Opportunity
):
    # WorkArea exists but is not assigned to this worker (no matching WorkAreaGroup)
    work_area = WorkAreaFactory(opportunity=opportunity, status=WorkAreaStatus.NOT_STARTED)
    oauth_application = opportunity.hq_server.oauth_application
    stub = WorkAreaUpdateStubFactory(work_area_id=work_area.case_id, status="request_for_inaccessible", reason="Flooding")
    form_json = get_form_json(
        form_block={**stub.json},
        domain=opportunity.deliver_app.cc_domain,
        app_id=opportunity.deliver_app.cc_app_id,
    )

    make_request(
        api_client, form_json, mobile_user_with_connect_link, expected_status_code=400, oauth_application=oauth_application
    )
    work_area.refresh_from_db()
    assert work_area.status == WorkAreaStatus.NOT_STARTED


@pytest.mark.django_db
def test_work_area_update_invalid_uuid(
    mobile_user_with_connect_link: User, api_client: APIClient, opportunity: Opportunity
):
    oauth_application = opportunity.hq_server.oauth_application
    stub = WorkAreaUpdateStubFactory(work_area_id="not-a-uuid", status="request_for_inaccessible", reason="Flooding")
    form_json = get_form_json(
        form_block={**stub.json},
        domain=opportunity.deliver_app.cc_domain,
        app_id=opportunity.deliver_app.cc_app_id,
    )

    make_request(
        api_client, form_json, mobile_user_with_connect_link, expected_status_code=400, oauth_application=oauth_application
    )


@pytest.mark.django_db
def test_work_area_update_nonexistent_work_area(
    mobile_user_with_connect_link: User, api_client: APIClient, opportunity: Opportunity
):
    oauth_application = opportunity.hq_server.oauth_application
    stub = WorkAreaUpdateStubFactory(work_area_id=str(uuid4()), status="request_for_inaccessible", reason="Flooding")
    form_json = get_form_json(
        form_block={**stub.json},
        domain=opportunity.deliver_app.cc_domain,
        app_id=opportunity.deliver_app.cc_app_id,
    )

    make_request(
        api_client, form_json, mobile_user_with_connect_link, expected_status_code=400, oauth_application=oauth_application
    )


@pytest.mark.django_db
def test_work_area_update_missing_reason(
    mobile_user_with_connect_link: User, api_client: APIClient, opportunity: Opportunity
):
    access = OpportunityAccess.objects.get(user=mobile_user_with_connect_link, opportunity=opportunity)
    work_area_group = WorkAreaGroupFactory(opportunity=opportunity, opportunity_access=access)
    work_area = WorkAreaFactory(
        opportunity=opportunity, work_area_group=work_area_group, status=WorkAreaStatus.NOT_STARTED
    )
    oauth_application = opportunity.hq_server.oauth_application
    stub = WorkAreaUpdateStubFactory(work_area_id=work_area.case_id, status="request_for_inaccessible", reason="")
    form_json = get_form_json(
        form_block={**stub.json},
        domain=opportunity.deliver_app.cc_domain,
        app_id=opportunity.deliver_app.cc_app_id,
    )

    make_request(
        api_client, form_json, mobile_user_with_connect_link, expected_status_code=400, oauth_application=oauth_application
    )
    work_area.refresh_from_db()
    assert work_area.status == WorkAreaStatus.NOT_STARTED
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest commcare_connect/form_receiver/tests/test_receiver_integration.py -k "test_work_area_update" -v`

Expected: All 6 tests FAIL (the processing logic doesn't exist yet, so the happy path test will fail on assertion and the error tests may pass or fail depending on how missing blocks are handled).

- [ ] **Step 3: Commit**

```bash
git add commcare_connect/form_receiver/tests/test_receiver_integration.py
git commit -m "add failing tests for work area status update via XForm"
```

---

### Task 4: Implement `process_work_area_update` and wire it into `process_deliver_form`

**Files:**
- Modify: `commcare_connect/form_receiver/processor.py`

- [ ] **Step 1: Add the JSONPath, transitions dict, and import**

At the top of `commcare_connect/form_receiver/processor.py`, add `pghistory` to imports:

```python
import pghistory
```

Add the import for `WorkAreaStatus` alongside the existing `WorkArea` import (line 17):

```python
from commcare_connect.microplanning.models import WorkArea, WorkAreaStatus
```

After the existing JSONPath definitions (after line 49), add:

```python
WORK_AREA_UPDATE_JSONPATH = parse("$..work_area_update")

ALLOWED_WORK_AREA_STATUS_TRANSITIONS = {
    WorkAreaStatus.NOT_STARTED: {WorkAreaStatus.REQUEST_FOR_INACCESSIBLE},
}

# Statuses that require a reason in the work_area_update block
WORK_AREA_STATUS_REASON_REQUIRED = {WorkAreaStatus.REQUEST_FOR_INACCESSIBLE}
```

- [ ] **Step 2: Add `process_work_area_update` function**

Add the function after `process_deliver_form` (after line 264), before `clean_form_submission`:

```python
def process_work_area_update(user: User, xform: XForm, opportunity: Opportunity, block: dict):
    work_area_case_id = block.get("work_area_id")
    if not work_area_case_id or not is_a_uuid(work_area_case_id):
        raise ProcessingError(f"Invalid work area case id specified: {work_area_case_id}")

    try:
        work_area = WorkArea.objects.get(case_id=work_area_case_id, opportunity=opportunity)
    except WorkArea.DoesNotExist:
        raise ProcessingError("Work area not found")

    try:
        access = OpportunityAccess.objects.get(opportunity=opportunity, user=user)
    except OpportunityAccess.DoesNotExist:
        raise ProcessingError(f"User does not have access to opportunity {opportunity.name}")

    if not work_area.work_area_group or work_area.work_area_group.opportunity_access_id != access.id:
        raise ProcessingError("User is not assigned to this work area")

    requested_status = block.get("status", "").upper()
    try:
        new_status = WorkAreaStatus(requested_status)
    except ValueError:
        raise ProcessingError(f"Invalid work area status: {requested_status}")

    allowed = ALLOWED_WORK_AREA_STATUS_TRANSITIONS.get(work_area.status, set())
    if new_status not in allowed:
        raise ProcessingError(
            f"Cannot transition work area from {work_area.status} to {new_status}"
        )

    reason = block.get("reason", "")
    if new_status in WORK_AREA_STATUS_REASON_REQUIRED and not reason:
        raise ProcessingError(f"Reason is required for status {new_status}")

    work_area.status = new_status
    if new_status == WorkAreaStatus.REQUEST_FOR_INACCESSIBLE:
        work_area.inaccessibility_reason = reason

    with pghistory.context(username=user.username):
        work_area.save(update_fields=["status", "inaccessibility_reason"])
```

**Note on worker assignment validation:** The code above validates assignment via `work_area.work_area_group.opportunity_access_id != access.id`. At implementation time, check whether WorkArea has gained a direct user/access FK. If so, replace the WorkAreaGroup-based check with the direct FK check.

- [ ] **Step 3: Wire into `process_deliver_form`**

In `process_deliver_form` (currently lines 252-263), add the work area update processing after the existing task processing:

```python
def process_deliver_form(user, xform: XForm, app: CommCareApp, opportunity: Opportunity):
    deliver_matches = [
        match.value for match in DELIVER_UNIT_JSONPATH.find(xform.form) if match.value["@xmlns"] == CCC_LEARN_XMLNS
    ]
    for deliver_unit_block in deliver_matches:
        process_deliver_unit(user, xform, app, opportunity, deliver_unit_block)

    task_matches = [
        match.value for match in TASK_MODULE_JSONPATH.find(xform.form) if match.value["@xmlns"] == CCC_LEARN_XMLNS
    ]
    if task_matches:
        process_task_modules(user, xform, app, opportunity, task_matches)

    work_area_update_matches = [
        match.value
        for match in WORK_AREA_UPDATE_JSONPATH.find(xform.form)
        if match.value["@xmlns"] == CCC_LEARN_XMLNS
    ]
    for block in work_area_update_matches:
        process_work_area_update(user, xform, opportunity, block)
```

- [ ] **Step 4: Run all work area update tests**

Run: `pytest commcare_connect/form_receiver/tests/test_receiver_integration.py -k "test_work_area_update" -v`

Expected: All 6 tests PASS.

- [ ] **Step 5: Run the full test suite for the form_receiver app**

Run: `pytest commcare_connect/form_receiver/ -v`

Expected: All existing tests still pass, no regressions.

- [ ] **Step 6: Commit**

```bash
git add commcare_connect/form_receiver/processor.py
git commit -m "implement work area status update processing for deliver forms"
```

---

### Task 5: Run pre-commit and full test suite

- [ ] **Step 1: Run pre-commit**

Run: `pre-commit run -a`

Expected: All checks pass. If black/isort makes changes, stage and commit them.

- [ ] **Step 2: Run full test suite**

Run: `pytest`

Expected: All tests pass, no regressions.

- [ ] **Step 3: Fix and commit any issues**

If pre-commit or tests surface issues, fix them and commit:

```bash
git add -u
git commit -m "fix linting issues"
```
