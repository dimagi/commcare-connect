# Audit history (django-pghistory)

Model changes are recorded with [django-pghistory](https://django-pghistory.readthedocs.io/). A tracked model gets
Postgres triggers that copy each insert, update or delete into an **event table**, together with a **context**
saying who made the change. Because the triggers run in the database, every write path is recorded, including
`QuerySet.update()`, `bulk_create()`, the Django admin and the shell, not just `Model.save()`.

## How it is wired up

- `pghistory` and `pgtrigger` are in `INSTALLED_APPS` (`config/settings/base.py`).
- `CustomPGHistoryMiddleware` (`commcare_connect/utils/middleware.py`) opens a context for every request and adds
  the user's `username` and `user_email` to it. They are stored as text so the record still names the user after
  the user is deleted. Read them from `event.pgh_context.metadata`.
- Outside a request there is no context, so events are recorded with no actor. Wherever a change is made on
  someone's behalf outside a request (Celery tasks, form processing, management commands), wrap it:

  ```python
  with pghistory.context(username=user.username, user_email=user.email):
      work_area.save(update_fields=["status"])
  ```

  Extra keys are allowed and are stored alongside, e.g. `reason=` in `microplanning/helpers.py`.

## What is tracked

| Model | What is recorded | Event model | Relation on the record |
|---|---|---|---|
| `Organization` | Every field except the BaseModel audit fields; insert, update, delete | `OrganizationEvent` | `events` |
| `Contact` | Every field except the BaseModel audit fields; insert, update, delete | `ContactEvent` | `events` |
| `OrganizationCountry`, `OrganizationPrimarySector` (through models) | Rows added and removed | `OrganizationCountryEvent`, `OrganizationPrimarySectorEvent` | `events` |
| `Program` | `funder` | `ProgramFunderEvent` | `funder_events` |
| `ProgramWatcher` (through model) | Rows added and removed | `ProgramWatcherEvent` | `events` |
| `Opportunity` | `active`; `supervising_organization` | `OpportunityActiveEvent`; `OpportunitySupervisingOrganizationEvent` | `active_events`; `supervising_organization_events` |
| `AssignedTask` | `due_date` | `AssignedTaskDueDateEvent` | `due_date_events` |
| `PaymentInvoice` | `status` | `PaymentInvoiceStatusEvent` | `status_events` |
| `WorkArea` | Status, assignment and expected-visit fields | `WorkAreaExpectedVisitCountWorkAreaGroupStatusOpportunityAccessExcludedReasonEvent` | `expected_visit_count_work_area_group_status_opportunity_access_excluded_reason_events` |

The decorators on the models are the source of truth; check them before relying on this table.

## Adding tracking to a model

1. **Choose what to record.**
   - To answer "who changed this, and when" for one or two fields, track only those fields:
     `@pghistory.track(fields=["status"])`.
   - For a full history of a record, snapshot the whole row but leave out BaseModel's audit fields, which the
     event already records as its own timestamp and context:
     `@pghistory.track(pghistory.InsertEvent(), pghistory.UpdateEvent(), pghistory.DeleteEvent(),
     exclude=UNTRACKED_MODEL_FIELDS)` (see `organization/models.py`).
   - An update event fires only when a tracked field changes, so leaving out noisy fields keeps the history clean.
   - Add `pghistory.DeleteEvent()` when the record can be deleted and its final state matters (e.g. organizations
     removed by a merge). It is not recorded by default.
2. **Mind the names pghistory generates.**
   - The event model is named after the model and the tracked fields (`OpportunityActiveEvent`), and the
     relation on the record after the fields (`active_events`). A full snapshot is `<Model>Event` / `events`.
   - So don't add a field to an existing field tracker: it renames the event model and its relation, which code
     may refer to. Add a separate tracker instead, as `Opportunity` does for `supervising_organization`.
   - Tracking several named fields makes a very long name (see `WorkArea` above). For a new tracker, pass
     `model_name="…Event"` and `obj_field=pghistory.ObjForeignKey(related_name="…")` to choose short, stable names.
   - Event labels (`insert`, `update`, …) must be unique among one model's trackers. A second tracker on the same
     model needs explicit labels, e.g. `pghistory.UpdateEvent("supervising_organization_update")`.
3. **Run `makemigrations`.** It creates the event table and `AddTrigger` operations. Don't edit the trigger SQL by
   hand; change the decorator and regenerate. Adding, renaming or removing a field on a tracked model also changes
   its triggers, so expect them in that migration.
4. **Many-to-many fields need an explicit through model.** pghistory can't install triggers on Django's
   auto-created through tables. Declare the through model explicitly, point it at the existing table with
   `Meta.db_table`, match the existing unique constraint with `unique_together`, and track it with
   `pghistory.InsertEvent(), pghistory.DeleteEvent()`. Make the migration state-only with
   `SeparateDatabaseAndState`, so the existing table and rows are adopted rather than recreated. See
   `ProgramWatcher` (`program/migrations/0018_…`) and `OrganizationCountry`
   (`organization/migrations/0024_…`). Django admin hides many-to-many fields with an explicit through model, so
   restore the form field if the admin edits it (`OrganizationAdmin.formfield_for_manytomany`).
5. **If the event model has a foreign key to `Organization`**, add it to `SKIPPED_RELATIONS` in
   `organization/merge.py`. `test_merge.py` fails until every relation to `Organization` is classified, and audit
   rows must keep pointing at the merged-away organization rather than being rewritten.
6. **Expose it** in the admin (below) or wherever the history is read.

## Reading events

- Query through the record's relation (`organization.events`, `invoice.status_events`) or the event model
  (`OrganizationEvent.objects.filter(pgh_obj=organization)`), ordered by `pgh_id` or `pgh_created_at`.
- `select_related("pgh_context")` to show who made each change; the actor is in `pgh_context.metadata`.
- **Event foreign keys have no database constraint** (`db_constraint=False`), so the row an event points at may be
  gone, which is the point of an audit trail. Don't `select_related()` them: they aren't nullable, so Django uses an
  INNER JOIN and silently drops every event whose record was deleted. Use `prefetch_related()`, and expect a
  missing related object (`ObjectDoesNotExist`) when displaying one.
- Each event is a snapshot of the tracked fields after the change, not a diff. To show what changed, compare an
  event with the previous one for the same record (`changed_fields_summary` in `utils/admin.py`).

## Admin

- Subclass `AuditEventAdmin` (`utils/admin.py`). It makes events read-only, orders newest first, and adds
  `changed_by` and `changed_by_email` columns read from the context.
- Use `audited_object("pgh_obj", _("…"))` for a column that names the record, so a deleted record shows a dash
  instead of an error.
- Load related records with `prefetch_related()` in `get_queryset()`, as `OrganizationAuditEventAdmin` does.
- Add `ChangedFieldsMixin` to a full-snapshot event admin to show a "Changes" field on each event's page.

## Testing

- Tests run the real migrations, so the triggers exist and events are written as in production.
- Assert on the record's relation, e.g. `organization.events.latest("pgh_id").pgh_label == "update"`.
- To test the actor, wrap the change in `pghistory.context(username=..., user_email=...)`, or go through a view so
  the middleware sets it.
