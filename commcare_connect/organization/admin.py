from django import forms
from django.contrib import admin, messages
from django.contrib.admin.models import DELETION, LogEntry
from django.contrib.admin.options import get_content_type_for_model
from django.core.exceptions import PermissionDenied
from django.db.models import QuerySet
from django.template.response import TemplateResponse

from commcare_connect.organization.merge import (
    HANDLED_RELATIONS,
    MergeNotAllowed,
    merge_organizations,
    programs_hidden_by_merge,
    relation_counts,
)
from commcare_connect.organization.models import Organization, UserOrganizationMembership
from commcare_connect.users.forms import OrganizationCreationForm

MERGE_ACTION = "merge_workspaces"

# Fields the target keeps and the source loses, so an admin can see what is about to be discarded.
PROFILE_FIELDS = [
    ("Name", "name"),
    ("Slug", "slug"),
    ("Program manager", "program_manager"),
    ("Funder", "funder"),
    ("LLO entity", "llo_entity"),
    ("Created", "date_created"),
    ("Created by", "created_by"),
]


def workspace_label(organization: Organization) -> str:
    return f"{organization.name} ({organization.slug})"


def merge_preview(organizations: list[Organization]) -> dict:
    counts = [relation_counts(organization) for organization in organizations]
    return {
        "column_headers": [workspace_label(organization) for organization in organizations],
        "profile_rows": [
            (label, [getattr(organization, field) for organization in organizations])
            for label, field in PROFILE_FIELDS
        ],
        "count_rows": [(label, [count[label] for count in counts]) for label in sorted(HANDLED_RELATIONS)],
        "flag_names": [sorted(organization.flag_set.values_list("name", flat=True)) for organization in organizations],
        "hidden_programs": _hidden_programs_per_survivor(organizations),
    }


def _hidden_programs_per_survivor(organizations: list[Organization]) -> list[list[str]]:
    """One cell per column, answering "which programs go dark if I keep this workspace?".

    The target is not chosen yet when the confirmation page renders, so every candidate is costed against the
    other one.
    """
    return [
        sorted(
            program
            for other in organizations
            if other.pk != survivor.pk
            for program in programs_hidden_by_merge(other, survivor)
        )
        for survivor in organizations
    ]


class OrganizationMergeForm(forms.Form):
    """Picks which of the two selected workspaces survives the merge."""

    target = forms.ModelChoiceField(
        queryset=Organization.objects.none(),
        widget=forms.RadioSelect,
        empty_label=None,
        label="Workspace to keep",
    )

    def __init__(self, *args, selected: QuerySet, **kwargs):
        super().__init__(*args, **kwargs)
        self.selected = selected
        # Scoping the queryset to the reviewed pair is what rejects a forged target.
        self.fields["target"].queryset = selected
        self.fields["target"].label_from_instance = workspace_label

    def source_and_target(self) -> tuple[Organization, Organization]:
        target = self.cleaned_data["target"]
        source = next(org for org in self.selected if org.pk != target.pk)
        return source, target


class UserOrganizationMembershipInline(admin.TabularInline):
    list_display = ["organization", "user", "role"]
    model = UserOrganizationMembership


@admin.register(Organization)
class OrganizationAdmin(admin.ModelAdmin):
    form = OrganizationCreationForm
    list_display = ["name", "created_by", "program_manager", "funder"]
    search_fields = ["name"]
    ordering = ["name"]
    inlines = [UserOrganizationMembershipInline]
    list_filter = ["program_manager", "funder"]
    actions = [MERGE_ACTION]

    def get_actions(self, request):
        actions = super().get_actions(request)
        if not request.user.is_superuser:
            actions.pop(MERGE_ACTION, None)
        return actions

    @admin.action(description="Merge selected workspaces (irreversible)")
    def merge_workspaces(self, request, queryset):
        if not request.user.is_superuser:
            raise PermissionDenied

        selected = queryset.order_by("pk")
        if selected.count() != 2:
            self.message_user(request, "Select exactly two workspaces to merge.", messages.WARNING)
            return None

        confirming = "confirm" in request.POST
        form = OrganizationMergeForm(request.POST if confirming else None, selected=selected)
        if confirming and form.is_valid():
            return self._merge(request, *form.source_and_target())
        return self._confirmation_page(request, selected, form)

    def _merge(self, request, source: Organization, target: Organization):
        # Captured up front: ``source.delete()`` clears the instance's pk, and the log entry needs it.
        source_pk, source_repr = source.pk, str(source)
        try:
            summary = merge_organizations(source, target)
        except MergeNotAllowed as error:
            self.message_user(request, str(error), messages.ERROR)
            return None

        LogEntry.objects.log_action(
            user_id=request.user.pk,
            content_type_id=get_content_type_for_model(target).pk,
            object_id=source_pk,
            object_repr=source_repr,
            action_flag=DELETION,
        )
        self.log_change(request, target, f"Merged workspace {summary.source_slug} into this one")
        self.message_user(
            request,
            f"Merged {summary.source_slug} into {summary.target_slug}. {summary}",
            messages.SUCCESS,
        )
        return None

    def _confirmation_page(self, request, selected: QuerySet, form: OrganizationMergeForm) -> TemplateResponse:
        organizations = list(selected)
        context = {
            **self.admin_site.each_context(request),
            **merge_preview(organizations),
            "title": "Merge workspaces",
            "opts": self.model._meta,
            "media": self.media + form.media,
            "form": form,
            "action": MERGE_ACTION,
            "selected_pks": [organization.pk for organization in organizations],
        }
        return TemplateResponse(request, "admin/organization/merge_confirmation.html", context)
