import django_tables2 as tables
from django.utils.translation import gettext_lazy as _
from django_tables2 import columns

from commcare_connect.organization.models import OrganizationInvite, UserOrganizationMembership
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

    class Meta:
        model = UserOrganizationMembership
        fields = ("role", "user")
        sequence = ("select", "index", "user", "role")


class PendingInviteTable(tables.Table):
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
