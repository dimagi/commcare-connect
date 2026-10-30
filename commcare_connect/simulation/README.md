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
Unless `--keep` is passed, the scenario first deletes the organizations it owns, so reruns start clean. It
refuses, deleting nothing, when anything outside them depends on them: another organization's opportunity
under their program or using their apps, or a solicitation. The command prints the pages worth opening when it
finishes.

## Standard scenarios

Each builds its own `Sim <Scenario> PM` and `Sim <Scenario> NM` organizations, a program and managed opportunities,
with a history that runs from about six weeks ago to now.

- `baseline`: one active opportunity whose workers have finished learning and claimed it.
- `opp_setup`: opportunities in each setup state: a draft, an active one, an ended one, and one with several
  payment units and verification rules.
- `learning`: workers at every learn stage: invited only, part way, finished but not claimed, failed the
  assessment, and claimed.
- `delivery_review`: clean and flagged visits (duplicate, no GPS, too short), approved, rejected or still pending
  with the network manager, and agreed or disagreed by the program manager.
- `payments`: approved work paid to workers in full and in part, and invoices that are paid, ready to pay, and
  waiting for program-manager review.
- `payment_units`: one opportunity paying two kinds of visit at different rates, workers paid in full and in part,
  and an invoice whose line items split the work by payment unit.

`--small` builds the fewest records that still show every state; that is what CI builds.

## Writing a scenario

A scenario is a module in `scenarios/` with `ORG_NAMES` and `build(sim, *, small)`, written in `Sim` steps
(`steps/`). For one-off data for a feature, write a scenario the same way; keep it if others will want it.

**Never seed with the ORM or factories.** If no step does what you need, add one that sends the page's request
through `sim.browser(user)`, which fails with the errors the page showed if the page refuses it. Inbound
integrations with no page, such as HQ forwarding a form, call the function their endpoint runs.

The one exception is configuration records that have no UI path: the simulated `HQServer` and its OAuth
application, and the program's `DeliveryType`, are get-or-created.

## History and the clock

The app stamps many records with the current time itself (claims, learn start, reviews, invites) and checks
limits against today. To build a history that reads in order, a step runs each real action inside
`sim.at(moment)`, which moves the clock the app reads (via `time-machine`). Timestamps written by database
triggers, such as the pghistory audit trail, keep the real time.

## How it reaches no external service

`fakes.local_fakes()` patches the outbound calls to CommCare HQ, ConnectID and Twilio, runs Celery tasks inline,
and serves the simulated apps in `data/` (see `data/README.md`). Everything up to those calls is the real code path.

Each step runs in its own transaction (`Sim.step()`), so the `on_commit` side effects of one step exist before
the next starts. The tests swap in `django_capture_on_commit_callbacks(execute=True)` for the same effect inside the
test transaction (see `tests/conftest.py`).

## Keeping it working

`tests/test_scenarios.py` builds every scenario at its smallest size in CI. When a change to a form or flow breaks
it, update the step in the same PR.
