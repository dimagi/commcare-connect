# Organization directory 1: organizations listing

## Goal

Give Connect staff (and GSP) one page in Connect to find, add, edit and archive organizations,
replacing the spreadsheet they maintain today. This is the first phase of an internal
organization directory; later phases add more tabs (starting with Contacts) and features to the
same page.

## Scope

- A new organization `status` (Active, Inactive, Prospective, Archived) and latest MSA / work
  order links
- Organizations listing with search, filters, and Organizations / Contacts (placeholder) / Archive
  tabs, reached from an "Admin" group in the sidenav
- Add and edit organizations from the listing
- Archive action, refused while the organization has a live program

Not in this phase: organization detail page, contacts, EOI/RFP / MSA / work order records, status
derived from contracts, internal partner assessment, change history, export, and any effect of
archiving outside the directory (workspace access, KPIs, org pickers, emails, the API).

## Tickets

Each ticket is written to be filed as-is. **Requirement** describes the change in plain terms
for anyone reading the ticket; **Technical specification** gives the developer what they need
to build it.

---

### 1. Add status and agreement link fields to organizations

**Requirement**

Staff need to record three new things about each organization:

- **Status** — one of:
  - **Active**: 1+ open contracts
  - **Inactive**: 0 open contracts, and 1+ previously active contracts
  - **Prospective**: 0 open contracts, 0 previously active contracts, 1 active contact
  - **Archived**: archived by staff, e.g. a duplicate

  Staff set the status by hand for now. Later, Active, Inactive and Prospective will be worked
  out automatically from contracts. These definitions must be shown wherever status is shown or
  chosen.
- **Latest MSA Link** — a link to the organization's latest MSA.
- **Latest Work Order Link** — a link to the organization's latest work order.

Existing organizations start with no status and no links. All three are visible and editable in
Django admin, where status can also be filtered on.

**Technical specification**

- New fields on `Organization` (`commcare_connect/organization/models.py`):
  - `status = CharField(max_length=20, choices=OrganizationStatus, blank=True)`
  - `latest_msa_link = URLField(blank=True)`
  - `latest_work_order_link = URLField(blank=True)`
- `OrganizationStatus(TextChoices)`: `active`, `inactive`, `prospective`, `archived`. The
  definitions live in `ORGANIZATION_STATUS_DEFINITIONS`, a module-level mapping next to the
  choices, for the UI to read.
- `OrganizationQuerySet` with `archived()` and `unarchived()`, installed as
  `Organization.objects`.
- Django admin: the three fields are added to `AdminOrganizationForm`
  (`commcare_connect/users/forms.py`), and `status` to `list_display` / `list_filter` in
  `commcare_connect/organization/admin.py`.

**Depends on:** —

---

### 2. Organizations listing page

**Requirement**

Connect staff (and GSP) need one page listing all organizations so they can find the one they
need. It is reached from **LLO Directory** in a new **Admin** group in the sidenav, which also
holds the existing **Internal Features** link. Everywhere else the page talks about
"organizations".

The sidenav:

- **Admin** is a collapsible group: it starts expanded on the Internal Features and directory
  pages and collapsed elsewhere. Its items are indented under a guide line.
- **LLO Directory** shows only to users with the permission to manage all organizations; the
  group itself shows to anyone with an internal permission, as Internal Features did.
- With the sidebar collapsed to icons, the group's items are always shown.

The page:

- Breadcrumbs "Admin › Organizations", title "Organizations", subtitle "All organizations in the
  Connect network."
- Tabs:
  - **Organizations** — all organizations that aren't archived, with their number
  - **Contacts** — a placeholder for the upcoming contacts list: visible but disabled, marked
    "Coming soon"
  - **Archive** — archived organizations, with their number
- A search box ("Search organization, country, sector…") that matches the organization's name,
  short name, countries or sectors.
- Filters:
  - **Countries** — pick one or more; shows organizations operating in any of them
  - **Sectors** — pick one or more; shows organizations in any of them
  - **Status** — pick one or more of Active, Inactive, Prospective (Organizations tab only)
- An **Apply** button, a **Reset** link that clears search and filters (staying on the same tab),
  and the number of matching organizations.
- A table with columns:
  - **Name**
  - **Status** — a coloured badge, "—" if not set. An info icon in the column header shows the
    status definitions on hover.
  - **Primary Sectors**
  - **Org Team Size** — the team size bracket (e.g. "11-50")
  - **FLWs Managed**
  - **Added** — the date the organization was added to Connect
- Newest organizations first; sorting by Name, Status, FLWs Managed and Added. Org Team Size is
  not sortable, as its brackets don't sort meaningfully as text.

**Technical specification**

- Access on all directory views: `DirectoryAccessMixin` (`LoginRequiredMixin` +
  `PermissionRequiredMixin`, `permission_required = WORKSPACE_ENTITY_MANAGEMENT_ACCESS`,
  `raise_exception=True`).
- URLs: `directory_urlpatterns` in `organization/urls.py` — kept apart from `urlpatterns`, which is
  mounted under `/a/<org_slug>/` — included from `config/urls.py` as
  `path("organizations/", include((directory_urlpatterns, "organization_directory")))`. The
  listing is `organization_directory:list` at `/organizations/`.
- `OrganizationListView` in `organization/views.py`: `SingleTableMixin` + django-filter's
  `FilterView`, as `InvoiceReportView` does.
  - `ORGANIZATIONS_TAB` / `ARCHIVE_TAB` class attributes; `tab` comes from the `?tab=` query
    parameter, defaulting to the Organizations tab.
  - `get_queryset()` returns `Organization.objects.archived()` or `.unarchived()` for the tab,
    with `primary_sectors` prefetched, ordered by `-date_created`.
  - `get_filterset_kwargs()` passes `archived=True` on the Archive tab.
  - Page size from `get_page_size(request)` (`utils/tables.py`).
  - Context: both tabs' counts, the result count, breadcrumbs and the Reset URL.
- `OrganizationFilterSet` (`organization/filters.py`):
  - `search` → `filter_by_search_term`: `icontains` over `name`, `short_name`, `countries__name`,
    `primary_sectors__name`, with `.distinct()`.
  - `ModelMultipleChoiceFilter` on `countries` and `primary_sectors`.
  - `MultipleChoiceFilter` on `status` with `STATUS_FILTER_CHOICES` (the three non-archived
    values); removed when built with `archived=True`.
  - Countries, sectors and status widgets carry `data-tomselect="1"`; the page loads
    `bundles/js/tomselect-bundle.js` and `bundles/css/tomselect.css`.
- `OrganizationDirectoryTable` (`organization/tables.py`):
  - Status uses predefined badge classes: `positive-light` (Active), `label` (Inactive),
    `badge-indigo` (Prospective), `badge-amber` (Archived), each with `badge badge-sm`.
  - The Status header is rendered lazily from `organization/directory/status_header.html`, with
    the definitions from `status_definitions.html` in an `x-tooltip`.
  - Primary Sectors is a `ManyToManyColumn`; Added is `date_created` formatted `d-M-Y`.
- Templates in `templates/organization/directory/`: `organization_list.html`, `tabs.html` (the
  Contacts tab is a non-link element with `aria-disabled` and a "Coming soon" tooltip),
  `status_badge.html`, `status_header.html`, `status_definitions.html`.
- Sidenav (`templates/layouts/sidenav.html`): inside the `show_internal_features` block, an
  Alpine-driven "Admin" button toggles a group containing the existing Internal Features item and
  the LLO Directory item (guarded by `perms.users.workspace_entity_management_access`), both via
  `components/sidenav-items.html`. The group starts open when the current URL is in the `users` or
  `organization_directory` namespace, and shows its items whenever the sidebar is collapsed.

**Depends on:** ticket 1.

---

### 3. Add and edit organizations

**Requirement**

Staff need to add organizations that didn't come through self sign-up, and edit any
organization's details, without using a spreadsheet.

- An **Add Organization** button on the listing, and an **Edit** action on each row (on both the
  Organizations and Archive tabs). Each opens the same form in a pop-up: "Add Organization", or
  "Edit Organization — Editing <name>".
- The form, laid out in two columns:
  - Organization Name
  - Short Name | Status
  - Year of Establishment | Has Used Connect (Yes / No)
  - Org Team Size | No. of FLWs Managed
  - Countries of Operation (pick one or more)
  - Regions / States of Operation
  - Primary Sector(s) (pick one or more)
  - Website | Office Address
  - Email Addresses (one per line)
  - EOI Links (one per line)
  - Latest MSA Link | Latest Work Order Link
  - Organization Notes
- **Required:** Organization Name, Short Name, Countries of Operation, Regions / States of
  Operation, Primary Sector(s) — when adding and when editing.
- Status choices are Active, Inactive and Prospective. Status can be left empty. An info icon
  next to the Status label shows the status definitions on hover.
- Archived is not offered as a choice — organizations are archived with the Archive action.
  When editing an organization that is already archived, Archived is shown as its current status
  so it can be edited and stay archived; choosing another status restores it to the
  Organizations tab.
- Two organizations can't have the same name (ignoring upper/lower case); an organization that
  already shares a name with another stays editable as long as its name isn't changed. Short Name
  can be up to 40 characters. Each email address, each EOI link, the website, and the MSA and
  work order links must be valid. Year of Establishment follows the existing sign-up rules.
- The pop-up has Cancel and Save (Save Changes when editing) buttons; errors are shown in the
  pop-up.
- After saving, staff return to the listing on the same tab with the same filters, with a
  confirmation message.
- Opening the add or edit address directly, rather than from the listing, goes to the listing.
- The staff member adding an organization does not become a member of it.
- Renaming an organization does not change its workspace URL.
- Program manager, funder, verified and test settings stay admin-only.

**Technical specification**

- `OrganizationCreateView` at `/organizations/new/` (`organization_directory:create`) and
  `OrganizationUpdateView` at `/organizations/<slug>/edit/` (`organization_directory:edit`), both
  on `OrganizationDirectoryFormMixin`, which carries the access mixin.
- `OrganizationDirectoryForm` in `organization/forms.py` subclasses `OrganizationProfileForm`
  (the sign-up wizard's form, which is built for subclassing), so it inherits the name, email,
  EOI link and year validation and the field widgets, and supplies its own `Meta.fields`,
  labels, help texts and `_layout()`.
  - Fields: `name`, `short_name`, `status`, `year_of_establishment`, `has_used_connect`,
    `team_size`, `flws_managed`, `countries`, `regions`, `primary_sectors`, `website`,
    `office_address`, `contact_emails`, `eoi_links`, `latest_msa_link`,
    `latest_work_order_link`, `notes`.
  - `REQUIRED_FIELDS` (`name`, `short_name`, `countries`, `regions`, `primary_sectors`) are set
    `required=True`; the model allows them blank.
  - `has_used_connect` uses a Yes / No select; `regions` and `office_address` are single-line
    inputs.
  - `status` choices: blank + `active` / `inactive` / `prospective`, plus `archived` only when
    `instance.status == "archived"`.
  - The Status label gets an info icon via `value_with_icon_tooltip` (`opportunity/tables.py`),
    with the tooltip rendered from `status_definitions.html`.
- `OrganizationDirectoryFormMixin` renders only the modal fragment
  (`organization/directory/organization_form.html`):
  - a non-htmx GET redirects to the listing;
  - a valid POST saves and responds with `HX-Redirect` to the `next` URL when it is a directory
    listing URL on this host (`directory_return_url`), otherwise the listing, plus a success
    message;
  - an invalid POST re-renders the fragment.
- The create view does not add a `UserOrganizationMembership`, unlike `organization_create`.
  `Organization.save` only sets the slug on create, so renames keep it.
- Listing integration: the page holds an Alpine modal (`form_modal.html`) that opens when htmx
  swaps a form into `#organization-form`; the Add button and the row Edit action (`row_actions.html`)
  `hx-get` the form with `?next=<current listing URL>`. Countries / sectors widgets are
  initialised by the existing `static/js/tomselect.js` on `htmx:afterSettle`.

**Depends on:** ticket 2.

---

### 4. Archive organizations

**Requirement**

Staff need to archive an organization (for example a duplicate) so it drops out of the main list
while the record is kept. An organization that still has a live program can't be archived.

- A program is **live** when it hasn't ended: its end date is today or later. An organization
  can't be archived while it runs, funds or has an accepted application to a live program.
  Programs it only applied to or watches don't count.
- An **Archive** action on each row of the listing's Organizations tab:
  - if the organization can be archived, it asks for confirmation ("Archive <name>? It will move
    to the Archive tab.") and then archives it;
  - if it has a live program, the action is disabled and explains why on hover: "Can't archive:
    it runs, funds or delivers in a program that hasn't ended."
- Archiving sets the organization's status to **Archived**, which moves it to the Archive tab.
- If the organization has gained a live program by the time staff confirm, nothing changes and an
  error names the programs that prevent it.
- After archiving, staff return to the listing on the same tab with the same filters, with the
  message "Organization <name> archived."
- To restore an archived organization, staff edit it from the Archive tab and choose another
  status.
- Archiving only affects the directory: the organization's workspace, opportunities, programs and
  reports carry on as before.

**Technical specification**

- `organization/archive.py`:
  - `live_programs(organization)`: `Program`s with `end_date >= today` that the organization runs
    (`organization`), funds (`funder`) or has an `ACCEPTED` `ProgramApplication` to.
  - `has_live_program()`: an `Exists` annotation expression over the same rule, used by the
    listing so each row knows whether it can be archived without a query per row.
  - `archive_organization(organization)`: raises `ArchiveNotAllowed` naming the live programs, or
    sets `status = OrganizationStatus.ARCHIVED` and saves `status` and `date_modified`.
- `OrganizationListView.get_queryset()` annotates `has_live_program=has_live_program()`.
- `OrganizationArchiveView`: POST-only at `/organizations/<slug>/archive/`
  (`organization_directory:archive`), same access mixin; calls `archive_organization`, shows the
  `ArchiveNotAllowed` message as an error or a success message, and redirects to
  `directory_return_url(request)`.
- `row_actions.html`: on unarchived rows, a disabled button with the tooltip when
  `record.has_live_program`, otherwise a POST form whose button opens the shared confirm modal
  (`components/confirm_modal.html`, `$store.confirmModal`), included on the listing page.

**Depends on:** ticket 2.
