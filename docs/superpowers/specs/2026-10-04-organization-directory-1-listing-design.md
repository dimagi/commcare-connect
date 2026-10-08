# Organization directory 1: organizations listing

## Goal

Give Connect staff and the Global Strategy Partnerships (GSP) team, who do business development
for Connect, one page in Connect to find, add and edit organizations, replacing the spreadsheet they maintain
today. Later phases add more tabs (starting with Contacts) to the same page.

## Scope

- Organization `status` and latest MSA / work order links
- Organizations listing with search, filters and tabs, under a new "Admin" sidenav group
- Add and edit organizations

Not in this phase: detail page, contacts, EOI/RFP / MSA / work order records, status derived
from contracts, partner assessment, change history, export, and archiving organizations.

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
- **Latest MSA Link** and **Latest Work Order Link**.
- Existing organizations start blank. All three are editable in Django admin, where status can be
  filtered on.

**Technical specification**

- `Organization.status` (`OrganizationStatus` choices, blank allowed), `latest_msa_link`,
  `latest_work_order_link` (`URLField`, blank allowed).
- Status definitions in `ORGANIZATION_STATUS_DEFINITIONS`, next to the choices.
- Added to `AdminOrganizationForm`; `status` in the admin's `list_display` and `list_filter`.

**Depends on:** —

---

### 2. Organizations listing page

**Requirement**

- Sidenav: a collapsible **Admin** group holding **Internal Features** and **LLO Directory**
  (the latter only for users with the permission to manage all organizations).
- Page: breadcrumbs "Admin › Organizations", title "Organizations".
- Tabs: **Organizations**, with a count, and a disabled **Contacts** tab marked "Coming soon".
- Search over name, short name, countries and sectors. Filters: **Countries** and **Sectors**
  (any of the chosen), **Status**. Apply, Reset, and a result count.
- Columns: Name, Status (badge; definitions on hover in the header), Primary Sectors, Org Team
  Size, FLWs Managed, Added. Newest first; all sortable except Primary Sectors and Org Team Size.

**Technical specification**

- `/organizations/`, from `directory_urlpatterns` in `organization/urls.py` (namespace
  `organization_directory`), gated on `WORKSPACE_ENTITY_MANAGEMENT_ACCESS`.
- `OrganizationListView`: `SingleTableMixin` + `FilterView`, with `OrganizationFilterSet`.
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
- Status offers Active, Inactive and Prospective (definitions on hover).
- The sign-up form's validation applies (unique name, valid emails and links, year range).
- Saving returns to the listing with its filters kept.
- Adding an organization doesn't make the staff member a member; renaming keeps its URL.

**Technical specification**

- `/organizations/new/` and `/organizations/<slug>/edit/`.
- `OrganizationDirectoryForm` subclasses `OrganizationProfileForm`.
- The views only serve the form as an htmx fragment; a direct visit redirects to the listing.

**Depends on:** ticket 2.

