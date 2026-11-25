> These checks are additional to the general Python review. Only raise an issue you are confident is a real defect; confirm the base classes and decorators with `file_read` before reporting one.

#### Base Classes

- A new model that does not extend `BaseModel` (`commcare_connect/utils/db.py`), which provides `created_by`, `modified_by`, `date_created` and `date_modified`. Many existing models predate this convention; only flag models the diff adds
- A new model that the API or worker-scoped URLs will expose, without a `UUIDField(editable=False, default=uuid4, unique=True)` identifier like `opportunity_id` or `payment_unit_id`. API lookups are moving from integer PKs to UUIDs (the `API_UUID` switch)

#### Organization Scoping

Most views are scoped under `/a/<org_slug>/`, and a user may only see data belonging to their organization or to a program their organization manages.

- A manager method, queryset or `related_name` traversal that can return rows from another organization or another opportunity
- A new model holding organization or opportunity data with no foreign key that lets a view scope it

#### Audit History

Read `docs/audit-history.md` before reporting any of these.

- A field added to an existing `@pghistory.track(fields=[...])` list. That renames the event model and its relation, which code may refer to; add a separate tracker instead, as `Opportunity` does for `supervising_organization`
- Two trackers on one model using the same event label (`insert`, `update`, ...)
- A model change applied outside a request (a Celery task, form processing, a management command) on someone's behalf without `pghistory.context(username=..., user_email=...)`, so the event records no actor

#### Terminology

- A model or field that treats "worker" as its own entity. There is no `Worker` model: a worker is a `User` reached through an `OpportunityAccess`

#### Do Not Report

- Missing docstrings, `Meta.ordering`, or `__str__`
- Models that already existed without `BaseModel`
