import django_tables2 as tables
from django.utils.html import format_html
from django_tables2 import columns

from commcare_connect.opportunity.tables import IndexColumn
from commcare_connect.organization.models import UserOrganizationMembership


class OrgMemberTable(tables.Table):
    use_view_url = True

    index = IndexColumn()
    user = columns.Column(verbose_name="member", accessor="user__email")
    accepted = columns.Column(verbose_name="Status")
    role = tables.Column()

    class Meta:
        model = UserOrganizationMembership
        fields = ("role", "accepted", "user")
        sequence = ("index", "user", "accepted", "role")

    def render_accepted(self, value):
        text_color = "text-green-600" if value else "text-orange-600"
        bg_color = "bg-green-600/20" if value else "bg-orange-600/20"
        text = "Accepted" if value else "Pending"

        return format_html(
            '<div class="flex justify-start text-sm font-normal truncate text-brand-deep-purple overflow-clip overflow-ellipsis">'  # noqa: E501
            '<span class="badge badge-sm {} {}">{}</span>'
            "</div>",
            bg_color,
            text_color,
            text,
        )

    def render_role(self, value):
        return format_html("<div class=' underline underline-offset-4'>{}</div>", value)
