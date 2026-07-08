import django_tables2 as tables
from django.utils.html import format_html
from django.utils.translation import gettext
from django_tables2 import columns

from commcare_connect.opportunity.tables import IndexColumn
from commcare_connect.organization.models import OrganizationInvite, UserOrganizationMembership


class OrgMemberTable(tables.Table):
    select = tables.CheckBoxColumn(
        accessor="pk",
        attrs={
            "th__input": {
                "@click": "toggleSelectAll()",
                "x-model": "selectAll",
                "name": "select_all",
                "type": "checkbox",
                "class": "checkbox",
            },
            "td__input": {
                "x-model": "selected",
                "@click.stop": "",  # used to stop click propagation
                "name": "row_select",
                "type": "checkbox",
                "class": "checkbox",
                "value": lambda record: record.pk,
                "id": lambda record: f"row_checkbox_{record.pk}",
                ":disabled": lambda record: f"currentUserMembershipId === '{record.pk}'",
            },
        },
    )
    use_view_url = True
    index = IndexColumn()
    user = columns.Column(verbose_name="member", accessor="user__email")
    role = tables.Column()

    class Meta:
        model = UserOrganizationMembership
        fields = ("role", "user")
        sequence = ("select", "index", "user", "role")

    def render_role(self, value):
        return format_html("<div class=' underline underline-offset-4'>{}</div>", value)


class PendingInviteTable(tables.Table):
    index = IndexColumn()
    email = tables.Column()
    role = tables.Column()
    date_created = columns.DateColumn(verbose_name="Invited on")
    revoke = tables.Column(empty_values=(), verbose_name="", orderable=False)

    class Meta:
        model = OrganizationInvite
        fields = ("email", "role", "date_created")
        sequence = ("index", "email", "role", "date_created", "revoke")

    def render_role(self, record):
        return format_html("<div class=' underline underline-offset-4'>{}</div>", record.get_role_display())

    def render_revoke(self, record):
        return format_html(
            '<button type="button" class="button button-sm outline-style text-red-600" '
            'hx-post="{}" hx-target="#pending_invites_container" hx-swap="innerHTML" hx-confirm="{}">{}</button>',
            record.get_revoke_url(),
            gettext("Revoke invite for {email}?").format(email=record.email),
            gettext("Revoke"),
        )
