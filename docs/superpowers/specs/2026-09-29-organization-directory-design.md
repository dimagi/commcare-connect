# Organization directory

## Goal

Give Connect staff (and GSP) one place in Connect to find, view, edit, create, archive and
export organizations, replacing the spreadsheet they maintain today.

This is the first slice of the directory. Contacts, EOI/RFP, MSA and work order records do not
exist in code yet and are out of scope; the pages are laid out so they can be added as further
tabs and detail-page sections later.

"LLO Directory" is used only as the sidenav label. Everywhere else — code, URLs, templates and
the rest of the UI — the entity is an organization.

## Scope

In scope:

- Organizations listing with search, filters, and an Organizations / Archive tab split
- Read-only organization detail page
- Add and edit organization in a modal, from the listing and the detail page
- Archive and unarchive
- CSV / XLSX export of the filtered listing, generated in Celery
- Change history on `Organization`, recorded with pghistory and viewable in Django admin

Out of scope (follow-ups):

- Contacts, EOI/RFP, MSA and work order records, and everything that depends on them: a
  Contacts tab, a main point-of-contact column, a contacts filter, the "active contract"
  indicator, and deriving status from contracts
- Site-wide archival — hiding archived organizations outside the directory, blocking their
  workspaces. Excluding them from KPIs is covered in [KPI exclusion](#kpi-exclusion) as a
  separate, optional ticket
- Opportunity and date-created filters
- Bulk selection and bulk edit
- Change history shown on the site
- CSV upload of organizations

## Access

Every directory view — listing, detail, add, edit, archive, export, export status and
download — requires `WORKSPACE_ENTITY_MANAGEMENT_ACCESS` (`users.workspace_entity_management_access`).
It already means "can see and create all organizations" (`Organization.visible_to`, the
header's Create Organization link) and is granted from the internal permission management page.

Its description still reads "Can manage LLO Entities for organizations", left over from the
removed `LLOEntity` model. It changes to "Can view and manage all organizations" (a
`User.Meta.permissions` change, so it needs a migration).

The views are class-based and use `LoginRequiredMixin` + `PermissionRequiredMixin` with
`raise_exception=True`. No waffle flag or switch: the permission is the gate.

## Data model

New fields on `Organization` (`commcare_connect/organization/models.py`):

| Field                         | Type                                                                             | Notes                                |
| ----------------------------- | -------------------------------------------------------------------------------- | ------------------------------------ |
| `status`                      | `CharField`, `OrganizationStatus` choices, `blank=True`                          | Existing rows get `""`, shown as "—" |
| `internal_partner_assessment` | `CharField`, `PartnerAssessment` choices, `blank=True`                           |                                      |
| `is_archived`                 | `BooleanField(default=False)`                                                    |                                      |
| `archived_at`                 | `DateTimeField(null=True, blank=True)`                                           |                                      |
| `archived_by`                 | `ForeignKey(User, null=True, blank=True, on_delete=SET_NULL, related_name="+")` |                                      |

`OrganizationStatus` (`TextChoices`): `active` Active, `inactive` Inactive, `prospective`
Prospective. Status is set manually for now; once Contracts exist it will be derived from them.
The definitions shown in the UI live next to the choices, in one mapping that the listing
legend and the form help text both read:

- **Active** — 1+ open contracts
- **Inactive** — 0 open contracts, and 1+ previously active contracts
- **Prospective** — 0 open contracts, 0 previously active contracts, 1 active contact

`PartnerAssessment` (`TextChoices`): Superstar LLO, Strong LLO, Average LLO, Weak LLO,
Do Not Engage, N/A, New LLO. These are the business's own labels for the choice values, so
they keep "LLO"; the code names do not.

Archive is independent of status: an archived organization keeps its last status.

`short_name` keeps its existing 40-character limit.

### Change history

- `@pghistory.track()` on `Organization`, all fields — insert, update and delete events. This
  records changes from every writer: the directory, the signup wizard, Django admin, org merges
  (which delete the source organization) and system code.
- `countries` and `primary_sectors` are many-to-many, so row triggers on the organization table
  do not see them. Their auto-created through models are tracked too (insert and delete events),
  so adding or removing a country or sector is recorded.
- Actor details come from the existing `CustomPGHistoryMiddleware` context (`username`,
  `user_email`).
- The event models are registered in Django admin with `AuditEventAdmin`
  (`commcare_connect/utils/admin.py`), read-only, as the program event admins are. No history is
  shown on the site.

## URLs

Top level, outside `/a/<org_slug>/`, since staff work across organizations. A new
`organization_directory` URL namespace in the `organization` app:

| URL                                   | View              | Method    |
| ------------------------------------- | ----------------- | --------- |
| `/organizations/`                     | listing           | GET       |
| `/organizations/new/`                 | add modal form    | GET, POST |
| `/organizations/<slug>/`              | detail            | GET       |
| `/organizations/<slug>/edit/`         | edit modal form   | GET, POST |
| `/organizations/<slug>/archive/`      | archive           | POST      |
| `/organizations/<slug>/unarchive/`    | unarchive         | POST      |
| `/organizations/export/`              | start export      | POST      |
| `/organizations/export/<task_id>/`    | export progress   | GET       |
| `/organizations/export/<task_id>/download/` | export download | GET   |

`new` and `export` are declared before `<slug>` so they are not captured as slugs.

## Navigation

- A sidenav item labelled "LLO Directory", shown to holders of the permission, in the internal
  block of `templates/layouts/sidenav.html` next to Internal Features, linking to the listing.
- Breadcrumbs: Internal › Organizations (› organization name on the detail page).

## Listing

`OrganizationListView`: `FilterMixin` + django-tables2 `SingleTableView`, following
`OpportunityList` (`commcare_connect/opportunity/views.py`).

Header: title "Organizations", subtitle "All organizations in the Connect network.",
**Export** and **Add Organization** buttons.

Tabs: **Organizations** (count of non-archived) on the left, **Archive** (count of archived) on
the right. The tab is a query parameter; each tab applies `is_archived=False` / `True` before the
filters.

### Filters — `OrganizationFilterSet`

| Filter        | Behaviour                                                                  |
| ------------- | -------------------------------------------------------------------------- |
| Search        | Case-insensitive match on name, short name, country name or sector name   |
| All Countries | Multi-select (tom-select), matches organizations in any selected country   |
| All Sectors   | Multi-select (tom-select), matches organizations in any selected sector    |
| Any Status    | Multi-select of the three statuses                                         |

A **Reset** link clears the filters but keeps the tab. The result count ("14 organizations")
sits on the right. Search, country and sector filters use `.distinct()` so multi-matches don't
duplicate rows. The page loads `bundles/js/tomselect-bundle.js` and `bundles/css/tomselect.css`
itself.

### Table — `OrganizationDirectoryTable`

| Column            | Content                                                  | Sortable |
| ----------------- | -------------------------------------------------------- | -------- |
| Organization      | Name (link to detail) with short name beneath            | yes      |
| Status            | Badge, "—" when blank; header legend with definitions    | yes      |
| Countries         | Comma-separated names                                    | no       |
| Primary Sector(s) | Comma-separated names                                    | no       |
| FLWs              | `flws_managed`                                           | yes      |
| Actions           | Edit (opens modal), Archive or Unarchive (confirm modal) | no       |

The whole row opens the detail page: the name is a real `<a>` whose click area is stretched over
the row with a `::after { position: absolute; inset: 0 }` overlay on a `relative` row. The action
buttons sit above the overlay. This is added as a predefined class (e.g. `.row-link`) in
`tailwind/tailwind.css`. No JavaScript; new-tab, keyboard and screen-reader behaviour are the
normal link behaviour.

Querysets prefetch `countries` and `primary_sectors`.

## Detail page

`OrganizationDetailView`, read-only.

- Header: name, short name, status badge, "Archived" badge when archived, **Edit** button
  (opens the same modal as the listing) and **Archive** / **Unarchive** button.
- Profile card: every field on the form, plus the created and modified dates.
- Nothing is shown for Contacts, EOI/RFP, MSA or work orders yet; they become sections here later.

## Add / edit modal

One modal partial, loaded with htmx into an Alpine modal, used by the listing's Add
Organization button, the row Edit action and the detail page's Edit button. Title "Add
Organization" or "Edit Organization / Editing <name>"; footer note "Contacts and EOIs are
linked after the record is saved."; Cancel and Save buttons.

On a valid POST the view saves and responds with `HX-Redirect` back to the page the modal was
opened from (listing with its filters, or detail page). An invalid POST re-renders the form
inside the modal with errors. tom-select is initialised on `htmx:afterSettle` by the existing
`static/js/tomselect.js`.

### Form — `OrganizationDirectoryForm`

A new single-page `ModelForm`, separate from the signup wizard (`OrganizationProfileForm`) and
the admin form (`AdminOrganizationForm`).

Layout:

| Left                                            | Right                       |
| ----------------------------------------------- | --------------------------- |
| Organization Name (full width)                  |                             |
| Short Name (full width)                         |                             |
| Status                                          | Internal Partner Assessment |
| Year of Establishment                           | Has Used Connect (Yes / No) |
| Org Team Size                                   | No. of FLWs Managed         |
| Countries of Operation (full width, tom-select) |                             |
| Regions / States of Operation (full width)      |                             |
| Primary Sector(s) (full width, tom-select)      |                             |
| Website                                         | Office Address              |
| Organization Notes (full width)                 |                             |

Required on both create and edit: Organization Name, Short Name, Countries of Operation,
Regions / States of Operation, Primary Sector(s). Everything else is optional.

Validation reuses the wizard's rules, moved to shared helpers where needed:

- name is unique case-insensitively, excluding the organization being edited
- `validate_year_of_establishment`

Not on the form: `contact_emails` and `eoi_links` (stay editable in Django admin until Contacts
and EOI records replace them), and `program_manager`, `funder`, `verified`, `is_test` (admin only).

Behaviour:

- Creating does **not** add the staff user as a member, unlike the signup wizard.
- Renaming does not change the slug (existing behaviour of `Organization.save`), so workspace
  URLs keep working.

## Archive

- `archive_organization(org, user)` sets `is_archived=True`, `archived_at=now`,
  `archived_by=user`; `unarchive_organization(org)` clears all three. Both are plain functions
  in the organization app, called by thin POST-only views.
- Triggered from the row action or the detail page, each behind an Alpine confirmation modal.
- The views redirect to where they were triggered from with a success message.
- Archiving only affects the directory: the organization moves to the Archive tab. Everything
  else in the app is unchanged.

## Export

Generated in Celery, since live environments have many organizations. Reuses the invoice report
flow (`reports/views.py`, `utils/celery.py`).

1. The Export button POSTs the current filters and tab to the export view.
2. The view validates them with `OrganizationFilterSet` and dispatches
   `export_organizations_task(filters_data, user_id, export_format)` inside
   `transaction.on_commit`. It responds with the progress component
   (`components/upload_progress_bar.html` via `render_export_status`).
3. The task rebuilds the filtered queryset, runs it through `OrganizationExportTable` with
   `TableExport`, saves the file with `get_export_storage()` (not the hard-coded S3 storage the
   invoice task uses, so local and CI work without S3), and returns the filename and the
   requesting user's id.
4. The status view polls; on success the download view serves the file with
   `download_export_file`, named `organizations_<YYYY-MM-DD>.<csv|xlsx>`.

Formats: CSV and XLSX (tablib[xlsx] is already a dependency).

Ownership: the status and download views compare the task result's user id with the requester
and 404 otherwise. (The invoice export has no such check; that is not changed here.)

`OrganizationExportTable` columns: name, short name, status, internal partner assessment,
countries, regions, primary sectors, FLWs managed, team size, year of establishment, has used
Connect, website, office address, notes, archived, date created.

## KPI exclusion

The requirement is that an archived organization is not counted towards KPIs. What exists today:

**In-app KPI report** — Delivery Stats (`reports:delivery_stats_report`,
`DeliveryStatsReportView`), built by `get_table_data_for_year_month` and
`get_activated_connect_user_counts_cumulative` in `commcare_connect/reports/helpers.py`.
Its metrics — active FLWs, services delivered, FLW amount earned and paid, intervention and
organization funding deployed, average and maximum time to payment, average top-earned FLWs,
cumulative activated users — are all activity aggregates over `CompletedWork`, `Payment` and
invoices, reached through the opportunity (`opportunity_access__opportunity__…`,
`invoice__opportunity__…`). They already exclude test opportunities with `…opportunity__is_test=False`.

- None of them counts organizations. Excluding archived organizations means excluding the
  **activity** of their opportunities: add `…opportunity__organization__is_archived=False`
  beside each `is_test=False` filter.
- The report's Organization filter lists all organizations
  (`DeliveryReportFilters.organization`); it would drop archived ones.
- The ConnectID user totals come from ConnectID and are not organization-scoped.

**Caveat.** Archiving only affects the directory, so an archived organization can still run live
opportunities. Excluding it from the KPI report would then hide real delivery and payments. The
exclusion is only safe for archived organizations with no activity (e.g. duplicates), so it is
kept as a separate ticket to be picked up once site-wide archival is decided.

**Outside the app.** No organization counts are computed in Connect. Counts of organizations are
presumably built downstream (e.g. Superset) from the organization profile export
(`LLOEntityDataView`, `/export/llo_entity/`, `LLOEntityDataSerializer`), which does not expose the
new fields. Adding `status`, `internal_partner_assessment` and `is_archived` to that export lets
downstream dashboards exclude archived organizations. The downstream dashboards themselves are
not in this repository.

The invoice report is a per-invoice listing, not a KPI, and is unaffected.

## Tickets

Each ticket is written to be filed as-is. **Requirement** describes the change in plain terms
for anyone reading the ticket; **Technical specification** gives the developer what they need
to build it.

---

### 1. Record status, partner assessment and archive details on organizations

**Requirement**

Staff need to record three new things about each organization:

- **Status** — one of:
  - **Active**: 1+ open contracts
  - **Inactive**: 0 open contracts, and 1+ previously active contracts
  - **Prospective**: 0 open contracts, 0 previously active contracts, 1 active contact

  Staff set the status by hand for now. Once contracts are tracked in Connect it will be worked
  out automatically. These definitions must be shown wherever status is shown or chosen.
- **Internal Partner Assessment** — one of: Superstar LLO, Strong LLO, Average LLO, Weak LLO,
  Do Not Engage, N/A, New LLO.
- **Archived** — whether the organization has been archived (e.g. because it is a duplicate),
  when, and by whom. Archiving keeps the record; it does not delete it. Archiving does not change
  the organization's status.

Existing organizations start with no status and no assessment, and are not archived.

All three are visible and editable in Django admin (archive date and user are read-only there).

The internal permission that controls who can manage all organizations is currently described as
"Can manage LLO Entities for organizations", which is out of date. Its description becomes
"Can view and manage all organizations".

**Technical specification**

- New fields on `Organization` (`commcare_connect/organization/models.py`):

  | Field                         | Definition                                                                                                        |
  | ----------------------------- | ----------------------------------------------------------------------------------------------------------------- |
  | `status`                      | `CharField(max_length=20, choices=OrganizationStatus.choices, blank=True)`                                        |
  | `internal_partner_assessment` | `CharField(max_length=30, choices=PartnerAssessment.choices, blank=True)`                                         |
  | `is_archived`                 | `BooleanField(default=False)`                                                                                     |
  | `archived_at`                 | `DateTimeField(null=True, blank=True)`                                                                            |
  | `archived_by`                 | `ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")`        |

- `OrganizationStatus(TextChoices)`: `active` / `inactive` / `prospective`. Keep the definitions
  in one mapping next to the choices (e.g. `OrganizationStatus.DEFINITIONS`) for the UI to read.
- `PartnerAssessment(TextChoices)`: `superstar_llo`, `strong_llo`, `average_llo`, `weak_llo`,
  `do_not_engage`, `not_applicable` (label "N/A"), `new_llo`.
- Django admin: add `status` and `internal_partner_assessment` to `AdminOrganizationForm`
  (`commcare_connect/users/forms.py`); show `archived_at`, `archived_by` read-only; add `status`
  and `is_archived` to `list_display` / `list_filter` in `commcare_connect/organization/admin.py`.
- Permission: change the `workspace_entity_management_access` description in
  `User.Meta.permissions` (`commcare_connect/users/models.py`); needs a `users` migration.

**Depends on:** —

---

### 2. Keep a change history for organizations

**Requirement**

Staff need to be able to see who changed an organization's details and when — including changes
made by any user or by the system, from any part of Connect (the new directory, organization
sign-up, Django admin, merging duplicate organizations). Changes to an organization's countries
and primary sectors are included. The history is kept even if the organization is later deleted.

The history is viewed in Django admin only; it is not shown on the site.

**Technical specification**

- `@pghistory.track()` on `Organization`, all fields, insert / update / delete events. See
  `@pghistory.track(fields=["funder"])` on `Program` for existing usage.
- Row triggers don't see M2M changes, so also track the auto-created through models
  `Organization.countries.through` and `Organization.primary_sectors.through` (insert and delete).
- The actor (`username`, `user_email`) is already put on the pghistory context by
  `CustomPGHistoryMiddleware` (`commcare_connect/utils/middleware.py`).
- Register the event models in admin with `AuditEventAdmin` (`commcare_connect/utils/admin.py`,
  read-only, provides `changed_by` / `changed_by_email`), as `commcare_connect/program/admin.py`
  does for program events.

**Depends on:** ticket 1, so the new fields are tracked from their first change.

---

### 3. Organizations listing page

**Requirement**

Connect staff (and GSP) need one page listing all organizations so they can find the one they
need. It is reached from a new sidenav item labelled **LLO Directory**, shown only to users with
the permission to manage all organizations. Everywhere else the page talks about
"organizations".

The page:

- Breadcrumbs "Internal › Organizations", title "Organizations", subtitle "All organizations in the
  Connect network."
- Two tabs: **Organizations** (with the number of non-archived organizations) and **Archive**
  (with the number of archived organizations). Archived organizations appear only on the Archive
  tab.
- A search box ("Search organization, country, sector…") that matches the organization's name,
  short name, countries or sectors.
- Filters:
  - **Countries** — pick one or more; shows organizations operating in any of them
  - **Sectors** — pick one or more; shows organizations in any of them
  - **Status** — pick one or more of Active, Inactive, Prospective
- A **Reset** link that clears search and filters (staying on the same tab), and the number of
  matching organizations.
- A table with columns:
  - **Organization** — full name, with the short name underneath
  - **Status** — shown as a coloured badge, "—" if not set. The column header explains the
    statuses: Active — 1+ open contracts; Inactive — 0 open contracts, and 1+ previously active
    contracts; Prospective — 0 open contracts, 0 previously active contracts, 1 active contact
  - **Countries**
  - **Primary Sector(s)**
  - **FLWs** — number of FLWs managed
- Sorting by Organization, Status and FLWs.

Later tickets add an Add Organization button, an Export button, row actions and clicking through
to an organization's detail page.

**Technical specification**

- Access on all directory views: `LoginRequiredMixin` + `PermissionRequiredMixin`,
  `permission_required = WORKSPACE_ENTITY_MANAGEMENT_ACCESS`, `raise_exception=True`.
- URL `/organizations/`, top level (not under `/a/<org_slug>/`), in a new `organization_directory`
  namespace in the `organization` app, included from `config/urls.py`. Later tickets add
  `new/`, `export/…`, `<slug>/`, `<slug>/edit/`, `<slug>/archive/`, `<slug>/unarchive/`; literal
  paths go before `<slug>/`.
- `OrganizationListView`: `FilterMixin` (`opportunity/filters.py`) + django-tables2
  `SingleTableView`, following `OpportunityList` in `opportunity/views.py`. Prefetch `countries`,
  `primary_sectors`. Tab via query parameter (e.g. `?tab=archive`), applied as
  `is_archived=False/True` before the filterset.
- `OrganizationFilterSet`: search `icontains` over `name`, `short_name`, `countries__name`,
  `primary_sectors__name`; `ModelMultipleChoiceFilter` on `countries` and `primary_sectors`;
  `MultipleChoiceFilter` on `status`. Use `.distinct()` for the M2M joins.
- Countries / sectors widgets get `data-tomselect="1"`; the page must load
  `bundles/js/tomselect-bundle.js` and `bundles/css/tomselect.css`.
- `OrganizationDirectoryTable`: status badge with predefined classes from `tailwind/tailwind.css`
  (`badge` + `status-active` / `status-inactive` / `badge-indigo` for Prospective). Status
  definitions read from `OrganizationStatus.DEFINITIONS`.
- Sidenav: `templates/layouts/sidenav.html`, inside the `show_internal_features` block next to
  Internal Features, guarded by `perms.users.workspace_entity_management_access`, using
  `components/sidenav-items.html` with `namespace='organization_directory'`.

**Depends on:** ticket 1.

---

### 4. Organization detail page

**Requirement**

Staff need to open an organization and see its full profile on one page. Clicking anywhere on an
organization's row in the listing opens it (opening in a new tab works as for any link).

The page shows:

- Breadcrumbs "Internal › Organizations › <organization name>".
- The organization's name, short name, a status badge, and an "Archived" badge if it is archived.
- Its profile: Organization Name, Short Name, Status, Internal Partner Assessment, Year of
  Establishment, Has Used Connect (Yes / No), Org Team Size, No. of FLWs Managed, Countries of
  Operation, Regions / States of Operation, Primary Sector(s), Website (as a link), Office
  Address, Organization Notes, Date Created, Last Modified. Empty values show "—".

Archived organizations can be viewed too. Contacts, EOI/RFP, MSA and work order records will be
added to this page later; nothing is shown for them now. Later tickets add Edit and Archive
buttons here.

**Technical specification**

- `OrganizationDetailView` (`DetailView`, `slug_field="slug"`) at `/organizations/<slug>/`,
  same access mixins as the listing, prefetching `countries` and `primary_sectors`.
- Whole-row link in the listing, no JavaScript: the Organization column's name becomes a real
  `<a>`; the row is `position: relative`; the link's `::after` is `position: absolute; inset: 0`.
  Clickable elements added to rows later sit above it (`position: relative` + higher `z-index`).
  Add it as a predefined class (e.g. `.row-link`) in `tailwind/tailwind.css`.

**Depends on:** ticket 3.

---

### 5. Add and edit organizations

**Requirement**

Staff need to add organizations that didn't come through self sign-up, and edit any
organization's details, without using a spreadsheet.

- An **Add Organization** button on the listing, and an **Edit** action on each listing row and
  on the detail page. Each opens the same form in a pop-up: "Add Organization", or "Edit
  Organization — Editing <name>".
- The form, laid out in two columns:
  - Organization Name
  - Short Name
  - Status | Internal Partner Assessment
  - Year of Establishment | Has Used Connect (Yes / No)
  - Org Team Size | No. of FLWs Managed
  - Countries of Operation (pick one or more)
  - Regions / States of Operation
  - Primary Sector(s) (pick one or more)
  - Website | Office Address
  - Organization Notes
- **Required:** Organization Name, Short Name, Countries of Operation, Regions / States of
  Operation, Primary Sector(s) — when adding and when editing.
- Status choices are Active, Inactive, Prospective, with their definitions shown as help:
  Active — 1+ open contracts; Inactive — 0 open contracts, and 1+ previously active contracts;
  Prospective — 0 open contracts, 0 previously active contracts, 1 active contact.
- Internal Partner Assessment choices: Superstar LLO, Strong LLO, Average LLO, Weak LLO,
  Do Not Engage, N/A, New LLO.
- Two organizations can't have the same name (ignoring upper/lower case). Short Name can be up to
  40 characters. Year of Establishment follows the existing sign-up rules.
- The pop-up notes "Contacts and EOIs are linked after the record is saved." It has Cancel and
  Save buttons; errors are shown in the pop-up.
- After saving, staff return to where they opened the form (the listing keeps its filters). After
  adding from the listing, staff go to the new organization's detail page.
- The staff member adding an organization does not become a member of it.
- Renaming an organization does not change its workspace URL.
- Contact emails and EOI links are not on this form; they remain editable in Django admin until
  Contacts and EOIs are tracked properly. Program manager, funder, verified and test settings
  also stay admin-only.

**Technical specification**

- `CreateView` at `/organizations/new/`, `UpdateView` at `/organizations/<slug>/edit/`, same
  access mixins as the listing.
- New `OrganizationDirectoryForm(ModelForm)` in `organization/forms.py` — not a reuse of
  `OrganizationProfileForm` (sign-up wizard) or `AdminOrganizationForm`. Set `required=True` on
  `name`, `short_name`, `countries`, `regions`, `primary_sectors` (blank is allowed on the model).
  Reuse the wizard's validation — `clean_name` (strip, case-insensitive uniqueness excluding the
  instance) and `validate_year_of_establishment` — moving it to shared helpers rather than
  duplicating.
- Unlike `organization_create`, the create view must not add a `UserOrganizationMembership`.
  `Organization.save` only sets the slug on create, so renames keep it.
- Modal: one partial loaded with htmx into an Alpine modal. Valid POST → `HX-Redirect` to the
  originating page (or the new detail page after an add) with a success message; invalid POST
  re-renders the partial. Countries / sectors widgets get `data-tomselect="1"`; the existing
  `static/js/tomselect.js` initialises them on `htmx:afterSettle`. The listing's Edit icon sits
  above the row-link overlay.

**Depends on:** tickets 3 and 4.

---

### 6. Archive and unarchive organizations

**Requirement**

Staff need to archive an organization (for example a duplicate) so it drops out of the directory
while the record is kept, and to restore it if needed.

- An **Archive** action on each row of the listing's Organizations tab and on the detail page of
  a non-archived organization. It asks for confirmation ("Archive <name>? It will move to the
  Archive tab.") before archiving.
- An **Unarchive** action on each row of the Archive tab and on the detail page of an archived
  organization, also with a confirmation.
- Archiving records when it happened and who did it; unarchiving clears that. Neither changes the
  organization's status.
- After either action, staff return to where they were, with a message ("<name> archived." /
  "<name> restored.").
- Archiving only affects this directory. The organization's workspace, opportunities, programs
  and reports carry on as before.

**Technical specification**

- Functions in the organization app (e.g. `organization/directory.py`):
  `archive_organization(organization, user)` sets `is_archived=True`, `archived_at=timezone.now()`,
  `archived_by=user`; `unarchive_organization(organization)` clears all three. Save with
  `update_fields`; don't touch `status`.
- POST-only views at `/organizations/<slug>/archive/` and `/organizations/<slug>/unarchive/`, same
  access mixins, redirecting to the originating page.
- Confirmation is an Alpine modal. Row action icons sit above the row-link overlay.

**Depends on:** tickets 3 and 4.

---

### 7. Export organizations to a spreadsheet

**Requirement**

Staff doing ad hoc research need to download the organizations they are looking at as a
spreadsheet.

- An **Export** button on the listing, offering CSV or Excel (XLSX).
- The file contains exactly the organizations matching the current tab, search and filters.
- Columns, in order: Organization Name, Short Name, Status, Internal Partner Assessment,
  Countries of Operation, Regions / States of Operation, Primary Sector(s), No. of FLWs Managed,
  Org Team Size, Year of Establishment, Has Used Connect, Website, Office Address, Organization
  Notes, Archived (Yes / No), Date Created.
- Because there are many organizations, the file is prepared in the background: staff see
  progress, then a download link. The file is named `organizations_<date>.csv` / `.xlsx`.
- Only the person who requested an export can download it.

**Technical specification**

- Views: POST `/organizations/export/` (start), GET `/organizations/export/<task_id>/` (progress),
  GET `/organizations/export/<task_id>/download/`, same access mixins.
- Follow the invoice report export (`reports/views.py`: `export_invoice_report`, `export_status`,
  `download_export`; `utils/celery.py`: `render_export_status`, `download_export_file`).
- Start view: validate with `OrganizationFilterSet`, make cleaned data JSON-serialisable (instances
  → ids), dispatch with `transaction.on_commit(partial(export_organizations_task.delay, filters_data, user_id, export_format))`.
  400 on an unsupported format.
- `export_organizations_task` (`organization/tasks.py`): rebuild the tab + filterset queryset, build
  `OrganizationExportTable`, export with `django_tables2.export.TableExport`, save with
  `get_export_storage()` (not the hard-coded `ExportS3Boto3Storage` the invoice task uses, so local
  and CI work without S3), return `{"filename": ..., "user_id": ...}`. `download_export_file`
  currently expects `task.result` to be the filename — adapt it for the dict.
- Progress and download views 404 when the task's `user_id` isn't the requester's.
- Choice fields export their labels; M2M values as comma-separated names.

**Depends on:** ticket 3.

---

### 8. Include status, assessment and archived in the organization data export

**Requirement**

Connect doesn't count organizations itself; organization counts are built outside Connect from
the organization data export. So that those reports can leave out archived organizations — and
use status and partner assessment — the export needs to include each organization's Status,
Internal Partner Assessment and whether it is Archived. Archived organizations stay in the export,
marked as archived.

**Technical specification**

- Add `status`, `internal_partner_assessment`, `is_archived` to `Meta.fields` of
  `LLOEntityDataSerializer` (`data_export/serializer.py`), served by `LLOEntityDataView` at
  `/export/llo_entity/`. Stored values are exported (e.g. `active`, `strong_llo`; empty string when
  unset). Class and URL names stay unchanged for existing consumers.

**Depends on:** ticket 1.

---

### 9. Leave archived organizations out of the KPI report (on hold)

**On hold** until it is decided what archiving means across the whole of Connect. Today archiving
only affects the directory, so an archived organization can still be running live work; leaving
it out of the KPIs would hide real deliveries and payments.

**Requirement**

Archived organizations should not count towards KPIs. In the Delivery Stats KPI report, activity
from archived organizations' opportunities is left out of every figure — active FLWs, services
delivered, FLW amount earned and paid, intervention and organization funding deployed, average and
maximum time to payment, average top-earned FLWs, cumulative activated users — and archived
organizations no longer appear in the report's Organization filter. Total ConnectID users are
unaffected, as they don't belong to organizations.

**Technical specification**

- In `get_table_data_for_year_month` and `get_activated_connect_user_counts_cumulative`
  (`reports/helpers.py`), next to each existing `…opportunity__is_test=False` filter add
  `opportunity_access__opportunity__organization__is_archived=False` (CompletedWork / Payment) or
  `invoice__opportunity__organization__is_archived=False` (invoice queries).
- `DeliveryReportFilters.organization` (`reports/views.py`): queryset
  `Organization.objects.filter(is_archived=False).order_by("name")`.

**Depends on:** ticket 1.
