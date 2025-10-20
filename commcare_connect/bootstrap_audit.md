# Bootstrap-class audit (commcare_connect/)

This file lists occurrences of Bootstrap-related classes found under `commcare_connect/` (project-only, `.venv` and external packages excluded).

## Summary

- Buttons (`btn*`): several occurrences in templates and generated HTML.
- Alerts (`alert`): two occurrences.
- Grid/layout (`row`, `col-*`, `container`): multiple occurrences across templates.
- Tables (`table`, `table-responsive`, `table-striped`): several occurrences.
- Modals (`modal`, `modal-backdrop`): many occurrences but used with Tailwind/Alpine utilities in project templates.
- Badges/labels: `badge` appears widely but often with custom modifiers (e.g., `badge-md positive-dark`).
- Glyphicons, `.navbar`, `.nav-tabs`: NO occurrences in `commcare_connect/` (they are present in external DRF templates/staticfiles).

---

## Matches (file -> snippet)

### Buttons

- `commcare_connect/templates/tables/table_manage_action.html` (line ~7)
  - `<button type="submit" class="btn btn-{{ button.color|default:'primary' }} btn-sm"`
- `commcare_connect/templates/tables/table_manage_action.html` (line ~15)
  - `<a class="btn btn-{{ button.color|default:'primary' }} btn-sm{% if button.disable %} disabled{% endif %}"`
- `commcare_connect/templates/users/user_detail.html` (line ~19)
  - `<a class="btn btn-primary" href="{% url 'users:update' %}" role="button"><i class="bi bi-info-circle"></i> My Info</a>`
- `commcare_connect/templates/users/user_detail.html` (line ~20)
  - `<a class="btn btn-primary" href="{% url 'account_email' %}" role="button"><i class="bi bi-envelope"></i> E-Mail</a>`
- `commcare_connect/templates/account/password_set.html` (line ~15)
  - `<input class="btn btn-primary w-100" type="submit" name="action" value="..."/>`
- `commcare_connect/templates/account/password_change.html` (line ~14)
  - `<button class="btn btn-primary" type="submit" name="action">Change Password</button>`
- `commcare_connect/opportunity/tables.py` (line ~75)
  - `return mark_safe(f'<a class="btn btn-sm btn-primary" href="{url}">Review</a>')`
- `commcare_connect/opportunity/tables.py` (line ~271)
  - `return format_html('<a class="btn btn-success" href="{}?next={}">Revoke</a>', revoke_url, page_url)`

### Alerts

- `commcare_connect/templates/opportunity/upload_progress.html` (line ~9)
  - `<div class="alert alert-danger" role="alert">`
- `commcare_connect/templates/opportunity/upload_progress.html` (line ~29)
  - `<div class="alert alert-info" role="alert">`

### Grid / layout

- `commcare_connect/templates/users/user_detail.html` (lines ~7-18)
  - `<div class="bg-white col-md-6 offset-md-3 shadow">`
  - `<div class="row mt-5 pt-4">`
  - `<div class="col-sm-12 text-center">`
- `commcare_connect/templates/reports/dashboard.html` (multiple lines)
  - Several `row` and `col-12 col-sm-6 col-md-3` usages.
- `commcare_connect/templates/users/demo_tokens.html` (line ~8)
  - `<div class="container">`
- `commcare_connect/organization/forms.py` (line ~51-52)
  - `layout.Field("email", wrapper_class="col-md-5"),`

### Tables

- `commcare_connect/templates/users/demo_tokens.html` (line ~11)
  - `<table class="table table-striped mb-0">`
- `commcare_connect/templates/tables/single_table.html` (line ~2)
  - `<div class="table-responsive">`
- `commcare_connect/templates/tables/table_placeholder.html` (line ~1)
  - `<table class="table border placeholder-glow">`
- Multiple templates use `base-table` (project-specific) and other table classes.

### Modals (note: Tailwind/Alpine-style usage)

- `commcare_connect/templates/components/upload_progress_bar.html` (lines ~60-70)
  - `class="modal-backdrop fixed inset-0 bg-black bg-opacity-50 z-40 flex items-center justify-center"`
  - `class="modal bg-white rounded-lg shadow-lg w-full max-w-lg z-50"`
- `commcare_connect/templates/components/worker_page/*` and `opportunity/*` contain `class="modal-backdrop"` / `class="modal"` in several places.

### Badges / labels

- `commcare_connect/templates/account/email.html` (lines ~42-51)
  - `<span class="badge badge-sm positive-light">...`
- `commcare_connect/templates/program/pm_home.html` (multiple lines)
  - `<span class="badge badge-md negative-dark">Rejected</span>` etc.
- `commcare_connect/opportunity/tables.py` (lines ~789-797)
  - Renders `<span class="badge badge-sm negative-light mx-1">...` etc.

### Inputs / input-group

- `commcare_connect/templates/account/*` (login/signup/password reset) contain `div class="input-group ..."` usages.

### Not found in `commcare_connect/`

- `.glyphicon`, `.navbar`, `.nav-tabs`, `.panel` — no occurrences found.

---

## Notes

- The project uses a mixture of utility-first classes (likely Tailwind) and some Bootstrap-like classes. That hybrid approach means migrating away from Bootstrap in the app templates is feasible incrementally.
- External DRF templates and `staticfiles/rest_framework` still contain Bootstrap 3 assets; if you want to remove Bootstrap from the whole repo you'll need to address those separately (they live outside `commcare_connect/`).

---

If you'd like, I can:

1. Commit this `bootstrap_audit.md` to your branch (done — file created). If you want it committed in git history, tell me to run the commit.
2. Produce a CSV of matches (file, line, snippet) for import into a tracking spreadsheet.
3. Start an automated PR replacing `btn btn-primary` with a project-level `btn-primary` component/class or Tailwind equivalent (low-risk first change).

Which of those should I do next?
