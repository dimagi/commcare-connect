# Permission Management Page Design

**Date:** 2026-03-09

## Overview

Add an internal permission management page where authorised staff can view and assign/unassign the custom `User.Meta` permissions to other `@dimagi.com` users. The page is gated behind a new `permission_access` permission and exposes three UI variants selectable via `?option=` query parameter.

## New Permission

Add `permission_access` to `User.Meta.permissions`:

```python
("permission_access", "Can manage user permissions"),
```

- Added to `show_internal_features` check in `User`
- Added to the Internal Features hub card list
- Added to `permission_const.py` as `PERMISSION_ACCESS`

## URL

Single URL entry point:

```
/users/permissions/?option=a   # Option A (default)
/users/permissions/?option=b   # Option B
/users/permissions/?option=c   # Option C
```

A single view (`PermissionManagementView`) reads the `option` query param and renders the appropriate template. Invalid/missing values default to `a`.

For Option B, a permission detail page is also needed:

```
/users/permissions/<codename>/   # Option B detail page
```

## Permissions in Scope

All 8 custom permissions defined in `User.Meta`:

- `demo_users_access`
- `otp_access`
- `kpi_report_access`
- `all_org_access`
- `view_commcarehq_form_link`
- `org_management_settings_access`
- `workspace_entity_management_access`
- `product_features_access`

Only users with emails ending in `@dimagi.com` can be assigned permissions.

## Option A — htmx Inline (default)

**Template:** `users/permissions_htmx.html`

- One card per permission showing its name, description, and current holders
- Each holder has an inline remove button → POST to `users/permissions/remove/` via htmx
- Each card has a text input to search `@dimagi.com` users → GET `users/permissions/search/?q=&permission=<codename>` returns a dropdown partial
- Selecting a user and confirming → POST to `users/permissions/add/` via htmx
- On success, htmx re-renders just the affected permission card via `HX-Retarget`

**Additional endpoints:**
- `GET users/permissions/search/` — returns user autocomplete partial
- `POST users/permissions/add/` — assigns permission, returns updated card partial
- `POST users/permissions/remove/` — unassigns permission, returns updated card partial

## Option B — Page-per-Permission

**Template:** `users/permissions_per_permission.html` (landing)
**Template:** `users/permissions_detail.html` (per-permission detail)

- Landing page lists all 8 permissions as clickable cards with user count badge
- Detail page at `/users/permissions/<codename>/`:
  - Shows all users currently holding the permission
  - Add form: email input restricted to `@dimagi.com`, standard POST
  - Each existing user has a remove button (POST form)
  - Full page reload on each action

## Option C — Matrix

**Template:** `users/permissions_matrix.html`

- Loads all users with `@dimagi.com` emails
- Table: rows = users, columns = the 8 permissions
- Each cell is a checkbox (checked = has permission)
- Single "Save" button submits the full matrix as one POST
- View diffs current state vs submitted state, applies adds/removes
- Shows a summary of changes after save

## Architecture

- All logic lives in `commcare_connect/users/`
- Views in `users/views.py` (new `PermissionManagementView` class + htmx helper views)
- URLs in `users/urls.py`
- Templates in `templates/users/`
- No new models required — uses Django's built-in `Permission` + `user.user_permissions`

## Access Control

All views use `PermissionRequiredMixin` (or `@permission_required`) with `PERMISSION_ACCESS`. Unauthenticated users are redirected to login. Authorised users can only assign permissions to `@dimagi.com` users (enforced in view logic, not just UI).
