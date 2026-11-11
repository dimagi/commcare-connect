# Services

Read [`../code-organization.md`](../code-organization.md) first.

A request body for an external system is a private function of the service that sends it, not a model method.

## Checks

A check that guards a change lives in the service's module, and the service calls it before writing. Checks raise `django.core.exceptions.ValidationError`, with a dict keyed by field name when the error is about one input. Forms call them in `clean()`, and serializers in `validate()`. Forms and serializers check the input's shape themselves (required fields, types, ranges, start ≤ end). Anything that needs the database or other objects' state is a service check.

## Celery tasks

Services dispatch the tasks they need with `transaction.on_commit(partial(task.delay, ...))`, importing the task inside the function, because `tasks.py` imports services. Services let transient errors (network errors, lock timeouts) propagate. The task decides whether to retry, and each task with `autoretry_for` has a test that it retries.

## Notifications

For email, SMS and push, the service of the app whose event it is picks the recipients, builds the message and queues the send after commit. Email goes through `utils/tasks.send_mail_async`, SMS through `utils/sms.send_sms`, and push through `connect_id_client.main.send_message`. Rendering an email's templates is generic code in `commcare_connect/utils/email.py`. Tasks don't build messages.

## File imports

Parsing an uploaded file into rows is a utility that returns rows or raises a parse error from `exceptions.py`. Checking the rows against the database and saving them is a service in `services/<topic>_import.py`. The view or task only opens the file and calls the service.
