# Connect Templates

This directory holds the project's Django HTML templates, organized by feature and by how widely they are reused.

## Directory structure

### Top level

| File                                       | Purpose                                                                                  |
| ------------------------------------------ | ---------------------------------------------------------------------------------------- |
| `base.html`                                | The page shell most templates extend                                                     |
| `base_table.html`, `grouped_table.html`    | django-tables2 templates; see the table rules in AGENTS.md (no horizontal scroll)        |
| `403.html`, `404.html`, `429.html`, `500.html` | Error pages Django renders for those status codes                                    |
| `favicon.html`                             | Favicon links included by the base templates                                             |

### Shared

| Directory     | Contents                                                                                                      |
| ------------- | ------------------------------------------------------------------------------------------------------------- |
| `components/` | Reusable partials: forms, modals, badges, cards, dropdowns, progress bars, worker page pieces, sidenav items  |
| `layouts/`    | `header.html`, `sidenav.html`, `footer.html`                                                                  |
| `tables/`     | Table-level templates and placeholders shared by several tables                                               |
| `widgets/`    | Templates for custom form widgets (e.g. `preset_date_range.html`)                                             |
| `email/`      | `base.html` / `base.txt` that app email templates extend                                                      |

### Per app or feature

| Directory                                 | Contents                                                                    |
| ----------------------------------------- | --------------------------------------------------------------------------- |
| `opportunity/`                            | The opportunity app: dashboards, worker views, visits, payments, invoices   |
| `microplanning/`                          | Work areas, groups, clustering modals and maps                              |
| `organization/`                           | Creating and managing organizations, members and invites                    |
| `program/`                                | Program manager and network manager views                                   |
| `solicitation/`                           | Solicitations: listing, publishing, invitations                           |
| `audit/`                                  | Audit reports and their emails                                              |
| `reports/`                                | KPI and admin reports                                                       |
| `tasks/`                                  | The assigned-task modal                                                     |
| `users/`                                  | User management, internal features, permissions                             |
| `flags/`                                  | The feature flag management page                                            |
| `ocs/`                                    | The Open Chat Studio connect prompt                                         |
| `account/`, `socialaccount/`              | django-allauth overrides: login, signup, email and password flows           |
| `admin/`                                  | Django admin overrides (e.g. the organization merge confirmation)           |
| `pages/`                                  | Standalone pages such as `about.html`                                       |
| `prelogin/`                               | Public marketing pages, `robots.txt` and `sitemap.xml`                      |

## Expectations

- **Reusability**: Place reusable UI pieces under `components/` or `layouts/`.
- **Modularity**: Each app has its own folder to keep related views organized.
- **Extensibility**: Most templates should extend a base (e.g. `base.html`) and override content blocks.
- **Naming**: File names should be descriptive and consistent with view functions or page purposes.
- **Partial templates**: Fragments meant to be included or swapped in by htmx go in a `partials/` folder inside
  the app's directory (most do), or under `components/`. A few older ones use a `_` or `partial_` prefix instead.
- **Styling and interactivity**: use the predefined style classes in `tailwind/tailwind.css`, Alpine.js for
  in-page behaviour and htmx for loading data; keep JavaScript in `.js` files. AGENTS.md has the details and the
  htmx/Alpine gotchas.

For any new templates, follow the existing organizational pattern and consider whether the file is page-specific,
app-specific, or a shared component.
