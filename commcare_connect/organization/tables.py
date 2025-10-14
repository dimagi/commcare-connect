import django_tables2 as tables
from django.utils.html import format_html
from django_tables2 import columns

from commcare_connect.opportunity.tables import IndexColumn
from commcare_connect.organization.models import UserOrganizationMembership


class OrgMemberTable(tables.Table):
    use_view_url = True

    index = IndexColumn()
    user = columns.Column(verbose_name="member", accessor="user__email")
    role = tables.Column()

    class Meta:
        model = UserOrganizationMembership
        fields = ("role", "user")
        sequence = ("index", "user", "role")

    def render_role(self, value):
        return format_html("<div class=' underline underline-offset-4'>{}</div>", value)
