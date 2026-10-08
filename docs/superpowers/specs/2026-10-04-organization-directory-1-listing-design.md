# Organization directory 1: organizations listing

## Goal

Give Connect staff and the Global Strategy Partnerships (GSP) team, who do business development
for Connect, one page in Connect to find, add, edit and archive organizations, replacing the
spreadsheet they maintain today. Later phases add more tabs (starting with
Contacts) to the same page.

## Scope

- Organization `status` and latest MSA / work order links
- Organizations listing with search, filters and tabs, under a new "Admin" sidenav group
- Add and edit organizations
- Archive organizations that have no live program

Not in this phase: detail page, contacts, EOI/RFP / MSA / work order records, status derived
from contracts, partner assessment, change history, export, and any effect of archiving outside
the directory.

## Tickets

**Requirement** is for anyone reading the ticket; **Technical specification** is for the
developer.

---

### 1. Add status and agreement link fields to organizations

**Requirement**

- **Status**, set by hand for now (later derived from contracts):
  - **Active**: 1+ open contracts
  - **Inactive**: 0 open contracts, and 1+ previously active contracts
  - **Prospective**: 0 open contracts, 0 previously active contracts, 1 active contact
  - **Archived**: archived by staff, e.g. a duplicate
- **Latest MSA Link** and **Latest Work Order Link**.
- Existing organizations start blank. All three are editable in Django admin, where status can be
  filtered on.

**Technical specification**

- `Organization.status` (`OrganizationStatus` choices, blank allowed), `latest_msa_link`,
  `latest_work_order_link` (`URLField`, blank allowed).
- Status definitions in `ORGANIZATION_STATUS_DEFINITIONS`, next to the choices.
- `Organization.objects.archived()` / `.unarchived()`.
- Added to `AdminOrganizationForm`; `status` in the admin's `list_display` and `list_filter`.

**Depends on:** —

---

### 2. Organizations listing page

**Requirement**

- Sidenav: a collapsible **Admin** group holding **Internal Features** and **LLO Directory**
  (the latter only for users with the permission to manage all organizations).
- Page: breadcrumbs "Admin › Organizations", title "Organizations".
- Tabs: **Organizations** (not archived) and **Archive**, each with a count, and a disabled
  **Contacts** tab marked "Coming soon".
- Search over name, short name, countries and sectors. Filters: **Countries** and **Sectors**
  (any of the chosen), **Status** (Organizations tab only). Apply, Reset, and a result count.
- Columns: Name, Status (badge; definitions on hover in the header), Primary Sectors, Org Team
  Size, FLWs Managed, Added. Newest first; all sortable except Primary Sectors and Org Team Size.

**Technical specification**

- `/organizations/`, from `directory_urlpatterns` in `organization/urls.py` (namespace
  `organization_directory`), gated on `WORKSPACE_ENTITY_MANAGEMENT_ACCESS`.
- `OrganizationListView`: `SingleTableMixin` + `FilterView`; tab from `?tab=`.
- `OrganizationFilterSet`; its `archived=True` argument drops the Status filter.
- `OrganizationDirectoryTable`, templates in `templates/organization/directory/`.

**Depends on:** ticket 1.

---

### 3. Add and edit organizations

**Requirement**

- **Add Organization** button and a row **Edit** action open the same pop-up form.
- Fields: Name, Short Name, Status, Year of Establishment, Has Used Connect, Org Team Size,
  No. of FLWs Managed, Countries, Regions, Primary Sectors, Website, Office Address, Email
  Addresses, EOI Links, Latest MSA Link, Latest Work Order Link, Notes.
- Required: Name, Short Name, Countries, Regions, Primary Sectors.
- Status offers Active, Inactive and Prospective (definitions on hover). Archived appears only
  when editing an already archived organization; choosing another status restores it.
- The sign-up form's validation applies (unique name, valid emails and links, year range).
- Saving returns to the listing with its tab and filters kept.
- Adding an organization doesn't make the staff member a member; renaming keeps its URL.

**Technical specification**

- `/organizations/new/` and `/organizations/<slug>/edit/`.
- `OrganizationDirectoryForm` subclasses `OrganizationProfileForm`.
- The views only serve the form as an htmx fragment; a direct visit redirects to the listing.

**Depends on:** ticket 2.

---

### 4. Archive organizations

**Requirement**

- An organization can't be archived while it runs, funds or has an accepted application to a
  program that hasn't ended (end date today or later).
- On the Organizations tab, each row's **Archive** action asks for confirmation, then moves the
  organization to the Archive tab. When archiving isn't allowed, the action is disabled and says
  why on hover.
- If a live program appears before staff confirm, nothing changes and an error names it.
- Restore by editing the organization and choosing another status.
- Archiving only affects the directory.

**Technical specification**

- `organization/archive.py`: `live_programs()`, the `has_live_program()` annotation used by the
  listing, and `archive_organization()`, which raises `ArchiveNotAllowed`.
- POST-only `/organizations/<slug>/archive/`.

**Depends on:** ticket 2.
