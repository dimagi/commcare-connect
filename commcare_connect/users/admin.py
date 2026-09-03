from django.contrib import admin
from django.contrib.auth import admin as auth_admin
from django.contrib.auth import get_user_model
from django.utils.translation import gettext_lazy as _

from commcare_connect.organization.models import Organization, PrimarySector, UserOrganizationMembership
from commcare_connect.users.forms import AdminOrganizationForm, UserAdminChangeForm, UserAdminCreationForm
from commcare_connect.users.models import ConnectIDUserLink

User = get_user_model()


class WebUserFilter(admin.SimpleListFilter):
    title = _("Web Users")
    parameter_name = "web_users"

    def lookups(self, request, model_admin):
        return (("web_users", _("Web Users")),)

    def queryset(self, request, queryset):
        if self.value() == "web_users":
            return queryset.exclude(email__isnull=True)
        return queryset


@admin.register(User)
class UserAdmin(auth_admin.UserAdmin):
    form = UserAdminChangeForm
    add_form = UserAdminCreationForm
    fieldsets = (
        (None, {"fields": ("email", "password")}),
        (_("Personal info"), {"fields": ("name",)}),
        (
            _("Permissions"),
            {
                "fields": (
                    "is_active",
                    "is_staff",
                    "is_superuser",
                    "groups",
                    "user_permissions",
                ),
            },
        ),
        (_("Important dates"), {"fields": ("last_login", "date_joined")}),
    )
    list_display = ["email", "name", "is_superuser"]
    search_fields = ["email", "name"]
    ordering = ["id"]
    list_filter = [
        "is_staff",
        "is_superuser",
        "is_active",
        WebUserFilter,
    ]
    add_fieldsets = (
        (
            None,
            {
                "classes": ("wide",),
                "fields": ("email", "password1", "password2"),
            },
        ),
    )


class UserOrganizationMembershipInline(admin.TabularInline):
    list_display = ["organization", "user", "role"]
    model = UserOrganizationMembership


@admin.register(Organization)
class OrganizationAdmin(admin.ModelAdmin):
    form = AdminOrganizationForm
    list_display = [
        "name",
        "short_name",
        "created_by",
        "program_manager",
        "funder",
        "verified",
        "has_used_connect",
    ]
    search_fields = ["name", "short_name"]
    ordering = ["name"]
    inlines = [UserOrganizationMembershipInline]
    list_filter = ["program_manager", "funder", "verified", "has_used_connect"]
    filter_horizontal = ["countries"]


@admin.register(PrimarySector)
class PrimarySectorAdmin(admin.ModelAdmin):
    list_display = ["name", "slug", "description"]
    search_fields = ["name", "slug", "description"]
    ordering = ["name"]
    prepopulated_fields = {"slug": ("name",)}


@admin.register(ConnectIDUserLink)
class ConnectIDUserLinkAdmin(admin.ModelAdmin):
    list_display = ["user", "commcare_username", "domain"]
    ordering = ["user"]
