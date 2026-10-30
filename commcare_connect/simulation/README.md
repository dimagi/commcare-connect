# Simulation

Builds local data by **using the app**, not by writing rows. Each step sends the request a real user action
sends: it submits the page's form as the acting admin, or calls the mobile API as the worker. Views, validation,
permission checks, derived records, visit flags and payment accrual all run, so the result hangs together the way
data from real use does.

Installed only by `config/settings/local.py` and `config/settings/test.py`; it never ships.

## Running a scenario

    ./manage.py run_scenario <scenario> --admin-email you@example.com [--small] [--seed N] [--keep]

`--admin-email` must belong to an existing local user, who becomes admin of every organization the scenario
creates. Use `--pm-email` / `--nm-email` to put the program-manager and network-manager sides on different users.
Unless `--keep` is passed, the scenario first deletes the organizations it owns, so reruns start clean. The
command prints the pages worth opening when it finishes.

## Writing a scenario

A scenario is a module in `scenarios/` with `ORG_NAMES` and `build(sim, *, small)`, written in `Sim` steps
(`steps/`). For one-off data for a feature, write a scenario the same way; keep it if others will want it.

**Never seed with the ORM or factories.** If no step does what you need, add one that sends the page's request
through `sim.browser(user)`, which fails with the errors the page showed if the page refuses it. Inbound
integrations with no page, such as HQ forwarding a form, call the function their endpoint runs.

## How it reaches no external service

`fakes.local_fakes()` patches the outbound calls to CommCare HQ, ConnectID and Twilio, runs Celery tasks inline,
and serves the simulated apps in `data/` (see `data/README.md`). Everything up to those calls is the real code path.

Each step runs in its own transaction (`Sim.step()`), so the `on_commit` side effects of one step exist before
the next starts. The tests swap in `django_capture_on_commit_callbacks(execute=True)` for the same effect inside the
test transaction (see `tests/conftest.py`).

## Keeping it working

`tests/test_scenarios.py` builds every scenario at its smallest size in CI. When a change to a form or flow breaks
it, update the step in the same PR.
