# Tests

Read [`../code-organization.md`](../code-organization.md) first.

- Setup for one test module is a fixture in that module. Setup for several modules of one app goes in the app's `tests/conftest.py`, and setup for several apps in `commcare_connect/conftest.py`.
- Builders and assertions that aren't fixtures go in `tests/helpers.py` of the app that owns the model they build or check. Only production code drops `helpers.py`.
- Factories go in `tests/factories.py` of the model's app. Factories for third-party models go in the app that integrates them (`SocialApp` in `users`).
- Share setup through fixtures (`usefixtures` on a class), not base classes.
- Test modules may import `tests/helpers.py` and `tests/factories.py`, but never other test modules.
- Test modules mirror the code they test (`opportunity/services/billing.py` → `opportunity/tests/services/test_billing.py`) and move with it in the same `refactor:` commit. A single `tests.py` becomes a `tests/` package when a second test module is needed.
- Factories build consistent related objects themselves, e.g. a `UserVisitFactory` given `opportunity_access` takes `user` and `opportunity` from it with `factory.SelfAttribute`. Don't pass values that equal a factory's defaults.
- Patch at the boundary (the HTTP client, a task's `.delay`), not internal functions, so moving internals doesn't break tests.
- Use `pytest.mark.parametrize` when tests differ only in inputs and expected values. Keep separate tests when each case needs its own setup or explanation.
