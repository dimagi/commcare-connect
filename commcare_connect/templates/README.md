# CommCare Connect Templates

This directory contains all Django HTML templates for the CommCare Connect project, organized by feature and reuse purpose. The structure follows a modular approach to support maintainability, reuse, and clarity.

## 📁 Directory Structure

- **Top-level error pages**
  - `403.html`, `404.html`, `500.html`: Global error pages rendered by Django for respective HTTP status codes.

- **`account/`**
  - Templates related to user authentication and account management (login, signup, email, password workflows).

- **`components/`**
  - Reusable template components and partials (e.g., `form.html`, `progressbar/`, `dropdowns/`).
  - Encouraged for shared UI elements across different pages and apps.

- **`layouts/`**
  - Base layout elements such as `header.html` and `sidenav.html`.
  - Used in other templates via `{% include %}` or extended base templates.

- **`opportunity/`**
  - All templates specific to the Opportunity app: dashboards, worker views, modals, invoice views, and more.
  - Follows a flat naming pattern per feature (e.g., `opportunity_worker.html`).

- **`organization/`**
  - Templates for managing organizations, e.g., creating or viewing an org.

- **`pages/`**
  - Static or informational pages like `about.html` and `home.html`.

- **`program/`**
  - Templates related to program manager or national manager views.
  - Includes shared base layout for program dashboards.

- **`reports/`**
  - Templates for data reports and admin dashboards.

- **`users/`**
  - User management templates including forms and user detail views.

## 🧩 Expectations

- **Reusability**: Place reusable UI pieces under `components/` or `layouts/`.
- **Modularity**: Each app (e.g., `opportunity`, `program`, `users`) has its own folder to keep related views organized.
- **Extensibility**: Most templates should extend a base (e.g., `base.html`) and override content blocks.
- **Naming**: File names should be descriptive and consistent with view functions or page purposes.
- **Partial Templates**: Templates prefixed with `partial_` or placed under `components/` are expected to be used via `include` in other templates.

---

For any new templates, follow the existing organizational pattern and consider whether the file is page-specific, app-specific, or a shared component.
