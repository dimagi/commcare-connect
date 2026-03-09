# Permission Management Page Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Build an internal permission management page at `/users/permissions/?option=a|b|c` where users with `permission_access` can assign/unassign the 8 custom User.Meta permissions to `@dimagi.com` users.

**Architecture:** Single URL entry point dispatching to three template variants based on `?option=` query param. Option A uses htmx for inline add/remove. Option B uses full-page-reload per-permission detail pages. Option C renders a full user×permission checkbox matrix submitted as one POST. All views live in `commcare_connect/users/`.

**Tech Stack:** Django 4.2, htmx (already in base template), Tailwind CSS (existing utility classes: `card_bg`, `card_title`, `card_description`, `button`, `button-md`, `primary-dark`), pytest-django, factory-boy.

---

### Task 1: Add `permission_access` permission constant and User.Meta entry

**Files:**
- Modify: `commcare_connect/utils/permission_const.py`
- Modify: `commcare_connect/users/models.py`

**Step 1: Write the failing test**

In `commcare_connect/users/tests/test_models.py`, add:

```python
def test_permission_access_exists():
    from django.contrib.auth.models import Permission
    assert Permission.objects.filter(codename="permission_access").exists()
```

**Step 2: Run test to verify it fails**

```bash
pytest commcare_connect/users/tests/test_models.py::test_permission_access_exists -v
```
Expected: FAIL — permission does not exist yet.

**Step 3: Add the constant to `permission_const.py`**

After line 8, add:
```python
PERMISSION_ACCESS = "users.permission_access"
```

**Step 4: Add to `User.Meta.permissions` in `users/models.py`**

After line 72 (the `product_features_access` entry), add:
```python
("permission_access", "Can manage user permissions"),
```

**Step 5: Update `show_internal_features` in `users/models.py`**

Change line 80 from:
```python
internal_features = [OTP_ACCESS, DEMO_USER_ACCESS, KPI_REPORT_ACCESS, ALL_ORG_ACCESS, PRODUCT_FEATURES_ACCESS]
```
to:
```python
internal_features = [OTP_ACCESS, DEMO_USER_ACCESS, KPI_REPORT_ACCESS, ALL_ORG_ACCESS, PRODUCT_FEATURES_ACCESS, PERMISSION_ACCESS]
```

Also add `PERMISSION_ACCESS` to the import at line 12–18 of `models.py`.

**Step 6: Generate and run the migration**

```bash
./manage.py makemigrations users
./manage.py migrate
```

**Step 7: Run test to verify it passes**

```bash
pytest commcare_connect/users/tests/test_models.py::test_permission_access_exists -v
```
Expected: PASS.

**Step 8: Commit**

```bash
git add commcare_connect/utils/permission_const.py commcare_connect/users/models.py commcare_connect/users/migrations/
git commit -m "feat: add permission_access custom permission"
```

---

### Task 2: Add helper — list of all manageable permissions

**Files:**
- Modify: `commcare_connect/users/views.py`

We need a single source of truth for the 8 manageable permissions. Add this near the top of `views.py`, after the imports:

**Step 1: Add the constant in `views.py`**

After the existing imports (around line 44), add:

```python
from commcare_connect.utils.permission_const import (
    ALL_ORG_ACCESS,
    DEMO_USER_ACCESS,
    KPI_REPORT_ACCESS,
    ORG_MANAGEMENT_SETTINGS_ACCESS,
    OTP_ACCESS,
    PERMISSION_ACCESS,
    PRODUCT_FEATURES_ACCESS,
    WORKSPACE_ENTITY_MANAGEMENT_ACCESS,
)

# Note: views.py already imports some of these — just add the missing ones to the existing import block.

MANAGEABLE_PERMISSIONS = [
    {
        "codename": "demo_users_access",
        "full": DEMO_USER_ACCESS,
        "label": "Demo Users Access",
        "description": "Allow viewing OTPs for demo users",
    },
    {
        "codename": "otp_access",
        "full": OTP_ACCESS,
        "label": "OTP Access",
        "description": "Allow fetching OTPs for Connect users",
    },
    {
        "codename": "kpi_report_access",
        "full": KPI_REPORT_ACCESS,
        "label": "KPI Report Access",
        "description": "Allow access to KPI reports",
    },
    {
        "codename": "all_org_access",
        "full": ALL_ORG_ACCESS,
        "label": "All Org Access",
        "description": "Allow admin access to all organizations",
    },
    {
        "codename": "view_commcarehq_form_link",
        "full": "users.view_commcarehq_form_link",
        "label": "View CommCareHQ Form Link",
        "description": "Can view CommCareHQ form link",
    },
    {
        "codename": "org_management_settings_access",
        "full": ORG_MANAGEMENT_SETTINGS_ACCESS,
        "label": "Org Management Settings",
        "description": "Can manage organizations settings",
    },
    {
        "codename": "workspace_entity_management_access",
        "full": WORKSPACE_ENTITY_MANAGEMENT_ACCESS,
        "label": "Workspace Entity Management",
        "description": "Can manage LLO Entities for organizations",
    },
    {
        "codename": "product_features_access",
        "full": PRODUCT_FEATURES_ACCESS,
        "label": "Product Features Access",
        "description": "Can access and manage product features (flags and switches)",
    },
]
```

Note: `ORG_MANAGEMENT_SETTINGS_ACCESS` and `WORKSPACE_ENTITY_MANAGEMENT_ACCESS` are already defined in `permission_const.py` but not currently imported in `views.py` — add them to the existing import block.

**Step 2: No test needed for a constant. Commit.**

```bash
git add commcare_connect/users/views.py
git commit -m "feat: add MANAGEABLE_PERMISSIONS constant to users views"
```

---

### Task 3: Add helper — get users with a given permission

**Files:**
- Modify: `commcare_connect/users/views.py`

**Step 1: Write the failing test**

In `commcare_connect/users/tests/test_views.py`, add:

```python
from commcare_connect.users.views import get_users_with_permission


class TestGetUsersWithPermission:
    def test_returns_users_with_permission(self):
        from django.contrib.auth.models import Permission

        user = UserFactory(email="staff@dimagi.com")
        perm = Permission.objects.get(codename="otp_access")
        user.user_permissions.add(perm)

        result = get_users_with_permission("otp_access")
        assert user in result

    def test_excludes_users_without_permission(self):
        user = UserFactory(email="other@dimagi.com")
        result = get_users_with_permission("otp_access")
        assert user not in result
```

**Step 2: Run to verify it fails**

```bash
pytest commcare_connect/users/tests/test_views.py::TestGetUsersWithPermission -v
```
Expected: FAIL — `get_users_with_permission` not defined.

**Step 3: Implement the helper in `views.py`**

Add after the `MANAGEABLE_PERMISSIONS` constant:

```python
def get_users_with_permission(codename):
    """Return all users who have the given permission (by codename) via user_permissions."""
    return User.objects.filter(user_permissions__codename=codename).order_by("email")
```

**Step 4: Run tests to verify they pass**

```bash
pytest commcare_connect/users/tests/test_views.py::TestGetUsersWithPermission -v
```
Expected: PASS.

**Step 5: Commit**

```bash
git add commcare_connect/users/views.py commcare_connect/users/tests/test_views.py
git commit -m "feat: add get_users_with_permission helper"
```

---

### Task 4: Main permission management view (dispatcher + option A template)

**Files:**
- Modify: `commcare_connect/users/views.py`
- Create: `commcare_connect/templates/users/permissions_management.html`
- Create: `commcare_connect/templates/users/permissions_htmx.html`
- Create: `commcare_connect/templates/users/partials/permission_card.html`

**Step 1: Write the failing test**

In `commcare_connect/users/tests/test_views.py`, add:

```python
from commcare_connect.utils.permission_const import PERMISSION_ACCESS


class TestPermissionManagementView:
    def test_requires_login(self, client):
        url = reverse("users:permission_management")
        response = client.get(url)
        assert response.status_code == 302
        assert "/accounts/login/" in response["Location"]

    def test_requires_permission_access(self, user, client):
        client.force_login(user)
        url = reverse("users:permission_management")
        response = client.get(url)
        assert response.status_code == 403

    def test_option_a_is_default(self, user, client):
        from django.contrib.auth.models import Permission

        perm = Permission.objects.get(codename="permission_access")
        user.user_permissions.add(perm)
        client.force_login(user)
        url = reverse("users:permission_management")
        response = client.get(url)
        assert response.status_code == 200
        assert "permissions_htmx.html" in [t.name for t in response.templates]

    def test_option_b_uses_per_permission_template(self, user, client):
        from django.contrib.auth.models import Permission

        perm = Permission.objects.get(codename="permission_access")
        user.user_permissions.add(perm)
        client.force_login(user)
        url = reverse("users:permission_management") + "?option=b"
        response = client.get(url)
        assert response.status_code == 200
        assert "permissions_per_permission.html" in [t.name for t in response.templates]

    def test_option_c_uses_matrix_template(self, user, client):
        from django.contrib.auth.models import Permission

        perm = Permission.objects.get(codename="permission_access")
        user.user_permissions.add(perm)
        client.force_login(user)
        url = reverse("users:permission_management") + "?option=c"
        response = client.get(url)
        assert response.status_code == 200
        assert "permissions_matrix.html" in [t.name for t in response.templates]
```

**Step 2: Run to verify it fails**

```bash
pytest commcare_connect/users/tests/test_views.py::TestPermissionManagementView -v
```
Expected: FAIL.

**Step 3: Add the view to `views.py`**

```python
class PermissionManagementView(LoginRequiredMixin, PermissionRequiredMixin, View):
    permission_required = PERMISSION_ACCESS

    OPTION_TEMPLATES = {
        "a": "users/permissions_htmx.html",
        "b": "users/permissions_per_permission.html",
        "c": "users/permissions_matrix.html",
    }

    def get(self, request):
        option = request.GET.get("option", "a").lower()
        template = self.OPTION_TEMPLATES.get(option, self.OPTION_TEMPLATES["a"])

        permissions_data = [
            {
                **perm,
                "users": get_users_with_permission(perm["codename"]),
            }
            for perm in MANAGEABLE_PERMISSIONS
        ]

        return render(
            request,
            "users/permissions_management.html",
            {
                "option": option,
                "template": template,
                "permissions_data": permissions_data,
            },
        )
```

**Step 4: Create `templates/users/permissions_management.html`**

This is a thin wrapper that includes the option-specific template:

```html
{% extends "base.html" %}
{% load i18n %}

{% block title %}{% translate "Permission Management" %}{% endblock %}

{% block content %}
<div class="container">
  <div class="flex items-center justify-between mb-6">
    <h2 class="text-xl font-semibold text-brand-deep-purple">{% translate "Permission Management" %}</h2>
    <div class="flex gap-2">
      <a href="?option=a" class="button button-md {% if option == 'a' %}primary-dark{% else %}outline-style{% endif %}">Option A</a>
      <a href="?option=b" class="button button-md {% if option == 'b' %}primary-dark{% else %}outline-style{% endif %}">Option B</a>
      <a href="?option=c" class="button button-md {% if option == 'c' %}primary-dark{% else %}outline-style{% endif %}">Option C</a>
    </div>
  </div>
  {% include template %}
</div>
{% endblock content %}
```

**Step 5: Create `templates/users/permissions_htmx.html`** (Option A)

```html
{% load i18n %}
<div class="grid gap-4 grid-cols-1 lg:grid-cols-2">
  {% for perm in permissions_data %}
    {% include "users/partials/permission_card.html" with perm=perm %}
  {% endfor %}
</div>
```

**Step 6: Create `templates/users/partials/permission_card.html`**

```html
{% load i18n %}
<div class="card_bg" id="perm-card-{{ perm.codename }}">
  <h3 class="card_title mb-1">{{ perm.label }}</h3>
  <p class="card_description mb-3">{{ perm.description }}</p>

  <ul class="mb-3 space-y-1">
    {% for u in perm.users %}
      <li class="flex items-center justify-between text-sm">
        <span>{{ u.email }}</span>
        <form method="post" action="{% url 'users:permission_remove' %}"
              hx-post="{% url 'users:permission_remove' %}"
              hx-target="#perm-card-{{ perm.codename }}"
              hx-swap="outerHTML">
          {% csrf_token %}
          <input type="hidden" name="codename" value="{{ perm.codename }}">
          <input type="hidden" name="user_id" value="{{ u.pk }}">
          <button type="submit" class="button button-md outline-style text-xs">{% translate "Remove" %}</button>
        </form>
      </li>
    {% empty %}
      <li class="text-sm text-gray-500">{% translate "No users assigned." %}</li>
    {% endfor %}
  </ul>

  <form method="post" action="{% url 'users:permission_add' %}"
        hx-post="{% url 'users:permission_add' %}"
        hx-target="#perm-card-{{ perm.codename }}"
        hx-swap="outerHTML"
        class="flex gap-2">
    {% csrf_token %}
    <input type="hidden" name="codename" value="{{ perm.codename }}">
    <input type="email" name="email" placeholder="user@dimagi.com"
           class="input flex-1 text-sm" required
           pattern=".*@dimagi\.com$"
           title="Must be a @dimagi.com email">
    <button type="submit" class="button button-md primary-dark text-sm">{% translate "Add" %}</button>
  </form>
</div>
```

**Step 7: Run tests**

```bash
pytest commcare_connect/users/tests/test_views.py::TestPermissionManagementView -v
```
Expected: PASS (templates for B and C don't exist yet but will be created in later tasks — create stub templates for now to make tests pass).

Create stub `templates/users/permissions_per_permission.html`:
```html
{% load i18n %}
<p>{% translate "Per-permission view" %}</p>
```

Create stub `templates/users/permissions_matrix.html`:
```html
{% load i18n %}
<p>{% translate "Matrix view" %}</p>
```

**Step 8: Register the URL in `users/urls.py`**

Add to `urlpatterns`:
```python
path("permissions/", views.PermissionManagementView.as_view(), name="permission_management"),
```

Also import `PermissionManagementView` in the import block at the top.

**Step 9: Run tests again to confirm**

```bash
pytest commcare_connect/users/tests/test_views.py::TestPermissionManagementView -v
```

**Step 10: Commit**

```bash
git add commcare_connect/users/views.py commcare_connect/users/urls.py \
  commcare_connect/templates/users/
git commit -m "feat: add permission management view with option dispatcher"
```

---

### Task 5: htmx add/remove endpoints (Option A)

**Files:**
- Modify: `commcare_connect/users/views.py`
- Modify: `commcare_connect/users/urls.py`

**Step 1: Write failing tests**

In `commcare_connect/users/tests/test_views.py`, add:

```python
class TestPermissionAddRemoveViews:
    @pytest.fixture
    def staff_user(self):
        from django.contrib.auth.models import Permission
        u = UserFactory(email="staff@dimagi.com")
        perm = Permission.objects.get(codename="permission_access")
        u.user_permissions.add(perm)
        return u

    @pytest.fixture
    def dimagi_user(self):
        return UserFactory(email="worker@dimagi.com")

    def test_add_permission_assigns_to_dimagi_user(self, staff_user, dimagi_user, client):
        client.force_login(staff_user)
        url = reverse("users:permission_add")
        response = client.post(url, {"codename": "otp_access", "email": dimagi_user.email})
        assert response.status_code == 200
        dimagi_user.refresh_from_db()
        assert dimagi_user.has_perm("users.otp_access")

    def test_add_permission_rejects_non_dimagi_email(self, staff_user, client):
        other = UserFactory(email="user@other.com")
        client.force_login(staff_user)
        url = reverse("users:permission_add")
        response = client.post(url, {"codename": "otp_access", "email": other.email})
        assert response.status_code == 400

    def test_remove_permission_unassigns_user(self, staff_user, dimagi_user, client):
        from django.contrib.auth.models import Permission
        perm = Permission.objects.get(codename="otp_access")
        dimagi_user.user_permissions.add(perm)

        client.force_login(staff_user)
        url = reverse("users:permission_remove")
        response = client.post(url, {"codename": "otp_access", "user_id": dimagi_user.pk})
        assert response.status_code == 200
        dimagi_user.refresh_from_db()
        assert not dimagi_user.has_perm("users.otp_access")

    def test_add_remove_require_permission_access(self, user, client):
        client.force_login(user)
        for url_name in ["users:permission_add", "users:permission_remove"]:
            response = client.post(reverse(url_name), {})
            assert response.status_code == 403
```

**Step 2: Run to verify they fail**

```bash
pytest commcare_connect/users/tests/test_views.py::TestPermissionAddRemoveViews -v
```

**Step 3: Add views to `views.py`**

```python
def _get_permission_card_response(request, codename):
    """Re-render a single permission card partial for htmx responses."""
    perm_meta = next((p for p in MANAGEABLE_PERMISSIONS if p["codename"] == codename), None)
    if perm_meta is None:
        from django.http import HttpResponseBadRequest
        return HttpResponseBadRequest("Unknown permission")
    perm_data = {**perm_meta, "users": get_users_with_permission(codename)}
    return render(request, "users/partials/permission_card.html", {"perm": perm_data})


class PermissionAddView(LoginRequiredMixin, PermissionRequiredMixin, View):
    permission_required = PERMISSION_ACCESS

    def post(self, request):
        from django.contrib.auth.models import Permission
        from django.http import HttpResponseBadRequest

        codename = request.POST.get("codename")
        email = request.POST.get("email", "")

        if not email.endswith("@dimagi.com"):
            return HttpResponseBadRequest("Only @dimagi.com users can be assigned permissions.")

        try:
            target_user = User.objects.get(email=email)
        except User.DoesNotExist:
            return HttpResponseBadRequest("User not found.")

        try:
            perm = Permission.objects.get(codename=codename, content_type__app_label="users")
        except Permission.DoesNotExist:
            return HttpResponseBadRequest("Unknown permission.")

        target_user.user_permissions.add(perm)
        return _get_permission_card_response(request, codename)


class PermissionRemoveView(LoginRequiredMixin, PermissionRequiredMixin, View):
    permission_required = PERMISSION_ACCESS

    def post(self, request):
        from django.contrib.auth.models import Permission
        from django.http import HttpResponseBadRequest

        codename = request.POST.get("codename")
        user_id = request.POST.get("user_id")

        try:
            target_user = User.objects.get(pk=user_id)
        except User.DoesNotExist:
            return HttpResponseBadRequest("User not found.")

        try:
            perm = Permission.objects.get(codename=codename, content_type__app_label="users")
        except Permission.DoesNotExist:
            return HttpResponseBadRequest("Unknown permission.")

        target_user.user_permissions.remove(perm)
        return _get_permission_card_response(request, codename)
```

**Step 4: Add URLs to `users/urls.py`**

```python
path("permissions/add/", views.PermissionAddView.as_view(), name="permission_add"),
path("permissions/remove/", views.PermissionRemoveView.as_view(), name="permission_remove"),
```

**Step 5: Run tests**

```bash
pytest commcare_connect/users/tests/test_views.py::TestPermissionAddRemoveViews -v
```
Expected: PASS.

**Step 6: Commit**

```bash
git add commcare_connect/users/views.py commcare_connect/users/urls.py
git commit -m "feat: add htmx permission add/remove endpoints"
```

---

### Task 6: Option B — per-permission landing + detail views

**Files:**
- Modify: `commcare_connect/users/views.py`
- Modify: `commcare_connect/users/urls.py`
- Modify: `commcare_connect/templates/users/permissions_per_permission.html` (replace stub)
- Create: `commcare_connect/templates/users/permissions_detail.html`

**Step 1: Write failing tests**

```python
class TestPermissionDetailView:
    @pytest.fixture
    def staff_user(self):
        from django.contrib.auth.models import Permission
        u = UserFactory(email="admin@dimagi.com")
        perm = Permission.objects.get(codename="permission_access")
        u.user_permissions.add(perm)
        return u

    def test_detail_view_loads(self, staff_user, client):
        client.force_login(staff_user)
        url = reverse("users:permission_detail", kwargs={"codename": "otp_access"})
        response = client.get(url)
        assert response.status_code == 200
        assert "permissions_detail.html" in [t.name for t in response.templates]

    def test_detail_view_404_for_unknown_codename(self, staff_user, client):
        client.force_login(staff_user)
        url = reverse("users:permission_detail", kwargs={"codename": "nonexistent"})
        response = client.get(url)
        assert response.status_code == 404

    def test_detail_view_add_user(self, staff_user, client):
        from django.contrib.auth.models import Permission
        target = UserFactory(email="target@dimagi.com")
        client.force_login(staff_user)
        url = reverse("users:permission_detail", kwargs={"codename": "otp_access"})
        response = client.post(url, {"action": "add", "email": target.email})
        assert response.status_code == 302
        target.refresh_from_db()
        assert target.has_perm("users.otp_access")

    def test_detail_view_remove_user(self, staff_user, client):
        from django.contrib.auth.models import Permission
        target = UserFactory(email="target2@dimagi.com")
        perm = Permission.objects.get(codename="otp_access")
        target.user_permissions.add(perm)
        client.force_login(staff_user)
        url = reverse("users:permission_detail", kwargs={"codename": "otp_access"})
        response = client.post(url, {"action": "remove", "user_id": target.pk})
        assert response.status_code == 302
        target.refresh_from_db()
        assert not target.has_perm("users.otp_access")
```

**Step 2: Run to verify they fail**

```bash
pytest commcare_connect/users/tests/test_views.py::TestPermissionDetailView -v
```

**Step 3: Add `PermissionDetailView` to `views.py`**

```python
class PermissionDetailView(LoginRequiredMixin, PermissionRequiredMixin, View):
    permission_required = PERMISSION_ACCESS

    def _get_perm_meta(self, codename):
        return next((p for p in MANAGEABLE_PERMISSIONS if p["codename"] == codename), None)

    def get(self, request, codename):
        from django.http import Http404
        perm_meta = self._get_perm_meta(codename)
        if perm_meta is None:
            raise Http404
        return render(request, "users/permissions_detail.html", {
            "perm": {**perm_meta, "users": get_users_with_permission(codename)},
        })

    def post(self, request, codename):
        from django.contrib.auth.models import Permission
        from django.http import Http404, HttpResponseBadRequest

        perm_meta = self._get_perm_meta(codename)
        if perm_meta is None:
            raise Http404

        action = request.POST.get("action")
        try:
            perm_obj = Permission.objects.get(codename=codename, content_type__app_label="users")
        except Permission.DoesNotExist:
            raise Http404

        if action == "add":
            email = request.POST.get("email", "")
            if not email.endswith("@dimagi.com"):
                messages.error(request, "Only @dimagi.com users can be assigned permissions.")
                return redirect(request.path)
            try:
                target_user = User.objects.get(email=email)
            except User.DoesNotExist:
                messages.error(request, "User not found.")
                return redirect(request.path)
            target_user.user_permissions.add(perm_obj)
            messages.success(request, f"Permission granted to {email}.")

        elif action == "remove":
            user_id = request.POST.get("user_id")
            try:
                target_user = User.objects.get(pk=user_id)
            except User.DoesNotExist:
                messages.error(request, "User not found.")
                return redirect(request.path)
            target_user.user_permissions.remove(perm_obj)
            messages.success(request, f"Permission removed from {target_user.email}.")

        return redirect(request.path)
```

**Step 4: Add URL to `users/urls.py`**

```python
path("permissions/<str:codename>/", views.PermissionDetailView.as_view(), name="permission_detail"),
```

**Step 5: Replace stub `permissions_per_permission.html`**

```html
{% load i18n %}
<div class="grid gap-4 grid-cols-1 sm:grid-cols-2 lg:grid-cols-3">
  {% for perm in permissions_data %}
    <a href="{% url 'users:permission_detail' perm.codename %}" class="card_bg block hover:shadow-md transition-shadow">
      <h3 class="card_title mb-1">{{ perm.label }}</h3>
      <p class="card_description mb-2">{{ perm.description }}</p>
      <span class="text-sm text-gray-500">{{ perm.users|length }} user{{ perm.users|length|pluralize }}</span>
    </a>
  {% endfor %}
</div>
```

**Step 6: Create `templates/users/permissions_detail.html`**

```html
{% extends "base.html" %}
{% load i18n %}

{% block title %}{{ perm.label }}{% endblock %}

{% block content %}
<div class="container max-w-2xl">
  <a href="{% url 'users:permission_management' %}?option=b" class="text-sm text-brand-deep-purple mb-4 inline-block">
    &larr; {% translate "Back" %}
  </a>
  <div class="card_bg">
    <h2 class="card_title text-lg mb-1">{{ perm.label }}</h2>
    <p class="card_description mb-4">{{ perm.description }}</p>

    <h3 class="font-semibold mb-2">{% translate "Current users" %}</h3>
    <ul class="mb-4 space-y-2">
      {% for u in perm.users %}
        <li class="flex items-center justify-between">
          <span class="text-sm">{{ u.email }}</span>
          <form method="post">
            {% csrf_token %}
            <input type="hidden" name="action" value="remove">
            <input type="hidden" name="user_id" value="{{ u.pk }}">
            <button type="submit" class="button button-md outline-style text-xs">{% translate "Remove" %}</button>
          </form>
        </li>
      {% empty %}
        <li class="text-sm text-gray-500">{% translate "No users assigned." %}</li>
      {% endfor %}
    </ul>

    <h3 class="font-semibold mb-2">{% translate "Add user" %}</h3>
    <form method="post" class="flex gap-2">
      {% csrf_token %}
      <input type="hidden" name="action" value="add">
      <input type="email" name="email" placeholder="user@dimagi.com"
             class="input flex-1 text-sm" required
             pattern=".*@dimagi\.com$"
             title="Must be a @dimagi.com email">
      <button type="submit" class="button button-md primary-dark">{% translate "Add" %}</button>
    </form>
  </div>
</div>
{% endblock content %}
```

**Step 7: Run tests**

```bash
pytest commcare_connect/users/tests/test_views.py::TestPermissionDetailView -v
```
Expected: PASS.

**Step 8: Commit**

```bash
git add commcare_connect/users/views.py commcare_connect/users/urls.py \
  commcare_connect/templates/users/
git commit -m "feat: add option B per-permission detail view"
```

---

### Task 7: Option C — matrix view

**Files:**
- Modify: `commcare_connect/users/views.py`
- Modify: `commcare_connect/templates/users/permissions_matrix.html` (replace stub)

**Step 1: Write failing tests**

```python
class TestPermissionMatrixView:
    @pytest.fixture
    def staff_user(self):
        from django.contrib.auth.models import Permission
        u = UserFactory(email="admin@dimagi.com")
        perm = Permission.objects.get(codename="permission_access")
        u.user_permissions.add(perm)
        return u

    def test_matrix_view_loads_dimagi_users(self, staff_user, client):
        dimagi = UserFactory(email="worker@dimagi.com")
        non_dimagi = UserFactory(email="other@example.com")
        client.force_login(staff_user)
        url = reverse("users:permission_management") + "?option=c"
        response = client.get(url)
        assert response.status_code == 200
        context = response.context
        assert dimagi in context["matrix_users"]
        assert non_dimagi not in context["matrix_users"]

    def test_matrix_post_assigns_permissions(self, staff_user, client):
        target = UserFactory(email="worker2@dimagi.com")
        client.force_login(staff_user)
        url = reverse("users:permission_matrix_save")
        response = client.post(url, {f"perm_{target.pk}_otp_access": "on"})
        assert response.status_code == 302
        target.refresh_from_db()
        assert target.has_perm("users.otp_access")

    def test_matrix_post_removes_unchecked_permissions(self, staff_user, client):
        from django.contrib.auth.models import Permission
        target = UserFactory(email="worker3@dimagi.com")
        perm = Permission.objects.get(codename="otp_access")
        target.user_permissions.add(perm)
        client.force_login(staff_user)
        url = reverse("users:permission_matrix_save")
        # Post without the checkbox — means remove
        response = client.post(url, {})
        assert response.status_code == 302
        target.refresh_from_db()
        assert not target.has_perm("users.otp_access")
```

**Step 2: Run to verify they fail**

```bash
pytest commcare_connect/users/tests/test_views.py::TestPermissionMatrixView -v
```

**Step 3: Update `PermissionManagementView.get()` to pass matrix context**

In the `get` method of `PermissionManagementView`, add matrix-specific context when `option == "c"`:

```python
def get(self, request):
    option = request.GET.get("option", "a").lower()
    template = self.OPTION_TEMPLATES.get(option, self.OPTION_TEMPLATES["a"])

    permissions_data = [
        {**perm, "users": get_users_with_permission(perm["codename"])}
        for perm in MANAGEABLE_PERMISSIONS
    ]

    ctx = {
        "option": option,
        "template": template,
        "permissions_data": permissions_data,
    }

    if option == "c":
        dimagi_users = User.objects.filter(email__endswith="@dimagi.com").order_by("email")
        codenames = [p["codename"] for p in MANAGEABLE_PERMISSIONS]
        from django.contrib.auth.models import Permission
        perms_qs = Permission.objects.filter(codename__in=codenames, content_type__app_label="users")
        perms_map = {p.codename: p for p in perms_qs}

        matrix = []
        for u in dimagi_users:
            user_perms = set(u.user_permissions.filter(codename__in=codenames).values_list("codename", flat=True))
            row = {
                "user": u,
                "perms": {p["codename"]: p["codename"] in user_perms for p in MANAGEABLE_PERMISSIONS},
            }
            matrix.append(row)
        ctx["matrix_users"] = dimagi_users
        ctx["matrix"] = matrix
        ctx["perm_labels"] = MANAGEABLE_PERMISSIONS

    return render(request, "users/permissions_management.html", ctx)
```

**Step 4: Add `PermissionMatrixSaveView` to `views.py`**

```python
class PermissionMatrixSaveView(LoginRequiredMixin, PermissionRequiredMixin, View):
    permission_required = PERMISSION_ACCESS

    def post(self, request):
        from django.contrib.auth.models import Permission

        codenames = [p["codename"] for p in MANAGEABLE_PERMISSIONS]
        dimagi_users = User.objects.filter(email__endswith="@dimagi.com").prefetch_related("user_permissions")
        perms_map = {
            p.codename: p
            for p in Permission.objects.filter(codename__in=codenames, content_type__app_label="users")
        }

        for user in dimagi_users:
            for codename in codenames:
                key = f"perm_{user.pk}_{codename}"
                should_have = key in request.POST
                currently_has = user.user_permissions.filter(codename=codename).exists()
                if should_have and not currently_has:
                    user.user_permissions.add(perms_map[codename])
                elif not should_have and currently_has:
                    user.user_permissions.remove(perms_map[codename])

        messages.success(request, "Permissions updated.")
        return redirect(reverse("users:permission_management") + "?option=c")
```

**Step 5: Add URL**

```python
path("permissions/matrix/save/", views.PermissionMatrixSaveView.as_view(), name="permission_matrix_save"),
```

**Note:** Place this URL **before** the `<str:codename>/` route to avoid it being matched as a codename.

**Step 6: Replace stub `permissions_matrix.html`**

```html
{% load i18n %}
<form method="post" action="{% url 'users:permission_matrix_save' %}">
  {% csrf_token %}
  <div class="overflow-x-auto">
    <table class="min-w-full text-sm">
      <thead>
        <tr>
          <th class="text-left py-2 pr-4 font-semibold">{% translate "User" %}</th>
          {% for perm in perm_labels %}
            <th class="py-2 px-2 font-semibold text-center text-xs">{{ perm.label }}</th>
          {% endfor %}
        </tr>
      </thead>
      <tbody>
        {% for row in matrix %}
          <tr class="border-t border-gray-200">
            <td class="py-2 pr-4">{{ row.user.email }}</td>
            {% for perm in perm_labels %}
              <td class="py-2 px-2 text-center">
                <input type="checkbox"
                       name="perm_{{ row.user.pk }}_{{ perm.codename }}"
                       {% if row.perms|dictsort:perm.codename %}
                       {% if row.perms.otp_access and perm.codename == 'otp_access' %}checked{% endif %}
                       {% endif %}
                       {% comment %}use a template tag workaround{% endcomment %}
                >
              </td>
            {% endfor %}
          </tr>
        {% empty %}
          <tr>
            <td colspan="99" class="py-4 text-center text-gray-500">{% translate "No @dimagi.com users found." %}</td>
          </tr>
        {% endfor %}
      </tbody>
    </table>
  </div>
  <div class="mt-4">
    <button type="submit" class="button button-md primary-dark">{% translate "Save changes" %}</button>
  </div>
</form>
```

**Note on the checkbox template:** Django templates can't do `row.perms[perm.codename]` directly. Use a custom template filter or pass a pre-flattened structure. The simplest fix is to add a template filter:

Create `commcare_connect/users/templatetags/users_extras.py`:
```python
from django import template

register = template.Library()

@register.filter
def get_item(dictionary, key):
    return dictionary.get(key)
```

Then in the template:
```html
{% load users_extras %}
...
<input type="checkbox"
       name="perm_{{ row.user.pk }}_{{ perm.codename }}"
       {% if row.perms|get_item:perm.codename %}checked{% endif %}>
```

Check if `commcare_connect/users/templatetags/` already exists; if not, create it with an `__init__.py`.

**Step 7: Run tests**

```bash
pytest commcare_connect/users/tests/test_views.py::TestPermissionMatrixView -v
```
Expected: PASS.

**Step 8: Commit**

```bash
git add commcare_connect/users/views.py commcare_connect/users/urls.py \
  commcare_connect/templates/users/permissions_matrix.html \
  commcare_connect/users/templatetags/
git commit -m "feat: add option C matrix permission view"
```

---

### Task 8: Add to Internal Features hub + sidebar visibility

**Files:**
- Modify: `commcare_connect/users/views.py` (the `internal_features` function)

**Step 1: Add the permission management card to `internal_features` view**

In `views.py`, in the `internal_features` function (around line 315), add to the `features` list:

```python
{
    "perm": PERMISSION_ACCESS,
    "name": "Permission Management",
    "description": "Assign and revoke internal permissions for @dimagi.com users.",
    "url": reverse("users:permission_management"),
},
```

Also add `PERMISSION_ACCESS` to the import block at the top of the file.

**Step 2: Write a test**

```python
def test_internal_features_shows_permission_management(client):
    from django.contrib.auth.models import Permission
    u = UserFactory(email="admin@dimagi.com")
    perm = Permission.objects.get(codename="permission_access")
    u.user_permissions.add(perm)
    client.force_login(u)
    response = client.get(reverse("users:internal_features"))
    assert response.status_code == 200
    assert b"Permission Management" in response.content
```

**Step 3: Run the test**

```bash
pytest commcare_connect/users/tests/test_views.py::test_internal_features_shows_permission_management -v
```
Expected: PASS (after adding to the features list).

**Step 4: Commit**

```bash
git add commcare_connect/users/views.py commcare_connect/users/tests/test_views.py
git commit -m "feat: add permission management to internal features hub"
```

---

### Task 9: Run full test suite and verify

**Step 1: Run all users tests**

```bash
pytest commcare_connect/users/tests/ -v
```
Expected: all PASS.

**Step 2: Run full suite**

```bash
pytest --tb=short -q
```
Expected: no regressions.

**Step 3: Final commit if any fixes needed**

```bash
git add -p
git commit -m "fix: address test failures from permission management feature"
```
