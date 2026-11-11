# LLO Contact Import Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A management command, `import_llo_contacts`, that imports contacts from the LLO Directory workbook into
Connect, creating missing organizations from the Organizations tab. By default it does a dry run that only
validates and reports. With `--commit` it writes the records and saves a copy of the workbook with each row's
Connect ID and import status.

**Architecture:** A small `organization/llo_import/` package with one module per step:

1. `workbook` reads the tabs into rows and later writes the result columns.
2. `mapping` turns each sheet row into form data plus a list of problems.
3. `planning` matches contacts to organizations and validates them with Connect's own forms, without saving, into an
   `ImportPlan`.
4. `execution` saves a plan.
5. `report` renders the plan as plain text.

The command wires these together. A dry run is steps 1–3 plus `report`. A real run also runs `execution` and
`workbook.write_results`.

**Tech Stack:** Django 5.2 management command; openpyxl (already installed via `tablib[xlsx]`, so no new
dependency); `OrganizationDirectoryForm` / `ContactForm`; pghistory; pytest + factory-boy.

**Spec:** `docs/superpowers/specs/2026-11-10-contact-import-design.md`. Read it before starting; this plan doesn't
repeat its rules.

## Global Constraints

- The command is `./manage.py import_llo_contacts <path> --user-email <email> [--commit]`. Without `--commit` it
  writes nothing: no database rows and no file.
- **Only add, never change.** Never update an existing `Organization` or `Contact`, including which contact is the
  main POC.
- **Pass or block:**
  - An empty optional cell is fine.
  - A value that is present but can't be mapped exactly as the spec says blocks its record.
  - There are no warnings and no partial imports.
- Every problem with a record is reported, not just the first one.
- Organization names match exactly, case included, after trimming surrounding whitespace. Country and sector names
  match exactly. There are no aliases.
- Validate with the existing forms, `OrganizationDirectoryForm` and `ContactForm`. Never re-implement their field
  rules.
- Tab names are `Contacts` and `Organizations`. Columns are found by their row-1 header text. Reported row numbers
  are the sheet's own, with the header as row 1.
- Result columns are named exactly `Connect ID` and `Connect Import Status`. The status values are exactly `Created`
  and `Already existed`.
- The output copy goes next to the input as `<input stem> - imported <YYYY-MM-DD HHMM>.xlsx`, using local time. The
  input file is never written.
- Records are attributed to `--user-email`: `created_by` and `modified_by` are set to that email, and every write
  runs inside `pghistory.context(username=user.username, user_email=user.email)` (`docs/audit-history.md`).
- Tests live in `commcare_connect/organization/tests/`. Use plain `assert`. Prefer existing fixtures and factories
  (`OrganizationFactory`, `ContactFactory`, `UserFactory` in `users/tests/factories.py`).
- Commits are semantic and short. Each task's tests go in that task's commit. Run `prek run --files <files>`, then
  `git commit --no-verify`.

## Review Focus

1. **Case and whitespace in emails.** `A@x.org ` in the sheet and `a@x.org` in Connect, or on another row, are the
   same email: it matches the existing contact and is a duplicate of the other row. Pinned in Task 4.
2. **One new organization needed by several contacts, some of them bad.** The organization is created once and only
   the valid contacts are linked. If every one of its contacts is bad, the organization isn't created. Pinned in
   Tasks 4 and 5.
3. **Excel's own value types.** Floats such as `50.0` and `2348012345678.0`, an `int` phone, a cell that holds a
   formula outside the EOI column, trailing spaces, and rows that are blank apart from formatting are all handled.
   Pinned in Tasks 2 and 3.
4. **Rerunning on the output file.** Rows that already have a Connect ID aren't attempted. The result columns are
   reused, not added a second time. An ID that isn't in this Connect blocks its row. Pinned in Tasks 4 and 6.
5. **Trailing unnamed columns.** The Organizations tab has empty-header columns after `HQ Source`. New columns go
   after the last used column and nothing existing is overwritten. Pinned in Task 6.

---

## File structure

| File | Responsibility |
|---|---|
| `commcare_connect/organization/management/__init__.py`, `commands/__init__.py` | Empty package files |
| `commcare_connect/organization/management/commands/import_llo_contacts.py` | Arguments, user lookup, wiring, printing the report |
| `commcare_connect/organization/llo_import/__init__.py` | Empty |
| `commcare_connect/organization/llo_import/workbook.py` | Load tabs → `SheetRow`s; write result columns and save the copy |
| `commcare_connect/organization/llo_import/mapping.py` | `Problem`; contact/organization row → form data + problems |
| `commcare_connect/organization/llo_import/planning.py` | Matching, sheet-wide checks, form validation → `ImportPlan` |
| `commcare_connect/organization/llo_import/execution.py` | Save an `ImportPlan` (real run) |
| `commcare_connect/organization/llo_import/report.py` | Render an `ImportPlan` as text |
| `commcare_connect/organization/tests/llo_import_helpers.py` | `make_workbook(...)` test helper |
| `commcare_connect/organization/tests/test_llo_import_*.py` | One test module per source module + the command |

---

### Task 1: Command reads the workbook

The consumer comes first. This task adds the command with argument parsing, the user check and workbook loading.
At the end of the task the command prints only the row counts it read, and later tasks replace that output.

**Files:**
- Create: the management package files, `llo_import/__init__.py`, `llo_import/workbook.py`, the command
- Create: `organization/tests/llo_import_helpers.py`, `organization/tests/test_llo_import_workbook.py`,
  `organization/tests/test_llo_import_command.py`

**Interfaces:**
- Produces, in `workbook.py`:

  ```python
  CONTACTS_TAB = "Contacts"; ORGANIZATIONS_TAB = "Organizations"
  CONNECT_ID_COLUMN = "Connect ID"; IMPORT_STATUS_COLUMN = "Connect Import Status"
  CONTACT_COLUMNS: tuple[str, ...]       # the 7 Contacts headers from the spec, verbatim
  ORGANIZATION_COLUMNS: tuple[str, ...]  # the 16 mapped Organizations headers from the spec, verbatim

  class WorkbookError(Exception): ...    # missing tab / missing column; message names them

  @dataclass(frozen=True)
  class SheetRow:
      number: int                      # sheet row number, header = 1
      values: dict[str, object]        # header -> raw cell value (None when empty); result columns included when present
      hyperlinks: dict[str, str]       # header -> cell hyperlink target, only for cells that have one

  @dataclass
  class LLOWorkbook:
      path: Path
      book: openpyxl.Workbook          # loaded with data_only=False so formulas survive a save
      contacts: list[SheetRow]
      organizations: list[SheetRow]

  def load_workbook(path: Path) -> LLOWorkbook
  ```

- In `llo_import_helpers.py`:
  `make_workbook(tmp_path, contacts: list[dict], organizations: list[dict], extra_org_headers=()) -> Path`.
  It writes an `.xlsx` with both tabs. The headers are `CONTACT_COLUMNS` / `ORGANIZATION_COLUMNS` (plus any extra),
  and each dict maps a header to a value. A value given as `("text", "https://…")` is written as text with that cell
  hyperlink.

- [ ] **Step 1: Write the failing tests**
  - `test_reads_rows_by_header` — given column order shuffled and a header with trailing spaces in the sheet,
    `load_workbook(...).contacts[0].values["Email Address"]` is the value, and `.number == 2`.
  - `test_skips_rows_with_no_values`: an all-`None` row in the middle doesn't appear, and the row after it keeps
    its real sheet number (4).
  - `test_keeps_cell_hyperlinks`: `organizations[0].hyperlinks["EOIs Applied for (Add Link to EOI)"]` holds the
    target.
  - `test_keeps_formula_text`: a cell set to `=HYPERLINK("https://a.example","A")` reads back as that string.
  - `test_missing_tab_or_column_raises` (parametrized: no Contacts tab; no `Email Address` header): raises
    `WorkbookError`, and its message contains the missing name.
  - In `test_llo_import_command.py`:
    - `test_unknown_user_email_fails`: `call_command("import_llo_contacts", path, user_email="nobody@x.org")`
      raises `CommandError` mentioning the email.
    - `test_missing_column_fails_cleanly`: a `WorkbookError` surfaces as a `CommandError` with the same message.

- [ ] **Step 2: Run and confirm they fail**
  Run: `pytest commcare_connect/organization/tests/test_llo_import_workbook.py commcare_connect/organization/tests/test_llo_import_command.py -v`
  Expected: FAIL (module not found).

- [ ] **Step 3: Implement `load_workbook` and the command**
  - Strip header text before matching.
  - The command's `handle(path, user_email, commit, **options)` looks up the user with
    `User.objects.filter(email__iexact=user_email)`. If that doesn't find exactly one user, raise `CommandError`.
    It then calls `load_workbook` and turns `WorkbookError` into a `CommandError`.
  - For now it writes `f"Read {n} contact rows and {m} organization rows."` to stdout.
  - `--commit` is a `store_true` flag that is still unused.

- [ ] **Step 4: Run and confirm they pass** (same command; expected PASS)

- [ ] **Step 5: Commit**: `feat(organization): add command to read the LLO directory workbook`

---

### Task 2: Map contact rows

**Files:**
- Create: `llo_import/mapping.py`, `organization/tests/test_llo_import_mapping.py`

**Interfaces:**
- Consumes: `SheetRow`, the column constants (Task 1).
- Produces, in `mapping.py`:

  ```python
  @dataclass(frozen=True)
  class Problem:
      column: str | None   # sheet header, or None for a row-level problem
      value: str | None    # the offending cell value as text
      reason: str

  @dataclass
  class ContactRow:
      row: SheetRow
      name: str
      email: str                  # stripped; "" when missing
      organization_name: str      # stripped
      is_main_poc: bool
      connect_id: str             # stripped Connect ID cell, "" when empty
      form_data: dict[str, str]   # ContactForm data, without "organization"
      problems: list[Problem]

  def map_contact_row(row: SheetRow) -> ContactRow
  def cell_text(value: object) -> str  # shared helper: None -> "", float with no fraction -> its int digits, str -> stripped
  ```

  `form_data` keys are `name, title, is_main_poc ("True"/"False"), email, phone, notes`.

- [ ] **Step 1: Write the failing tests** (parametrize where marked)
  - `test_maps_all_fields`: a full row gives the expected `form_data`, with surrounding spaces trimmed.
  - `test_main_poc_values` (parametrized): `"Yes"` → True, `"No"` → False, `None` → False, and `""` → False, all
    with no problem. `"yes please"` → a `Problem("Main POC?", "yes please", …)`.
  - `test_phone_from_float`: `2348012345678.0` → `"2348012345678"`. The `int` `2348012345678` gives the same.
  - `test_email_with_several_addresses_is_a_problem` (parametrized: `"a@x.org, b@x.org"`, `"a@x.org\nb@x.org"`):
    gives a problem on `Email Address`.
  - `test_missing_required_values` (parametrized over name, email and organization name): each gives a problem
    naming its column.
  - `test_formula_cell_is_a_problem`: `"=A1"` in `Role / Title` gives a problem whose reason says formulas aren't
    read.

- [ ] **Step 2: Run, expect FAIL** — `pytest commcare_connect/organization/tests/test_llo_import_mapping.py -v`

- [ ] **Step 3: Implement `cell_text` and `map_contact_row`**
  - Use `cell_text` for every value.
  - Any string that starts with `=` is a problem in every column except EOI (Task 3).
  - The email check finds several addresses by splitting on `[,;\s]+`. It doesn't validate address syntax; the
    form does that.

- [ ] **Step 4: Run, expect PASS**

- [ ] **Step 5: Commit**: `feat(organization): map LLO contact rows to contact form data`

---

### Task 3: Map organization rows

**Files:**
- Modify: `llo_import/mapping.py`
- Modify: `organization/tests/test_llo_import_mapping.py`

**Interfaces:**
- Consumes: `Problem`, `cell_text`, `SheetRow`.
- Produces:

  ```python
  @dataclass
  class OrganizationRow:
      row: SheetRow
      name: str
      connect_id: str
      form_data: dict[str, object]   # OrganizationDirectoryForm data; countries/primary_sectors are lists of pks
      problems: list[Problem]

  def map_organization_row(row: SheetRow, countries: dict[str, str], sectors: dict[str, int]) -> OrganizationRow
  # countries: Country.name -> Country.pk; sectors: PrimarySector.name -> pk. Built once by the caller.
  def split_countries(value: str) -> list[str]
  def split_sectors(value: str) -> list[str]
  def eoi_links(text: str, hyperlink: str | None) -> tuple[list[str], list[str]]  # (urls, lines with no link)
  ```

  Use the spec's mapping table for every column. The form data keys are `OrganizationDirectoryForm.Meta.fields`
  minus `status`.

- [ ] **Step 1: Write the failing tests**
  - `test_split_countries` (parametrized):
    - `'Nigeria, Chad, "Congo, the Democratic Republic of the"'` → `["Nigeria", "Chad", "Congo, the Democratic Republic of the"]`
    - `"Nigeria "` → `["Nigeria"]`
  - `test_split_sectors`: `"Health, WASH (Water, Sanitation, Hygiene), Education"` →
    `["Health", "WASH (Water, Sanitation, Hygiene)", "Education"]`
  - `test_unknown_country_and_sector_are_problems`: `"DRC"` is a problem on `Countries of Operation`, with a reason
    that says names must match Connect's country list. `"health"` is a problem when only `"Health"` exists.
  - `test_eoi_links` (parametrized):
    - `("Title — https://a.example/x", None)` → `(["https://a.example/x"], [])`
    - `('=HYPERLINK("https://a.example/y","Title")', None)` → `(["https://a.example/y"], [])`
    - `("Title", "https://a.example/z")` → `(["https://a.example/z"], [])`
    - `("One\nTwo", "https://a.example/z")` → `(["https://a.example/z"], ["Two"])`; the hyperlink covers the first
      line that has no URL
    - `("None yet, but has deployed Connect", None)` → `([], ["None yet, but has deployed Connect"])`
    - duplicate URLs are kept once
  - `test_eoi_line_without_link_blocks`: such a line gives a problem on the EOI column.
  - `test_has_used_connect` (parametrized): `"X"` and `"x"` → `"True"`; `None` → `"False"`; `"Yes"` → a problem.
  - `test_team_size`: `"51-200"` passes through. `"Medium"` → a problem.
  - `test_whole_numbers`: `50.0` → `50`. `50.5` → a problem on `No. of FLWs Managed`.
  - `test_contact_emails_one_per_line`: `"a@x.org, b@x.org;c@x.org"` → `"a@x.org\nb@x.org\nc@x.org"`.

- [ ] **Step 2: Run, expect FAIL**

- [ ] **Step 3: Implement**
  - `split_countries` uses `csv.reader([value], skipinitialspace=True)`, then strips each value.
  - `split_sectors` splits on commas at bracket depth 0.
  - `eoi_links`:
    1. If the whole text is a `=HYPERLINK("url", …)` formula, take its URL.
    2. Otherwise, from each non-empty line, take any `https?://\S+` URLs.
    3. The cell hyperlink is assigned to the first line that has no URL.
    4. Lines still without a URL are returned as the second list.

- [ ] **Step 4: Run, expect PASS**

- [ ] **Step 5: Commit**: `feat(organization): map LLO organization rows to directory form data`

---

### Task 4: Build the import plan and print the dry-run report

**Files:**
- Create: `llo_import/planning.py`, `llo_import/report.py`
- Create: `organization/tests/test_llo_import_planning.py`, `organization/tests/test_llo_import_report.py`
- Modify: the command, so that it prints `render_report(plan, committed=False, output_path=None)`
- Create: `organization/tests/conftest.py` (`countries`, `sectors` fixtures moved here)
- Modify: `organization/tests/test_directory.py` (drop the two moved fixtures)

**Interfaces:**
- Consumes: `LLOWorkbook`, `ContactRow`, `OrganizationRow`, `Problem`, and the mapping functions.
- Produces, in `planning.py`:

  ```python
  class Status(StrEnum):
      TO_CREATE = "to_create"; CREATED = "created"
      ALREADY_EXISTED = "already_existed"     # matched an existing Connect record
      ALREADY_IMPORTED = "already_imported"   # row already had a Connect ID
      BLOCKED = "blocked"
      NOT_NEEDED = "not_needed"               # org row valid, but none of its contacts import

  @dataclass
  class OrganizationPlan:
      source: OrganizationRow | None          # None when only matched in Connect
      organization: Organization | None       # existing, or the created one after execution
      status: Status
      problems: list[Problem]
      contacts: list["ContactPlan"]

  @dataclass
  class ContactPlan:
      source: ContactRow
      organization_plan: OrganizationPlan | None
      contact: Contact | None                 # existing (ALREADY_*), or created after execution
      status: Status
      problems: list[Problem]

  @dataclass
  class ImportPlan:
      contacts: list[ContactPlan]
      organizations: list[OrganizationPlan]   # one per distinct organization referenced

  def build_import_plan(workbook: LLOWorkbook) -> ImportPlan
  ```

- Produces, in `report.py`: `render_report(plan: ImportPlan, committed: bool, output_path: Path | None) -> str`.

**Rules `build_import_plan` applies.** These come from the spec; the spec is authoritative if anything differs.

- **Rows that already have a Connect ID.**
  - A contact row with an ID looks it up as `Contact.objects.filter(contact_id=…)`. If found, the row is
    `ALREADY_IMPORTED`. If the ID isn't a valid UUID or no contact has it, the row is `BLOCKED`.
  - An organization row with an ID is handled the same way, looking up by slug.
- **Existing emails.** A contact whose email exists in Connect (`email__iexact`) is `ALREADY_EXISTED`, with
  `contact` set to the existing record. This takes priority over the duplicate-in-sheet rule.
- **Duplicates within the sheet.**
  - Emails are compared stripped and lower-cased. Every row that shares an email is `BLOCKED`.
  - If several rows say `Yes` under Main POC for the same organization name, all of them are `BLOCKED`.
- **Resolving a contact's organization.** These are tried in order:
  1. An Organizations-tab row with exactly that name and a Connect ID: link by slug. If the slug isn't found or the
     organization is archived, the row is blocked.
  2. `Organization.objects.filter(name=name)`:
     - One match that isn't archived: link to it. Its plan is `ALREADY_EXISTED`.
     - One archived match: `BLOCKED`.
     - More than one match: `BLOCKED`, listing the slugs.
  3. Exactly one Organizations-tab row with that name: validate it with
     `OrganizationDirectoryForm(data=row.form_data)`.
     - Valid → `TO_CREATE`.
     - Invalid → `BLOCKED`, with each form error turned into a `Problem` on that field's sheet column.
     - When a Connect organization's name matches case-insensitively, add the reason "differs only in case from
       '<existing name>'".
  4. Several tab rows with that name → `BLOCKED` (ambiguous in the sheet). No tab row → `BLOCKED`. If a tab row
     matches case-insensitively, add "differs only in case from Organizations row N".
- **Main contact already set.** A contact marked Main POC whose organization already has an `is_main_poc` contact in
  Connect is `BLOCKED`.
- **Contact validation.**
  - For an existing organization: `ContactForm(data={**form_data, "organization": org.pk})`.
  - For a `TO_CREATE` organization: validate without `organization` and ignore only the `organization` error.
  - If the organization plan is `BLOCKED`, the contact is `BLOCKED` with the reason "organization can't be
    created".
- **Organizations whose contacts all fail.** A `TO_CREATE` organization with no `TO_CREATE` contacts becomes
  `NOT_NEEDED`, and its problems stay listed.
- **Collect every problem.** Don't stop at the first problem: mapping problems, sheet-wide problems and form errors
  all go on the record.
- **Queries.** Load countries and sectors once, and match emails against Connect in one query.

- [ ] **Step 1: Write the failing planning tests.** Build workbooks with `make_workbook`, and build the Connect state
  with factories plus the `countries`/`sectors` fixtures (move those two fixtures from `test_directory.py` into
  `organization/tests/conftest.py`).
  - `test_links_contact_to_existing_organization`
  - `test_creates_missing_organization_once_for_two_contacts` (status `TO_CREATE` on one `OrganizationPlan` with two
    contacts)
  - `test_organization_name_match_is_case_sensitive`: Connect has `ACME` and the sheet says `acme`, with a complete
    tab row. The organization is `BLOCKED`, and one problem mentions `ACME`.
  - `test_ambiguous_organization_is_blocked`: two Connect organizations share the name. The problem lists both slugs.
  - `test_archived_organization_is_blocked`
  - `test_existing_email_is_already_existed` (parametrized email case: `A@X.org ` vs stored `a@x.org`)
  - `test_duplicate_emails_block_every_row`: three rows, one email in different cases. All three are `BLOCKED`.
  - `test_main_poc_conflicts` (parametrized: Connect already has a main contact; two sheet rows say Yes): every
    affected row is `BLOCKED`.
  - `test_blocked_organization_blocks_its_contacts`: a missing short name blocks the organization, and the contact's
    reason names the organization.
  - `test_organization_not_needed_when_all_contacts_blocked`
  - `test_rows_with_connect_id_are_not_attempted` (parametrized for a contact ID and for an organization-tab slug):
    a known ID → `ALREADY_IMPORTED` / linked; an unknown ID → `BLOCKED`.
  - `test_collects_all_problems`: a row with no name and a bad Main POC has two problems.

- [ ] **Step 2: Run, expect FAIL**

- [ ] **Step 3: Implement `build_import_plan`.** Add a module-level `FIELD_COLUMNS: dict[str, str]` (form field →
  sheet header) to translate form errors.

- [ ] **Step 4: Run, expect PASS**

- [ ] **Step 5: Write the failing report tests**
  - `test_dry_run_header_says_nothing_was_written`: the output starts with `DRY RUN — nothing was written` and
    names the file.
  - `test_lists_each_section`: a plan with one of each status. The output contains the totals line, the new
    organization with its contact row numbers, the blocked organization's row, column, value and reason, the
    blocked contact's row/name/email/organization and every reason, and the already-existing contact's
    organization.
  - `test_real_run_names_output_file`: `committed=True` shows `REAL RUN` and the output path.

- [ ] **Step 6: Run, expect FAIL. Implement `render_report`, then run, expect PASS.** Use plain text without colours.
  Sections are headed by plain-text titles in the spec's order, and empty sections are left out.

- [ ] **Step 7: Wire the command.** It prints the report. Add `test_dry_run_writes_nothing` to the command tests:
  after a run, `Organization`/`Contact` counts are unchanged, no new file exists in `tmp_path`, and stdout contains
  `DRY RUN`.

- [ ] **Step 8: Commit**: `feat(organization): report what an LLO contact import would do`

---

### Task 5: Save the plan on `--commit`

**Files:**
- Create: `llo_import/execution.py`, `organization/tests/test_llo_import_execution.py`
- Modify: the command (`--commit` → `execute_import_plan`, then render with `committed=True`)

**Interfaces:**
- Consumes: `ImportPlan`, `OrganizationPlan`, `ContactPlan`, `Status` (Task 4).
- Produces: `execute_import_plan(plan: ImportPlan, user: User) -> None`. It mutates statuses (`TO_CREATE` →
  `CREATED` or `BLOCKED`) and sets `organization` / `contact` on the plans.

**Transactions and re-checks:**

- Each organization plan is processed in its own `transaction.atomic()`:
  1. Re-validate and save a new organization with `OrganizationDirectoryForm`.
  2. Save each `TO_CREATE` contact in a nested `atomic()`, re-validating it with `ContactForm` and the real
     organization pk.
  3. Before saving a contact, re-check that its email isn't taken and, for a main POC, that the organization has no
     main contact. An `IntegrityError` blocks only that contact.
- If none of a new organization's contacts are saved, roll back the organization: raise inside its atomic block,
  and mark it `NOT_NEEDED`.
- Everything runs inside one `pghistory.context(...)`. Set `created_by` / `modified_by` = `user.email` on
  `form.instance` before `save()`.

- [ ] **Step 1: Write the failing tests**
  - `test_creates_organization_and_contacts`: the fields, countries and sectors are saved, `created_by` is the
    user's email, and the contact is linked.
  - `test_records_who_imported_in_history`: `OrganizationEvent` / `ContactEvent` context metadata `user_email` ==
    user email.
  - `test_contact_conflict_at_save_time_is_blocked`: after planning, create a `ContactFactory` with the same email.
    Executing marks that contact `BLOCKED` and doesn't raise. The other contacts are still created.
  - `test_organization_rolled_back_when_no_contact_saves`: the organization doesn't exist afterwards, and its plan is
    `NOT_NEEDED`.
  - `test_never_changes_existing_records`: an existing organization's fields and its existing main contact are
    unchanged (compare `date_modified` and `is_main_poc`).
- [ ] **Step 2: Run, expect FAIL**
- [ ] **Step 3: Implement `execute_import_plan`**
- [ ] **Step 4: Run, expect PASS**
- [ ] **Step 5: Command test.** `test_commit_creates_records` checks that `--commit` creates the records and stdout
  says `REAL RUN`. Run it and expect a PASS.
- [ ] **Step 6: Commit**: `feat(organization): import LLO contacts and organizations with --commit`

---

### Task 6: Write Connect IDs back to a copy of the workbook

**Files:**
- Modify: `llo_import/workbook.py`, `organization/tests/test_llo_import_workbook.py`
- Modify: the command (after execution, it writes the copy and passes `output_path` to the report)

**Interfaces:**
- Consumes: `ImportPlan` (Task 4), `LLOWorkbook` (Task 1).
- Produces:
  - `output_path_for(path: Path, now: datetime) -> Path`, for example
    `Connect LLO Directory - imported 2026-11-11 1430.xlsx`.
  - `write_results(workbook: LLOWorkbook, plan: ImportPlan, output_path: Path) -> None`.

**Rules:**

- For each status, write these values:

  | Plan status | Connect ID | Connect Import Status |
  |---|---|---|
  | `CREATED` | contact `contact_id` / organization `slug` | `Created` |
  | `ALREADY_EXISTED` | contact `contact_id` / organization `slug` | `Already existed` |
  | `ALREADY_IMPORTED` | left as it is | left as it is |
  | Any other | empty | empty |

- **Organizations tab.** Write the `Organizations` tab row of every organization plan that has a `source` row. For
  an organization that already existed and was linked by name, write the row with the same exact name, but only if
  exactly one such row exists.
- **Finding the columns.** Find each result column by its header. If it's missing, add it at `max_column + 1` of
  that sheet.
- **Saving.** Save with `book.save(output_path)` and never to `workbook.path`.

- [ ] **Step 1: Write the failing tests**
  - `test_output_path_for`
  - `test_writes_ids_and_statuses` (created contact, already-existed contact, created organization)
  - `test_reuses_existing_result_columns`: on a rerun, the header appears once.
  - `test_appends_after_trailing_unnamed_columns`: an organization tab with data in an unnamed column 26 gets the new
    headers at 27/28, and column 26 is untouched.
  - `test_does_not_modify_input_and_keeps_other_tabs`: the input's bytes are unchanged. The copy still has an extra
    tab, a hyperlink, and a `=HYPERLINK(...)` formula.
- [ ] **Step 2: Run, expect FAIL**
- [ ] **Step 3: Implement**
- [ ] **Step 4: Run, expect PASS**
- [ ] **Step 5: Command tests**
  - `test_commit_writes_workbook_copy`: the copy exists, and its path is in stdout.
  - `test_rerun_on_output_creates_nothing`: running `--commit` again on the copy creates no records, and the report
    counts the rows as already imported.
  - Run the command tests and expect PASS.
- [ ] **Step 6: Run the whole organization test suite**
  `pytest commcare_connect/organization -q`: everything passes.
- [ ] **Step 7: Manual check against the real workbook (dry run)**
  `./manage.py import_llo_contacts "Connect LLO Directory.xlsx" --user-email <a local user>`: the command prints the
  report without errors. Look over the counts against the spec's known issues (52 organization names not found, 11
  duplicated emails, `DRC` countries, and so on). Don't commit the workbook.
- [ ] **Step 8: Commit**: `feat(organization): record Connect IDs of imported rows in a copy of the workbook`
