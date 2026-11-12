# ADR-0001: Record model change history with database triggers

**Status:** Accepted

**Author:** Chris Smit · **Created:** 2026-11-12

## Context

Several records need a history of who changed them and when: invoice status, an opportunity's active flag and supervising organization, organizations and their contacts, and others. The `created_by` and `modified_by` fields on `BaseModel` record only the latest change. History written by application code is lost whenever a write skips `Model.save()`, such as `QuerySet.update()`, `bulk_create()`, a data migration or a shell session.

## Decision

We will record change history with django-pghistory, which installs Postgres triggers that copy each tracked change into an event table.

- Each model opts in with `@pghistory.track` and chooses what to record: a few fields, or a snapshot of the whole row minus the `BaseModel` audit fields.
- Every request opens a pghistory context. It stores the user's `username` and `user_email` as text, so the record still names the actor after the user is deleted.
- Changes made on someone's behalf outside a request, such as in Celery tasks or management commands, are wrapped in `pghistory.context(username=..., user_email=...)`.
- Event rows have no database constraint on the record they describe, so an event survives the deletion of its record.
- Events are read-only.

## Consequences

- Every write path is recorded, including bulk updates, the admin, data migrations and the shell.
- Each tracked model gets its own event table and triggers, created by an ordinary migration.
- Adding, renaming or removing a field on a tracked model also regenerates its triggers.
- Many-to-many fields need an explicit through model, because triggers can't be installed on Django's auto-created through tables.
- A change made outside a request without an explicit context is recorded with no actor.
- Event model and relation names are derived from the tracked fields, so adding a field to an existing tracker renames them.
- Each event is a snapshot, not a diff; showing what changed means comparing it with the previous event.

## Alternatives considered

- Rely on `BaseModel`'s `modified_by` and `date_modified` — rejected because they keep only the latest change.
- A library that records history from Django signals or `save()` — rejected because it misses writes that bypass the ORM's per-instance save.
- Store the actor as a foreign key to `User` — rejected because the record would lose its actor when the user is deleted.
