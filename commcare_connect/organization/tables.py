import django_tables2 as tables
from django.template.loader import render_to_string
from django.utils.functional import lazy
from django.utils.safestring import SafeString
from django.utils.translation import gettext_lazy as _
from django_tables2 import columns

from commcare_connect.organization.models import (
    ORGANIZATION_STATUS_DEFINITIONS,
    Contact,
    Organization,
    OrganizationInvite,
    UserOrganizationMembership,
)
from commcare_connect.utils.tables import DMYTColumn, IndexColumn, select_column

ACTION_COLUMN_ATTRS = {"th": {"class": "col-action"}, "td": {"class": "col-action"}}
ROLE_BADGE_TEMPLATE = "organization/role_badge.html"


class OrgMemberTable(tables.Table):
    select = select_column(
        td_extra={":disabled": lambda record: f"currentUserMembershipId === '{record.pk}'"},
    )
    use_view_url = True
    index = IndexColumn()
    user = columns.Column(verbose_name=_("Member"), accessor="user__email")
    role = columns.TemplateColumn(verbose_name=_("Role"), template_name=ROLE_BADGE_TEMPLATE)
    accepted_at = DMYTColumn(verbose_name=_("Accepted On"))

    class Meta:
        model = UserOrganizationMembership
        fields = ("role", "user", "accepted_at")
        sequence = ("select", "index", "user", "role", "accepted_at")


class PendingInviteTable(tables.Table):
    # This table is rendered as an htmx fragment, so sort links must be built from the
    # hosting page's URL (the referer) rather than from the fragment endpoint's own path.
    use_view_url = True
    index = IndexColumn()
    email = tables.Column(verbose_name=_("Email"))
    role = columns.TemplateColumn(verbose_name=_("Role"), template_name=ROLE_BADGE_TEMPLATE)
    date_modified = DMYTColumn(verbose_name=_("Invited on"))
    expiry_date = DMYTColumn(verbose_name=_("Expires on"), orderable=False)
    actions = columns.TemplateColumn(
        verbose_name="",
        orderable=False,
        attrs=ACTION_COLUMN_ATTRS,
        template_name="organization/pending_invite_actions.html",
    )

    class Meta:
        model = OrganizationInvite
        fields = ("email", "role", "date_modified")
        sequence = ("index", "email", "role", "date_modified", "expiry_date", "actions")
        empty_text = _("No pending invites.")
        # The organization page hosts this table alongside OrgMemberTable and feeds both
        # from one query string, so prefix these params to keep sorting and paging
        # on the two tables independent.
        prefix = "invites-"


def _status_column_header():
    definitions_html = render_to_string(
        "organization/directory/status_definitions.html",
        {"definitions": ORGANIZATION_STATUS_DEFINITIONS.items()},
    )
    return render_to_string("organization/directory/status_header.html", {"definitions_html": definitions_html})


class OrganizationDirectoryTable(tables.Table):
    use_view_url = False
    name = columns.Column(verbose_name=_("Name"))
    status = columns.TemplateColumn(
        # Lazy so the template isn't rendered at import time.
        verbose_name=lazy(_status_column_header, SafeString)(),
        template_name="organization/directory/status_badge.html",
    )
    primary_sectors = columns.ManyToManyColumn(verbose_name=_("Primary Sectors"))
    # Team size is stored as a bracket ("11-50"), which doesn't sort meaningfully as text.
    team_size = columns.Column(verbose_name=_("Org Team Size"), orderable=False)
    flws_managed = columns.Column(verbose_name=_("FLWs Managed"))
    date_created = columns.DateTimeColumn(verbose_name=_("Added"), format="d-M-Y")
    actions = columns.TemplateColumn(
        verbose_name=_("Actions"),
        template_name="organization/directory/row_actions.html",
        orderable=False,
        empty_values=(),
    )

    class Meta:
        model = Organization
        fields = ("name", "status", "primary_sectors", "team_size", "flws_managed", "date_created")
        sequence = (*fields, "actions")
        default = "—"
        empty_text = _("No organizations found.")


class ContactDirectoryTable(tables.Table):
    use_view_url = False
    name = columns.Column(verbose_name=_("Contact Name"))
    organization = columns.Column(verbose_name=_("Organization"), accessor="organization__name")
    title = columns.Column(verbose_name=_("Role / Title"))
    is_main_poc = columns.TemplateColumn(
        verbose_name=_("POC"),
        template_name="organization/directory/main_poc_badge.html",
        orderable=False,
    )
    email = columns.TemplateColumn(verbose_name=_("Email"), template_name="organization/directory/email_link.html")
    phone = columns.Column(verbose_name=_("Phone"), orderable=False)
    actions = columns.TemplateColumn(
        verbose_name=_("Actions"),
        template_name="organization/directory/contact_row_actions.html",
        orderable=False,
        empty_values=(),
    )

    class Meta:
        model = Contact
        fields = ("name", "organization", "title", "is_main_poc", "email", "phone")
        sequence = (*fields, "actions")
        default = "—"
        empty_text = _("No contacts found.")


class ContactExportTable(tables.Table):
    name = columns.Column(verbose_name=_("Contact Name"))
    organization = columns.Column(verbose_name=_("Organization"), accessor="organization__name")
    title = columns.Column(verbose_name=_("Role / Title"))
    is_main_poc = columns.Column(verbose_name=_("Main POC"))
    email = columns.Column(verbose_name=_("Email"))
    phone = columns.Column(verbose_name=_("Phone"))

    def value_is_main_poc(self, value):
        return _("Yes") if value else _("No")
