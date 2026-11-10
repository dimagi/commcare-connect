# Contact import from the LLO Directory spreadsheet

## Intent

The partnerships team keeps the LLO Directory as an Excel workbook (`Connect LLO Directory.xlsx`). Its **Contacts**
tab lists people at partner organizations, and its **Organizations** tab holds those organizations' profiles. We
want those contacts in Connect as `Contact` records linked to the right `Organization`, creating an organization
from its Organizations-tab row when Connect doesn't have it yet.

The sheet is maintained by hand and is messy: missing emails, several addresses in one cell, the same email on two
rows, several main contacts for one organization, organization names that don't match exactly, country and sector
names that aren't Connect's. So the import is a management command with a **dry run** that reports everything that
would stop a row from importing, so the team can fix the sheet, and a **real run** that imports what is valid. The
command will be run more than once as the sheet is corrected.

## Goals

- Import new contacts from the Contacts tab and link each to exactly one organization.
- Create an organization from the Organizations tab when a contact's organization isn't in Connect, but only when
  that row has everything Connect requires.
- A dry run (the default) that writes nothing and prints a report of what would be created and what is blocked and
  why, precise enough to fix the sheet from.
- Safe reruns: rows already imported are skipped and reported, not errors.

## Non-goals

- Updating anything that already exists. The import **only adds**: it never changes an existing organization or
  contact, including which contact is an organization's main contact.
- Importing organizations that no contact refers to. The Organizations tab is read only to create organizations
  that contacts need.
- Reading any tab other than Contacts and Organizations.
- Creating primary sectors or countries. Those lists are managed in Connect.
- A UI, or a downloadable/CSV report.

## Anti-goals

- **Linking a contact to the wrong organization.** Any ambiguity fails the row.
- **Guessing values.** A value that can't be mapped as specified blocks the record and is reported; it is never
  guessed, defaulted or silently dropped.
- **Changing state that already exists in Connect** (see non-goals).
- **Creating near-duplicate organizations.** Connect's own case-insensitive duplicate name check stays in force.

## The command

```bash
./manage.py import_llo_contacts <path-to-xlsx> --user-email <email> [--commit]
```

- Without `--commit` it is a dry run: validation only, no writes.
- `--user-email` (required) must belong to an existing Connect user; the command fails before reading the sheet if
  it doesn't. Created records get that email in `created_by`/`modified_by`, and writes are wrapped in
  `pghistory.context(username=..., user_email=...)` so the audit history names the person (see
  `docs/audit-history.md`).
- The workbook must contain tabs named `Contacts` and `Organizations`. Columns are found by their header text in
  row 1 (listed below), not by position; a missing tab or column fails the command up front with a clear message.
- Rows whose cells are all empty are ignored. Reported row numbers are the sheet's own row numbers (header = row 1).

## How the dry run validates

The dry run does **not** write and roll back. It validates with Connect's own forms (`is_valid()` without
`save()`) plus explicit checks for what those forms can't see. Rationale (decided): the main concern is whether
organizations' countries, sectors and required fields map, which form validation covers, and a write-and-rollback
run isn't worth the database load.

- **Organizations** are validated with `OrganizationDirectoryForm`, the form behind the Organization Directory's
  "Add organization" page. It requires name, short name, countries, regions and primary sectors, validates URLs,
  choices and emails, and rejects a name that matches an existing organization case-insensitively.
- **Contacts** are validated with `ContactForm` (requires name, email, organization; rejects an email that already
  belongs to a contact). For a contact whose organization will be created by this run, the organization field
  can't validate yet, so every other field is validated and the contact is reported as linking to the new
  organization; if that organization is blocked, the contact is blocked too.
- **Explicit checks** for rules that only the database or the sheet as a whole can see are listed under the row
  rules below.

The real run (`--commit`) runs exactly the same checks and then writes, re-checking each record just before it is
saved so that anything that changed since the dry run is still caught. Each record is handled on its own: a failed
row is reported and the run continues. An organization and the contacts that depend on it should be written
together, so a contact never ends up half-imported against an organization that failed.

## Matching a contact to an organization

The contact's Organization Name, trimmed of surrounding whitespace, is compared **exactly, case-sensitively**
against Connect organization names.

| Result | Outcome |
|---|---|
| Exactly one match, not archived | Link to it. Its details are not updated, even if the sheet's differ. |
| Exactly one match, archived | Row blocked: organization is archived. |
| More than one match | Row blocked: ambiguous, listing the matching organizations' slugs. |
| No match | Look the name up (same exact rule) on the Organizations tab. |
| — one Organizations-tab row | Create the organization from that row if it validates; otherwise block it and its contacts. |
| — several Organizations-tab rows | Blocked: the name is ambiguous in the sheet. |
| — no Organizations-tab row | Row blocked: organization not found in Connect or the sheet. |

A name that differs from an existing organization only by case (e.g. sheet `acme`, Connect `ACME`) has no exact
match, and creation is then refused by the form's case-insensitive duplicate check. The report should say that
the name differs only in case from the existing organization, so it's easy to fix.

Several contacts can point at one new organization; it is created once.

A new organization is created only if at least one of its contacts will be imported. If every contact that needs
it is blocked or skipped, the organization is not created and is reported as "not created: no importable
contacts", with its validation result, so the team still learns whether its row is complete. In the real run, the
organization and its contacts are saved in one transaction; if none of its contacts can be saved after the
re-check, the organization is rolled back with them.

The lookup on the Organizations tab is case-sensitive as well (decided: names must match case). In the current
workbook about 33 contact rows differ from their Organizations-tab row only by case (e.g.
`Hope and Awareness Foundation` / `Hope And Awareness Foundation`). These are reported with the hint
"differs only in case from Organizations row N", the same hint used against Connect.

## Contact row rules

A contact row is skipped and reported when any of these holds. All applicable reasons for a row are reported, not
just the first.

- **Email already exists in Connect** (case-insensitive, as `ContactForm` and the `unique_contact_email` constraint
  do): reported as *skipped — already exists*. This is the expected outcome on reruns, so the report keeps it
  separate from errors.
- **Same email on more than one row of the sheet** (case-insensitive, trimmed): **every** such row is reported, not
  only the later ones.
- **Main POC in the sheet, and the organization already has a main contact in Connect**: skipped. The existing main
  contact keeps the role. (Saving would otherwise silently unmark the existing one; see `Contact.save`.)
- **Several rows in the sheet marked Main POC for the same organization**: all of them are reported.
- Organization not resolvable (see matching above), or the organization it needs is blocked.
- A required field is missing or any value can't be mapped (see field mappings).

## Field mappings

General rules: text is trimmed of surrounding whitespace; an empty cell is "no value"; Excel numbers arrive as
floats (`50.0`) and whole-number fields take them only if they have no fractional part. Any value that doesn't map
as described blocks that record and is reported with the column, the value and why.

### Contacts tab → `Contact` (via `ContactForm`)

| Sheet column | Field | Mapping |
|---|---|---|
| Contact Full Name | `name` (required) | As-is. |
| Organization Name | `organization` (required) | See matching. |
| Email Address | `email` (required) | Exactly one address. A cell holding several addresses is reported, not split. |
| Role / Title | `title` | As-is. |
| Main POC? | `is_main_poc` | `Yes` → true; `No` or empty → false; anything else is reported. |
| Phone Number | `phone` | Text. A number Excel stored as a float is written as its integer digits (`2348012345678.0` → `2348012345678`). Max 32 characters (form validates). |
| Contact Notes (e.g. best way to contact) | `notes` | As-is. |

### Organizations tab → `Organization` (via `OrganizationDirectoryForm`)

| Sheet column | Field | Mapping |
|---|---|---|
| Organization Name | `name` (required) | As-is. |
| Short Name | `short_name` (required) | As-is. Max 40 characters (form validates). |
| Countries of Operation | `countries` (required) | Split on commas, except commas inside double quotes, so `"Congo, the Democratic Republic of the"` is read (and reported) as one value, quotes removed. Each name must **exactly** match a `Country.name` in the database the command runs against. No aliases (decided). Connect's names come from `opportunity/migrations/0092_…` (`Democratic Republic of the Congo`, `United Republic of Tanzania`, `Ivory Coast`, …) and differ from the workbook's own `Country List` tab, so `DRC`, `Tanzania`, `"Tanzania, United Republic of"`, `"Congo, the Democratic Republic of the"`, `Côte d'Ivoire` and similar are reported as unknown. To make those reports actionable, an unknown country is reported together with the hint that names must match Connect's country list. |
| Regions/States of Operation | `regions` (required) | As-is. |
| Primary Sector(s) | `primary_sectors` (required) | Split on commas, except commas inside parentheses (`WASH (Water, Sanitation, Hygiene)` is one value). Each must exactly match, case included, a `PrimarySector.name` in the database the command runs against; sectors exist only in the database, so what matches depends on the environment. The sheet mixes `WASH` / `WASH (Water, Sanitation, Hygiene)` and `Health` / `health`; whichever doesn't match is reported. Unknown sectors are reported, never created. |
| Has Used Connect | `has_used_connect` | `X`/`x` → true; empty → false; anything else is reported. |
| Year of Establishment | `year_of_establishment` | Whole number; form validates the range. |
| Org Team Size | `team_size` | Must equal a `TeamSizeRange` value (`1-10`, `11-50`, `51-200`, `201-500`, `500+`). `Medium`, `Small` etc. are reported. |
| No. of FLWs Managed | `flws_managed` | Whole number. |
| Website | `website` | Passed to the form (URL validation). |
| Office Address | `office_address` | As-is. |
| Email addresses from Contacts sheet | `contact_emails` | Split on commas, semicolons, whitespace and new lines; one address per line; form validates each. |
| EOIs Applied for (Add Link to EOI) | `eoi_links` | See below. One URL per line; form validates each. |
| Organization Notes | `notes` | As-is. |
| Latest MSA Link | `latest_msa_link` | Passed to the form (URL validation). |
| Latest Work Order Link | `latest_work_order_link` | Passed to the form (URL validation). |
| All other columns (HQ City, HQ Basis, HQ Source, …) | — | Ignored. |

`status` is left empty; there is no column for it.

**EOI links.** The link often isn't in the cell's text. In the current workbook the 389 filled cells come in
these shapes, and the URLs are collected from all of them:

1. A cell hyperlink behind title text (158 cells) — use the hyperlink's target.
2. An `=HYPERLINK("url","title")` formula (36 cells) — use the formula's URL. (The sheet must be read with formulas,
   not cached values, for this column.)
3. A URL written in the text, e.g. `Title — https://…` (about 150 cells) — use that URL.

A cell can list several EOIs, one per line. Excel keeps only one hyperlink per cell, so in a multi-line cell whose
link is a cell hyperlink, only one line's URL is known. The column is optional, so an empty cell is fine, but any
line that is not a link (including text such as `None yet, but has deployed Connect`) blocks the organization
(decided). Duplicate URLs are stored once.

There are no warnings and no partial imports: an optional field may be empty, but a value that is present and
can't be mapped blocks its record, in any field.

## The report

Printed to the terminal as plain text, to be copied and shared with the team by hand. It must read on its own
without the command's options in view. Contents:

- A header saying whether this was a **dry run** (nothing written) or a **real run**, with the file name.
- Counts: contacts to create / created, organizations to create / created, rows skipped as already existing, rows
  blocked.
- Organizations that will be (or were) created, each with the sheet rows of the contacts that need it.
- Blocked organizations: Organizations-tab row number, name, and each problem (column, value, reason), followed by
  the contact rows blocked because of it.
- Blocked contact rows: Contacts-tab row number, name, email, organization name, and every reason.
- Skipped rows (email already in Connect): row number, name, email, and the organization it already belongs to.
Grouping blocked items by reason or by organization is left to the builder, as long as each problem names the tab,
row and column needed to fix it.

## Constraints

- Follow AGENTS.md: guard against missing configuration, `logger` rather than prints for diagnostics (the report
  itself is the command's output and goes to stdout), tests in `commcare_connect/organization/tests/`.
- Use the existing forms for validation, not re-implemented field rules, so the import stays in step with the
  Organization Directory UI.
- Reading `.xlsx` uses openpyxl, already installed through `tablib[xlsx]`. No new dependency.
- Creating organizations and contacts must not have side effects outside the database (emails, Celery tasks). None
  are known today; the builder should confirm.

## Decisions made by Claude (for review)

- Only organizations referenced by a contact are created; the rest of the Organizations tab is ignored.
- An organization is created only when at least one of its contacts imports, and is rolled back with them if none
  can be saved.
- "Differs only in case" hints, against both Connect and the Organizations tab.
- Reasons for blocking are reported together, not just the first.
- Several sheet rows marked Main POC for one organization: all are reported (mirrors the duplicate-email rule).
- `--user-email` for attribution and audit history.
- Matched existing organizations are linked without reporting differences between the sheet and Connect.

## Left to the builder

- Module layout (a reader/mapper module plus a thin command is the expected shape), and how the report is
  formatted within the contents above.
- Transaction boundaries for the real run, within "an organization and its contacts are written together; one
  failure doesn't stop the rest".
