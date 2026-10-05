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
  tabs
- Add and edit organizations from the listing
- Archive action
- Archived organizations' workspaces are no longer reachable from the UI

Not in this phase: organization detail page, contacts, EOI/RFP / MSA / work order records, status
derived from contracts, internal partner assessment, change history, export, and leaving
archived organizations out of KPIs, org pickers, emails or the API.

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

The internal permission that controls who can manage all organizations is currently described as
"Can manage LLO Entities for organizations", which is out of date. Its description becomes
"Can view and manage all organizations".

**Technical specification**

- New fields on `Organization` (`commcare_connect/organization/models.py`):
  - `status = CharField(max_length=20, choices=OrganizationStatus.choices, blank=True)`
  - `latest_msa_link = URLField(blank=True)`
  - `latest_work_order_link = URLField(blank=True)`
- `OrganizationStatus(TextChoices)`: `active`, `inactive`, `prospective`, `archived`. Keep the
  definitions in one mapping next to the choices (e.g. `OrganizationStatus.DEFINITIONS`) for the
  UI to read.
- Django admin: add the three fields to `AdminOrganizationForm` (`commcare_connect/users/forms.py`),
  and `status` to `list_display` / `list_filter` in `commcare_connect/organization/admin.py`.
- Permission: change the `workspace_entity_management_access` description in
  `User.Meta.permissions` (`commcare_connect/users/models.py`); needs a `users` migration.

**Depends on:** —

---

### 2. Organizations listing page

**Requirement**

Connect staff (and GSP) need one page listing all organizations so they can find the one they
need. It is reached from a new sidenav item labelled **LLO Directory**, shown only to users with
the permission to manage all organizations. Everywhere else the page talks about
"organizations".

The page:

- Breadcrumbs "Internal › Organizations", title "Organizations", subtitle "All organizations in
  the Connect network."
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
- A **Reset** link that clears search and filters (staying on the same tab), and the number of
  matching organizations.
- A table with columns:
  - **Name**
  - **Status** — a coloured badge, "—" if not set. The column header explains the statuses:
    Active — 1+ open contracts; Inactive — 0 open contracts, and 1+ previously active contracts;
    Prospective — 0 open contracts, 0 previously active contracts, 1 active contact; Archived —
    archived by staff, e.g. a duplicate
  - **Primary Sectors**
  - **Org Team Size**
  - **FLWs Managed**
  - **Added** — the date the organization was added to Connect
- Newest organizations first; sorting by Name, Status, Org Team Size, FLWs Managed and Added.

Later tickets add the Add Organization button and the Edit and Archive actions.

**Technical specification**

- Access on all directory views: `LoginRequiredMixin` + `PermissionRequiredMixin`,
  `permission_required = WORKSPACE_ENTITY_MANAGEMENT_ACCESS`, `raise_exception=True`.
- URL `/organizations/`, top level (not under `/a/<org_slug>/`), in a new `organization_directory`
  namespace in the `organization` app, included from `config/urls.py`. Later tickets add `new/`,
  `<slug>/edit/` and `<slug>/archive/`; `new/` goes before `<slug>/` paths.
- `OrganizationListView`: `FilterMixin` (`opportunity/filters.py`) + django-tables2
  `SingleTableView`, following `OpportunityList` in `opportunity/views.py`. Prefetch
  `primary_sectors`. Tab via query parameter (e.g. `?tab=archive`): Organizations tab excludes
  `status="archived"`, Archive tab filters to it; applied before the filterset. Default ordering
  `-date_created`.
- `OrganizationFilterSet`: search `icontains` over `name`, `short_name`, `countries__name`,
  `primary_sectors__name`; `ModelMultipleChoiceFilter` on `countries` and `primary_sectors`;
  `MultipleChoiceFilter` on `status` limited to the three non-archived values. Use `.distinct()`
  for the M2M joins.
- Countries / sectors widgets get `data-tomselect="1"`; the page must load
  `bundles/js/tomselect-bundle.js` and `bundles/css/tomselect.css`.
- `OrganizationDirectoryTable`: status badge with predefined classes from `tailwind/tailwind.css`
  (`badge` + `status-active` / `status-inactive` / `badge-indigo` / `badge-amber`); definitions
  read from `OrganizationStatus.DEFINITIONS`. Added renders `date_created` as a date.
- Contacts tab: rendered as a non-link element, styled disabled, with a "Coming soon" tooltip —
  never a link to a page that doesn't exist.
- Sidenav: `templates/layouts/sidenav.html`, inside the `show_internal_features` block next to
  Internal Features, guarded by `perms.users.workspace_entity_management_access`, using
  `components/sidenav-items.html` with `namespace='organization_directory'`.

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
  - Latest MSA Link | Latest Work Order Link
  - Organization Notes
- **Required:** Organization Name, Short Name, Countries of Operation, Regions / States of
  Operation, Primary Sector(s) — when adding and when editing.
- Status choices are Active, Inactive and Prospective, with their definitions shown as help:
  Active — 1+ open contracts; Inactive — 0 open contracts, and 1+ previously active contracts;
  Prospective — 0 open contracts, 0 previously active contracts, 1 active contact. Status can be
  left empty.
- Archived is not offered as a choice — organizations are archived with the Archive action.
  When editing an organization that is already archived, Archived is shown as its current status
  so it can be edited and stay archived; choosing another status restores it to the
  Organizations tab.
- Two organizations can't have the same name (ignoring upper/lower case). Short Name can be up to
  40 characters. Each email address, the website, and the MSA and work order links must be valid.
  Year of Establishment follows the existing sign-up rules.
- The pop-up notes "Contacts and EOIs are linked after the record is saved." It has Cancel and
  Save buttons; errors are shown in the pop-up.
- After saving, staff return to the listing on the same tab with the same filters, with a
  confirmation message.
- The staff member adding an organization does not become a member of it.
- Renaming an organization does not change its workspace URL.
- EOI links are not on this form; they remain editable in Django admin until EOIs are tracked
  properly. Program manager, funder, verified and test settings also stay admin-only.

**Technical specification**

- `CreateView` at `/organizations/new/`, `UpdateView` at `/organizations/<slug>/edit/`, same
  access mixins as the listing.
- New `OrganizationDirectoryForm(ModelForm)` in `organization/forms.py` — not a reuse of
  `OrganizationProfileForm` (sign-up wizard) or `AdminOrganizationForm`. Fields: `name`,
  `short_name`, `status`, `year_of_establishment`, `has_used_connect`, `team_size`,
  `flws_managed`, `countries`, `regions`, `primary_sectors`, `website`, `office_address`,
  `contact_emails`, `latest_msa_link`, `latest_work_order_link`, `notes`.
- Set `required=True` on `name`, `short_name`, `countries`, `regions`, `primary_sectors` (blank is
  allowed on the model).
- `status` choices: blank + `active` / `inactive` / `prospective`; add `archived` only when
  `instance.status == "archived"`.
- Reuse the wizard's validation — `clean_name` (strip, case-insensitive uniqueness excluding the
  instance), `_clean_lines` with `EmailValidator` for `contact_emails`,
  `validate_year_of_establishment` — moving it to shared helpers rather than duplicating.
- Unlike `organization_create`, the create view must not add a `UserOrganizationMembership`.
  `Organization.save` only sets the slug on create, so renames keep it.
- Modal: one partial loaded with htmx into an Alpine modal. Valid POST → `HX-Redirect` to the
  listing URL it was opened from (tab and filters kept) with a success message; invalid POST
  re-renders the partial. Countries / sectors widgets get `data-tomselect="1"`; the existing
  `static/js/tomselect.js` initialises them on `htmx:afterSettle`.

**Depends on:** ticket 2.

---

### 4. Archive organizations

**Requirement**

Staff need to archive an organization (for example a duplicate) so it drops out of the main list
while the record is kept.

- An **Archive** action on each row of the listing's Organizations tab. It asks for confirmation
  ("Archive <name>? It will move to the Archive tab, and its workspace will no longer be
  available to anyone.") before archiving.
- Archiving sets the organization's status to **Archived**, which moves it to the Archive tab.
- After archiving, staff return to the listing on the same tab with the same filters, with the
  message "<name> archived."
- To restore an archived organization, staff edit it from the Archive tab and choose another
  status.
- What archiving does to the organization's workspace is covered by ticket 5.

**Technical specification**

- `archive_organization(organization)` in the organization app (e.g.
  `organization/directory.py`): sets `status = OrganizationStatus.ARCHIVED`, saves with
  `update_fields=["status"]`.
- POST-only view at `/organizations/<slug>/archive/`, same access mixins as the listing, calling
  the function and redirecting to the originating listing URL with a success message.
- Confirmation is an Alpine modal.

**Depends on:** ticket 2.

---

### 5. Make archived organizations unavailable

**Requirement**

Once an organization is archived, nobody should be able to use its workspace in Connect — not
its members, not staff, not superusers.

- Opening any page of an archived organization's workspace (for example from an old bookmark or
  a link in an email) shows a simple **"No longer available"** page — "This organization is no
  longer available." — with a link back to the user's home page. This applies to everyone.
- Connect never sends anyone to an archived organization:
  - it is left out of the organization switcher in the header;
  - after logging in, users are taken to one of their other organizations instead;
  - a user whose only organizations are archived is treated like a user with no organization
    and sees the existing "no organization" page.
- Staff still manage archived organizations from the LLO Directory (Archive tab), which is not
  part of any organization's workspace. Restoring an organization there (editing it and choosing
  another status) makes its workspace available again straight away.
- Not affected in this phase: Django admin, the API and mobile app, emails Connect sends, and
  lists where staff pick an organization (e.g. program invites, report filters).

**Technical specification**

- Workspace pages: in `OrganizationMiddleware.process_view` (`commcare_connect/users/middleware.py`),
  when `view_kwargs` has `org_slug` and that organization's `status == OrganizationStatus.ARCHIVED`,
  return the "No longer available" page instead of calling the view. This covers every
  `/a/<org_slug>/…` URL, including htmx partials, in one place. Render a new template
  (e.g. `organization/no_longer_available.html`, extending the base layout) with status 410.
  The directory URLs (`/organizations/…`) have no `org_slug`, so they are unaffected.
- Treat archived memberships as absent wherever Connect picks or lists a user's organizations —
  add one helper (e.g. memberships excluding `organization__status="archived"`) and use it in:
  - `_get_all_memberships` in `users/middleware.py` (feeds `request.memberships`, i.e. the
    header switcher in `templates/layouts/header.html`);
  - the no-`org_slug` fallback in `get_organization_for_request` (`users/helpers.py`);
  - `UserRedirectView` (`users/views.py`) and `no_organization` (`organization/views.py`), which
    check `memberships.exists()` — otherwise a user with only archived organizations would be sent
    back and forth between them.
- `Organization.visible_to` and other org querysets are left as they are in this phase.

**Depends on:** ticket 1 (status field). Ticket 4 is what users will use to archive, but this can be
built and tested independently.
