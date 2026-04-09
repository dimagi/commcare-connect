# Work Area Status Update via XForm

## Summary

Allow XForms submitted from CommCare deliver apps to communicate WorkArea status updates to Connect. The first supported update is marking a WorkArea as inaccessible (`REQUEST_FOR_INACCESSIBLE`), but the design is generic and extensible to future status transitions.

## Background

Workers in the field may encounter WorkAreas they cannot visit (e.g., flooding, road closures). Currently there is no mechanism for the deliver form to signal this back to Connect. The `WorkAreaStatus.REQUEST_FOR_INACCESSIBLE` status already exists in the model but has no intake path from XForms.

## XForm Structure

A new top-level block `work_area_update` in the deliver form submission:

```xml
<work_area_update xmlns="http://commcareconnect.com/data/v1/learn" id="{form_id}">
    <work_area_id>{work_area_case_id}</work_area_id>
    <status>request_for_inaccessible</status>
    <reason>Text explanation from the worker</reason>
</work_area_update>
```

- Uses the existing `CCC_LEARN_XMLNS` namespace (`http://commcareconnect.com/data/v1/learn`)
- `work_area_id`: UUID matching `WorkArea.case_id`
- `status`: the requested new status (lowercase, maps to `WorkAreaStatus` values)
- `reason`: free-text explanation (optional for some statuses, required for `request_for_inaccessible`)

This block can coexist alongside `deliver` and `task` blocks in the same form submission, or appear on its own.

## Model Changes

### WorkArea (`commcare_connect/microplanning/models.py`)

Add a new field:

```python
inaccessibility_reason = geo_models.TextField(blank=True, default="")
```

Expand pghistory tracking to include the new field and `status`:

```python
@pghistory.track(fields=["expected_visit_count", "work_area_group", "status", "inaccessibility_reason"])
```

A database migration is required for the new field and updated pghistory event model.

## Processing Pipeline

### Location

All changes in `commcare_connect/form_receiver/processor.py`.

### New JSONPath

```python
WORK_AREA_UPDATE_JSONPATH = parse("$..work_area_update")
```

### Integration Point

In `process_deliver_form`, after existing deliver and task processing:

```python
work_area_update_matches = [
    match.value for match in WORK_AREA_UPDATE_JSONPATH.find(xform.form)
    if match.value["@xmlns"] == CCC_LEARN_XMLNS
]
for block in work_area_update_matches:
    process_work_area_update(user, xform, opportunity, block)
```

### `process_work_area_update` Function

1. Extract `work_area_id`, `status`, and `reason` from the block
2. Validate `work_area_id` is a valid UUID
3. Look up `WorkArea` by `case_id` and `opportunity`
4. Validate the worker is assigned to the WorkArea. **Note:** at time of writing, assignment is via `OpportunityAccess` → `WorkAreaGroup` → `WorkArea`. A pending change may assign WorkAreas directly to users. At implementation time, check the current assignment model and validate accordingly.
5. Validate the status transition is allowed per `ALLOWED_WORK_AREA_STATUS_TRANSITIONS`
6. Apply status-specific logic (e.g., for `request_for_inaccessible`: require `reason`, set `inaccessibility_reason`)
7. Update WorkArea status
8. Save within `pghistory.context(username=user.username)`

### Allowed Status Transitions

A Python dict defining valid transitions:

```python
ALLOWED_WORK_AREA_STATUS_TRANSITIONS = {
    WorkAreaStatus.NOT_STARTED: {WorkAreaStatus.REQUEST_FOR_INACCESSIBLE},
}
```

To support future status updates, add entries to this dict and any status-specific field logic in `process_work_area_update`.

## pghistory Audit Trail

The form receiver is not an authenticated Django request path, so pghistory context must be set explicitly. The mobile user is available via `get_user(xform)`:

```python
with pghistory.context(username=user.username):
    work_area.save(update_fields=["status", "inaccessibility_reason"])
```

This ensures the audit trail captures which worker requested the status change.

## No UserVisit Created

An inaccessibility report is a status signal, not a visit. No `UserVisit` record is created. No `CompletedWork` is affected.

## Error Handling

All validation failures raise `ProcessingError`, consistent with existing deliver unit processing:

- Invalid or missing `work_area_id` UUID
- WorkArea not found for the given `case_id` + `opportunity`
- Worker not assigned to the WorkArea
- Invalid status transition (current status does not permit the requested new status)
- Missing `reason` when required (for `request_for_inaccessible`)

## Testing

### Test XForm Template

New `WORK_AREA_UPDATE_XML_TEMPLATE` in the test xforms module:

```xml
<data>
<work_area_update xmlns="http://commcareconnect.com/data/v1/learn" id="{id}">
    <work_area_id>{work_area_id}</work_area_id>
    <status>{status}</status>
    <reason>{reason}</reason>
</work_area_update>
</data>
```

### Test Cases

- **Happy path**: assigned worker, WorkArea in `NOT_STARTED` status, sends `request_for_inaccessible` with reason → WorkArea status updated to `REQUEST_FOR_INACCESSIBLE`, `inaccessibility_reason` saved, pghistory event recorded with worker username
- **Wrong status**: WorkArea in `VISITED` status → `ProcessingError`
- **Unassigned worker**: worker not assigned to the WorkArea (check current assignment model — may be via WorkAreaGroup or direct assignment) → `ProcessingError`
- **Invalid UUID**: malformed `work_area_id` → `ProcessingError`
- **WorkArea not found**: valid UUID but no matching WorkArea → `ProcessingError`
- **Missing reason**: `request_for_inaccessible` without `reason` → `ProcessingError`
