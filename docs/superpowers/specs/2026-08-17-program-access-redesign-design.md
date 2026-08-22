# Program access redesign: funder, watchers, and supervising organization

## Goal

Expose three organization relationships that already exist in the data model but have no
user interface:

| Relationship               | Level       | Cardinality | Settable                     |
| -------------------------- | ----------- | ----------- | ---------------------------- |
| funder                     | program     | one         | at creation only             |
| watchers                   | program     | many        | at creation and edit         |
| supervising organization   | opportunity | one         | at creation and edit, always |

All of it sits behind the `ENABLE_PROGRAM_ACCESS_REDESIGN` waffle switch, an umbrella
gate for the wider program access redesign that later changes will also hang off.

Changes to all three relationships are recorded in an audit trail, which is *not* behind
the switch. See Audit trail.

## Background

Every field in this spec already exists on a model and is already read by the permission
layer. The user-facing part of this change is therefore form and UI only; the migrations it
adds create audit tables rather than columns on existing ones.

### Funder and watchers

`Program.funder` and `Program.watchers` exist on the model (migration
`0016_program_funder_program_watchers`). In `commcare_connect/program/utils.py`:

- the funder gets `AccessLevel.MANAGE` on the program, the same level as the owning
  organization;
- watchers get `AccessLevel.VIEW`.

Neither is exposed in `ProgramForm`, so before this change they could only be set in the
Django admin — and `Organization.funder`, the flag that makes an organization eligible to be
picked as one, could not be set there at all: `OrganizationAdmin` listed and filtered on it
but its form omitted the field. That form is widened here as well, so marking an
organization as a funder no longer needs a shell.

Because the funder is granted MANAGE, and because it is selectable only at creation,
setting it is effectively a permanent grant of full management rights over the program.
That is the intended behaviour, but it is the reason the field stays visible after
creation rather than disappearing.

### Supervising organization

`Opportunity.supervising_organization` exists on the model (migration
`0140_opportunity_supervising_organization`, which added it nullable, backfilled it from
the program's owning organization, then enforced non-null). It is read by
`opportunity_manage_org_ids` in `program/utils.py`, which grants its holder PM-level
oversight of that one opportunity.

`Opportunity.save()` currently defaults it to `self.program.organization_id` whenever it
is unset, under the comment *"Until the UI allows setting a supervising organization"*.
This spec is what that comment anticipates. The fallback is kept regardless — see
"Supervising organization" under Design.

## Scope

No changes to `utils.py` or any permission check — `org_program_role` and
`opportunity_manage_org_ids` already read all three fields, so a program or opportunity
saved through these forms immediately grants the new access levels.

No column is added to any table. The three relationships already exist as model fields;
the migrations this change does add exist only to record their history, and are described
under Audit trail below.

Four forms are touched:

- `commcare_connect/program/forms.py` — `ProgramForm` gains funder and watchers.
- `commcare_connect/opportunity/forms.py` — `OpportunityInitForm` gains the supervising
  organization. `OpportunityInitUpdateForm` subclasses it and so inherits the field.
  `OpportunityChangeForm` gains it too, behind a permission check.
- `commcare_connect/users/forms.py` — the admin form behind `OrganizationAdmin` gains
  `funder`, so an organization can be marked as one without a shell. Renamed from
  `OrganizationCreationForm` to `AdminOrganizationForm`: it backs both the add and change
  views, and the old name collided conceptually with the non-admin `OrganizationChangeForm`
  in `organization/forms.py`. This one is not behind the switch — it is staff-only.

Both opportunity forms are needed because they sit on different pages.
`OpportunityInitUpdateForm` is only reachable from the creation wizard's step navigation,
so on its own it leaves the field effectively uneditable once an opportunity exists.
`OpportunityChangeForm` backs the Edit page linked from the opportunity menu, which is
where a program manager would look. See "Editing an existing opportunity" below for the
permission check that difference forces.

One model gains a declaration but no column: `Program.watchers` is repointed at an
explicit `ProgramWatcher` through model so its rows can be audited. See Audit trail.

## Feature switch

This change ships behind a waffle switch introduced here but **not owned by it**:

```python
ENABLE_PROGRAM_ACCESS_REDESIGN = "enable_program_access_redesign"
```

added to `commcare_connect/flags/switch_names.py`.

The switch is the umbrella gate for the wider program access redesign. The three fields
in this spec are the first changes behind it; further changes will be added to the same
switch rather than introducing new ones, so the redesign can be rolled out and rolled
back as a single unit. Two consequences for anyone implementing against it:

- Do not rename or repurpose the switch for a funder/watcher/supervisor-specific
  meaning. Its scope is the redesign, not these forms.
- Later work will add its own `switch_is_active(ENABLE_PROGRAM_ACCESS_REDESIGN)` call
  sites. Keep each one local to the behaviour it gates rather than threading a single
  computed boolean through unrelated code, so call sites can be removed independently
  when the switch is eventually retired.

For this change specifically:

- **Off** — all three fields are removed from their forms entirely, popped from
  `self.fields` and absent from the layout, so none can be rendered or submitted. This
  holds on create and on edit, and regardless of what the underlying records already
  hold. Opportunities keep getting their supervising organization from the
  `Opportunity.save()` fallback, exactly as today.
- **On** — the fields appear as described below.

Gating follows the existing pattern in `commcare_connect/opportunity/forms.py`: a
`layout_fields` list that is conditionally appended to.

On `ProgramForm`, no extra guard is needed in `save()`. Popping a field from
`self.fields` is by itself sufficient to make a crafted POST inert, because
`construct_instance` and `_save_m2m` both act only on keys present in `cleaned_data` — a
field that is not on the form never reaches it. The same mechanism is why an existing
program's funder and watchers survive a save made while the switch is off: they are
skipped, not cleared. Tests 8 and 9 below pin both halves of that behaviour.

**The opportunity form is different, and needs an explicit assignment.**
`supervising_organization` is not listed in `OpportunityInitForm.Meta.fields`; the field
is attached to `self.fields` at runtime instead. `construct_instance` consults
`Meta.fields`, not `self.fields`, so it never copies the cleaned value onto the instance.
The existing `organization` field has exactly this shape and is assigned by hand in
`save()`, and the supervising organization follows that precedent:

```python
if "supervising_organization" in self.cleaned_data:
    opportunity.supervising_organization = self.cleaned_data["supervising_organization"]
```

The `in self.cleaned_data` test is what preserves the switch-off behaviour: with the
field absent, the key is missing, the assignment is skipped, and `Opportunity.save()`
falls back to the program's organization on create or leaves the stored value alone on
edit. No `switch_is_active` call is needed at the save site.

This must be done in **all three** save methods that can persist the field:
`OpportunityInitForm.save()`, `OpportunityInitUpdateForm.save()`, and
`OpportunityChangeForm.save()`. `OpportunityInitUpdateForm` overrides its parent without
calling `super()`, so an assignment added only to the parent silently does nothing on edit,
and `OpportunityChangeForm` is a separate class entirely. A form that renders the field but
omits this assignment accepts the change and discards it.

## Design

### Queryset helpers

Two module-level functions in `commcare_connect/program/forms.py`, kept out of the form
class so they can be unit-tested directly rather than through an HTTP response:

```python
def funder_organizations(program_organization):
    return Organization.objects.filter(funder=True).exclude(pk=program_organization.pk).order_by("name")


def watcher_organizations(program_organization, funder):
    excluded_ids = {program_organization.pk}
    if funder:
        excluded_ids.add(funder.pk)
    return Organization.objects.exclude(pk__in=excluded_ids).order_by("name")
```

- **Funder options**: organizations with `funder=True`, excluding the program's own
  organization.
- **Watcher options**: all organizations, excluding the program's own organization and
  the program's funder. Both already outrank watcher status — `org_program_role` returns
  MANAGE for them before the watcher check runs — so offering them would allow a
  selection that has no effect.

### Form fields

Both fields are added to `ProgramForm.Meta.fields` and to the crispy `Layout` in a new
`Row` following `delivery_type`.

- `funder`: `ModelChoiceField`, `required=False`, `empty_label="Select a funder"`, widget
  `forms.Select(attrs={"data-tomselect": "1"})` — matching the existing `currency` and
  `country` fields. Optional, because the model field is `null=True, blank=True` and a
  program may have no funder.
- `watchers`: `ModelMultipleChoiceField`, `required=False`, widget
  `forms.SelectMultiple(attrs={"data-tomselect": "1"})`. The shared initializer in
  `static/js/tomselect.js` already handles multi-selects and adds the `remove_button`
  plugin, so no new JavaScript is needed. This mirrors `commcare_connect/flags/forms.py`.

### Create versus edit

Branching happens in `ProgramForm.__init__`, keyed off `self.instance.pk`.

| Field    | Create              | Edit                          |
| -------- | ------------------- | ----------------------------- |
| funder   | editable select     | visible, disabled             |
| watchers | editable multiselect | editable multiselect         |

On edit, `self.fields["funder"].disabled = True`. Django ignores submitted POST data for
a disabled field and falls back to the instance value, so a tampered request cannot
reassign the funder. This is the enforcement mechanism, not merely a visual lock — no
separate `clean_funder` guard is required.

The funder widget drops the `data-tomselect` attribute on edit so it renders as a plain
locked control rather than an inert tom-select box.

### Validation

On the create form both dropdowns list overlapping organizations, so a user can select
the same organization as funder and as watcher. `clean()` rejects this with an error
attached to `watchers`:

> An organization cannot be both the funder and a watcher.

The alternative — silently discarding the funder from the watcher set — was rejected in
favour of telling the user what happened.

This check is only reachable on create; on edit the funder is already excluded from the
watcher queryset.

### Supervising organization

Added to `OpportunityInitForm.__init__` alongside the existing `organization` ("Network
Manager Workspace") field, which is built from a closely related queryset at
`opportunity/forms.py:532-541`. `OpportunityInitUpdateForm` subclasses
`OpportunityInitForm`, so it inherits the field and only needs its `initial` set from the
instance, in the same block that already sets `self.fields["organization"].initial`.

**Options** — a module-level helper in `commcare_connect/opportunity/forms.py`, following
the same testable-outside-the-form rule as the program helpers:

```python
def supervising_organizations(program):
    """Organizations eligible to supervise an opportunity in `program`.

    The program's own organization, its funder, and every organization with an accepted
    ProgramApplication for the program. Eligibility is evaluated on each render, so an
    organization that loses its accepted application stops being offered.
    """
    eligible = Q(pk=program.organization_id)
    if program.funder_id:
        eligible |= Q(pk=program.funder_id)
    eligible |= Q(
        programapplication__program=program,
        programapplication__status=ProgramApplicationStatus.ACCEPTED,
    )
    return Organization.objects.filter(eligible).distinct().order_by("name")
```

**Reassignment revokes the previous organization's oversight.** `opportunity_manage_org_ids`
reads `supervising_organization_id` live on every permission check, so the moment a new
supervisor is saved the previous organization stops passing
`request_supervises_opportunity` for that opportunity. Nothing caches the old value.

**Stale supervisors are not grandfathered.** The queryset offers only currently-eligible
organizations, with no allowance for the value already stored on the instance. If an
organization loses its accepted application while still supervising an opportunity, the
edit form's bound value is no longer a valid choice, so the form will not validate until
a program manager selects an eligible organization. This blocks unrelated edits on that
form, and that is intended: it forces the invalid state to be resolved rather than
carried forward.

**It supervises; it does not replace the opportunity's own organization.**
`Opportunity.organization` — the Network Manager workspace that delivers the work, chosen
through the "Network Manager Workspace" field on the same form — and
`Opportunity.supervising_organization` are separate roles that coexist:

| Field                      | Role                | Access path                                                    |
| -------------------------- | ------------------- | -------------------------------------------------------------- |
| `organization`             | delivers the work   | ordinary org-scoped access to its own opportunity               |
| `supervising_organization` | oversees the work   | `opportunity_manage_org_ids`, i.e. PM-level oversight           |

Note that `opportunity_manage_org_ids` does not include `opportunity.organization_id` at
all; the delivering organization's access comes from the normal `/a/<org_slug>/` scoping,
not from this set. So assigning a supervisor grants oversight to a second organization
and takes nothing away from the first — the Network Manager keeps every permission it had.

The two may also be the same organization, which is the common case: an accepted
applicant that delivers an opportunity can equally be the one that supervises it. Nothing
in the queryset or validation prevents that, and it should not.

**Field** — `ModelChoiceField`, `required=True` (the model column is non-null),
`label="Supervising Organization"`, widget `forms.Select(attrs={"data-tomselect": "1"})`.
On create, `initial` is `program.organization`, which reproduces today's implicit
default. On edit, `initial` is the instance's current value.

**Create versus edit** — no difference. The field is editable at every point in the
opportunity's life, including after Connect Workers have joined. It is deliberately *not*
added to `OpportunityInitUpdateForm._disabled_fields`, which locks `hq_server`,
`api_key`, and the learn/deliver apps once `OpportunityAccess` rows exist; those are
locked because changing them would invalidate existing worker data, which reassigning
oversight does not.

#### Editing an existing opportunity

The field must appear on `OpportunityChangeForm` as well, because that is the form behind
the Edit page linked from `opportunity_menu.html`. `OpportunityInitUpdateForm` is only
linked from `steps.html` in the creation wizard, so wiring the field there alone leaves it
unreachable in practice once an opportunity exists.

The two pages are guarded very differently, and that difference is the whole design
constraint here:

| View | Form | Guard | Who that admits |
| ---- | ---- | ----- | --------------- |
| `ManagedOpportunityInitUpdate` | `OpportunityInitUpdateForm` | `ProgramManagerMixin` | program org, funder, or the opportunity's supervisor |
| `OpportunityEdit` | `OpportunityChangeForm` | `org_member_required` | any member of the opportunity's own organization |

Adding the field to `OpportunityChangeForm` unguarded would therefore let the *delivering*
Network Manager organization reassign oversight of its own opportunity, removing the
program manager. Since `opportunity_manage_org_ids` reads the column live, that is a
privilege escalation, and the audit trail would record it without preventing it.

So on `OpportunityChangeForm` the field is gated on PM-level oversight as well as the
switch:

```python
def _can_edit_supervising_organization(self):
    if self.request is None or not switch_is_active(ENABLE_PROGRAM_ACCESS_REDESIGN):
        return False
    if not self.instance.pk or not self.instance.managed:
        return False
    return request_supervises_opportunity(self.request, self.instance)
```

`OpportunityEdit.get_form_kwargs` passes `request` in for this. Three details:

- **The `request is None` guard is required, not defensive.**
  `request_supervises_opportunity` dereferences `request.org`, and the form is constructed
  without a request in several existing tests.
- **`managed` is checked** because a supervising organization is a program concept; on a
  standalone opportunity the eligible-organization list would be meaningless.
- **Hiding the field is not the control.** Withholding it from the layout only affects
  rendering; what actually prevents reassignment is that the field is never added to
  `self.fields`, so a crafted POST carrying `supervising_organization` finds no field to
  clean and the `in self.cleaned_data` check in `save()` skips the assignment.

**`Opportunity.save()` fallback is kept.** The existing default —
`supervising_organization_id = self.program.organization_id` when unset — stays, with its
now-stale comment updated to say the fallback covers non-form creation paths. The column
is non-null, so removing the fallback would make the API, factories, and any programmatic
`Opportunity.objects.create()` raise `IntegrityError`. With the switch off, this fallback
remains the only thing that sets the field, exactly as today.

## Audit trail

Each of the three relationships grants access, so each change to one is recorded. This uses
the project's existing django-pghistory setup, so attribution comes from
`CustomPGHistoryMiddleware`, which stores `username` and `user_email` on the context and so
survives the user being deleted.

The trail is deliberately **not** behind `ENABLE_PROGRAM_ACCESS_REDESIGN`. It is installed
as Postgres triggers, so it records changes made through the Django admin, the API, a data
migration, or a shell as well as through these forms. The consequence is that this part of
the change is not covered by the switch's rollback: undoing it means unapplying the
migrations.

### Program.funder

```python
@pghistory.track(fields=["funder"])
class Program(BaseModel):
```

The default trackers are insert and update, which is what is wanted: insert records the
funder a program was created with, and the generated update trigger is conditional on
`OLD.funder_id IS DISTINCT FROM NEW.funder_id`, so ordinary program edits write no rows.

### Program.watchers

Watchers are a `ManyToManyField`, so the rows that change live in the through model, and
adding or removing a watcher is a row insert or a row delete there. Tracking the
auto-created through model does not work: Django's autodetector ignores auto-created
through models, so pghistory generates an event model but never installs the triggers that
would populate it, leaving a permanently empty audit table.

`Program.watchers` is therefore repointed at an explicit through model:

```python
@pghistory.track(pghistory.InsertEvent(), pghistory.DeleteEvent())
class ProgramWatcher(models.Model):
    program = models.ForeignKey(Program, on_delete=models.CASCADE)
    organization = models.ForeignKey(Organization, on_delete=models.CASCADE)

    class Meta:
        db_table = "program_program_watchers"
        unique_together = [("program", "organization")]
```

Two details matter:

- **The trackers are given explicitly.** The defaults are insert and update, and a removal
  is a delete, so relying on the defaults would silently lose removals.
- **`db_table` pins the model to the table Django already generated** for the
  `ManyToManyField` in migration `0016`. The table exists in every deployed environment with
  rows in it, so the model creation is wrapped in `SeparateDatabaseAndState` with
  `database_operations=[]`: the existing table and its rows are adopted, and no DDL is
  attempted. This is the one hand-edited part of an otherwise generated migration and is the
  part to review most closely.

### Opportunity.supervising_organization

`Opportunity` is already tracked with `fields=["active"]`. The new field gets its **own**
tracker rather than joining that field list:

```python
@pghistory.track(
    pghistory.InsertEvent("supervising_organization_insert"),
    pghistory.UpdateEvent("supervising_organization_update"),
    fields=["supervising_organization"],
)
@pghistory.track(pghistory.UpdateEvent(), fields=["active"])
class Opportunity(BaseModel):
```

pghistory derives the event model name from the tracked field list, so adding a field to
the existing tracker would rename `OpportunityActiveEvent` — a name imported in `views.py`
and three test modules, and whose `active_events` related name is read by
`opportunity_edit.html`. A stacked tracker leaves that model, its table, and its rows
untouched.

Both labels are explicit. A label must be unique among a model's trackers and the `active`
tracker already holds the default `"update"`, so leaving this tracker's `UpdateEvent` to
default raises `ValueError`. Only that label is strictly required; the insert label is named
to match so the `pgh_label` values stored in one table read consistently.

### Viewing the trail

All three event models are registered in the Django admin through a shared
`AuditEventAdmin` base in `commcare_connect/utils/admin.py`. Three things it settles:

- **Read-only.** Add, change, and delete are all denied; editing an audit row would falsify
  the record it exists to preserve. Viewing still goes through the normal `view_*`
  permission, so read access can be granted without write.
- **The actor is read off `pgh_context.metadata`**, not a user foreign key, so it survives
  the user being deleted. `username` is shown on the changelist to match
  `active_toggle_metadata.html`; `user_email` appears on the detail view, since email is
  uniquely constrained on `User` while username is not, and roughly a tenth of users have no
  email at all.
- **Pointers degrade rather than raise.** `pgh_obj` is declared `db_constraint=False`, so
  the row it names can be deleted while the event survives. The display helper catches that
  and renders a dash.

`ProgramWatcherEvent` lists its snapshotted `program` and `organization` rather than
`pgh_obj`, which points at the through row and is therefore gone precisely for the delete
events you most want to read.

Filters use `RelatedOnlyFieldListFilter` so each dropdown lists only organizations that
actually appear in the audit table, rather than every organization in the system.

### Migrations

`program/0018` and `opportunity/0143`, both additive: they create event tables and their
triggers. Nothing existing is altered, renamed, or backfilled.

`migrate_multi` needs no special handling. The router's `allow_migrate` returns `True` for
the secondary unless a `run_on_secondary` hint says otherwise, and the existing pghistory
migrations pass no hints, so these follow the same precedent. Replication is opt-in through
`REPLICATION_ALLOWED_MODELS`; no event model is listed there and none is added, so the
publication is unchanged.

## Known gap, deliberately out of scope

`opportunity_manage_org_ids` (`program/utils.py:145-152`) returns
`supervising_organization_id` unconditionally, with no check that the organization is
still eligible. Nothing resets the column when an application is rejected either — the
status-change sites at `program/views.py:224` and `program/api/views.py:83` only write
the status.

So an organization that is set as supervisor and later rejected from the program **keeps
PM-level oversight of that opportunity** until someone reassigns it. This predates the
present change and is not introduced by it, and the form work above does not widen it:
the dropdown stops offering such an organization, so the only way to be in that state is
to already be in it.

Closing it means either re-checking eligibility inside `opportunity_manage_org_ids` or
resetting the column when an application is rejected. Both are permission-layer changes
and belong in their own ticket, tracked separately from this spec.

## Testing

Tests are appended to the existing `commcare_connect/program/tests/test_forms.py` and
`commcare_connect/opportunity/tests/test_forms.py` — neither is a new file. They target
the helper functions and form behaviour directly rather than view responses, per the
project testing convention. Existing `conftest.py` fixtures (`organization`, `funder_org`,
`watcher_org`, `supervisor_org`, `program`, `user`) are reused instead of new factory
calls.

Switch state is controlled with `waffle.testutils.override_switch`, as in
`commcare_connect/utils/tests/test_db.py`. Note that it cannot decorate a plain pytest
class — Django's `TestContextDecorator` rejects anything that is not a `SimpleTestCase`
subclass — so switch-on classes use a fixture that wraps it as a context manager.

### Program form

Cases with the switch **on**:

1. `funder_organizations` includes funder organizations and excludes both non-funders
   and the program's own organization.
2. `watcher_organizations` excludes the program's own organization and the funder, and
   includes everything else. The no-funder branch is covered behaviourally by the form
   tests, which build a form for a funderless program, so it gets no separate unit test.
3. Creating a program through the form persists the chosen funder and watchers.
4. On edit the funder field is disabled, and a POST carrying a different funder id
   leaves `program.funder` unchanged.
5. On edit watchers can still be added and removed.
6. Selecting the same organization as funder and watcher on create raises the
   validation error on `watchers`.

Cases with the switch **off**:

7. Neither `funder` nor `watchers` appears in `form.fields`, on create and on edit.
8. A POST carrying `funder` and `watchers` values is ignored — a program created this
   way has no funder and no watchers, and an existing program's values are unchanged.
   This covers the `save()` guard, not just the field removal.
9. A program that already has a funder and watchers set still saves cleanly through the
   form, with those existing values left intact rather than cleared.

### Opportunity form

Cases with the switch **on**:

10. `supervising_organizations` includes the program's organization, its funder, and
    organizations with an accepted `ProgramApplication`.
11. `supervising_organizations` excludes organizations whose application is `INVITED`,
    `APPLIED`, `REJECTED`, or `DECLINED`, and excludes unrelated organizations entirely.
12. `supervising_organizations` omits the funder when the program has none, without
    error.
13. On create, the field's `initial` is the program's organization, and saving without
    changing it produces an opportunity supervised by that organization.
14. On create, selecting an accepted applicant persists that organization as
    `supervising_organization`.
15. On edit, the field's `initial` is the opportunity's current supervising
    organization, and it is not disabled even when `OpportunityAccess` rows exist.
16. On edit, changing the supervisor persists the new organization, and
    `opportunity_manage_org_ids` then contains the new organization and not the old one.
17. On edit, when the stored supervisor has lost its accepted application, submitting
    that same value fails validation on `supervising_organization`; submitting an
    eligible organization succeeds.
18. Assigning a supervisor leaves `opportunity.organization` untouched, and an
    organization may be selected as supervisor while also being the opportunity's own
    organization.

Cases with the switch **off**:

19. `supervising_organization` does not appear in `form.fields`, on create and on edit.
20. A POST carrying `supervising_organization` is ignored on create — the opportunity
    falls back to the program's organization via `Opportunity.save()`.
21. An existing opportunity's supervising organization is unchanged by a save that
    carries a different value.

### Opportunity edit page

Covering `OpportunityChangeForm`, where the permission gate lives. The escalation case is
asserted in two halves, because hiding a field is not by itself a control.

28. A program manager's organization sees the field, and a change through the form
    persists.
29. The opportunity's own organization does **not** see the field, **and** a POST from it
    carrying a different `supervising_organization` leaves the stored value unchanged.
30. The field is absent for an unmanaged opportunity.
31. The field is absent when the form is built without a request, which is how several
    pre-existing tests construct it.

### Audit trail

These are independent of the switch, since the triggers are installed at the database
level. Each is written against the model rather than the form, because that is the level the
triggers act on.

32. Assigning and then changing a program's funder records the initial value and both
    changes in order.
33. An edit that does not touch the funder records nothing, so the audit table does not
    grow on every save.
34. Adding and then removing a watcher records an insert and then a delete. This is the case
    the auto-created through model would silently fail.
35. Assigning and then changing an opportunity's supervising organization records both.
36. An edit that does not touch the supervising organization records nothing.
37. `OpportunityActiveEvent` still records `active` changes, pinning that the stacked
    tracker has not disturbed the pre-existing one.

Attribution is not tested here. Populating `user_email` is the job of
`CustomPGHistoryMiddleware` and only happens on a real request, so a test that opened
`pghistory.context()` by hand would prove only that pghistory stores what it is given.
