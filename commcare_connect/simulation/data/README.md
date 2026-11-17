# Simulated CommCare apps

Form XML that the simulation's HQ stand-in serves in place of a CCZ download, so that
`sync_learn_modules_and_deliver_units` creates real `LearnModule` and `DeliverUnit` rows.

- `learn_app/` is a copy of `opportunity/tests/data/demo_app` (modules `module_1`–`module_3`, assessment `demo`,
  tasks `task_1`, `task_2`).
- `deliver_app/` is hand-written: deliver units `household_visit` and `child_screening`. The demo app has no
  deliver blocks, and a payment unit cannot be created without one.

File names keep the CCZ layout (`modules-N-forms-M.xml`) so new forms slot in the same way.
