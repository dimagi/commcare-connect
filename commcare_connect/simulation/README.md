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
(`steps/`). `scenarios/_common.py` has the shared opening (organizations, program, trained workers).

A personal scenario goes in `scenarios/local/` instead (see "Shared and personal scenarios" below). Git ignores
that folder and CI doesn't build it, but `run_scenario <name>` runs it like any other scenario. Its name must not
be one a shared scenario already uses.

**Never seed with the ORM or factories.** If no step does what you need, add one that sends the page's request
through `sim.browser(user)`, which fails with the errors the page showed if the page refuses it. Inbound
integrations with no page, such as HQ forwarding a form, call the function their endpoint runs.

The one exception is configuration records that have no UI path: the simulated `HQServer` and its OAuth
application, and the program's `DeliveryType`, are get-or-created.

A scenario is done when it has run: build it with `run_scenario <name> --admin-email <your local user>` and open
the pages it prints. For a shared scenario, also run `pytest commcare_connect/simulation/tests/test_scenarios.py`.

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

Steps call the same code the pages do, so they break when that code changes, and CI catches it:
`tests/test_scenarios.py` builds every standard scenario at its smallest size. What to update depends on the change:

- **A form or view behind a step changes** (a new required field, a new rule): update the step in the same PR.
- **A page's request changes** (a renamed field, a new required field, a different URL or success response):
  update the step's POST to match what the page now sends.
- **A new feature needs local data**: whoever first needs it adds the step. Areas with no steps yet include tasks,
  microplanning, audit, solicitations and the reports.
- **A new state should be easy to reach**: extend the shared scenario for that area, or add a new one if no area
  fits.

Scenarios only combine steps, so they change only when the state they show should change.

## Shared and personal scenarios

Shared scenarios are the small set everyone runs to reach a known state. Each one costs CI time and upkeep, so add
one only for a state many people will need; otherwise extend an existing one.

Everything else is personal and lives in the gitignored `scenarios/local/`:

- **A feature in progress**: the data you need while building it.
- **A bug repro**: write the steps that reach the bug, see it in the browser, fix it, rerun. What gets committed is
  a regression test, small and built on the usual fixtures. Use `Sim` steps in that test only when the bug needs a
  long flow (deliver, review, pay, invoice) that factories can't set up faithfully. The `sim` fixture is in
  `tests/conftest.py`; move it to the global `conftest.py` when another app's tests need it.
- **A PR a reviewer needs to try**: extend a shared scenario if the state is generally useful, or paste the steps
  in the PR description.
