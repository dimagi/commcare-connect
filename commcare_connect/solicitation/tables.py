import django_tables2 as tables
from django.urls import reverse
from django.utils.translation import gettext_lazy as _
from django_tables2 import columns

from commcare_connect.opportunity.tables import STAT_COLUMN_ATTRS
from commcare_connect.solicitation.models import EDITABLE_STATUSES, Solicitation
from commcare_connect.utils.tables import DMYTColumn, IndexColumn, select_column


def details_url(record, table):
    if record.status in EDITABLE_STATUSES:
        return reverse("solicitation:details", args=(table.request.org.slug, record.solicitation_id))
    return None


class SolicitationTable(tables.Table):
    title = columns.Column(verbose_name=_("Title"), linkify=details_url, attrs={"a": {"class": "underline"}})
    type = columns.Column(verbose_name=_("Type"))
    visibility = columns.Column(verbose_name=_("Visibility"), accessor="public")
    status = columns.TemplateColumn(verbose_name=_("Status"), template_name="solicitation/status_badge.html")
    application_deadline = DMYTColumn(verbose_name=_("Deadline"))
    invited_count = columns.Column(verbose_name=_("Invited"), attrs=STAT_COLUMN_ATTRS)
    response_count = columns.Column(verbose_name=_("Responses"), attrs=STAT_COLUMN_ATTRS)

    class Meta:
        model = Solicitation
        fields = ("title", "type", "visibility", "status", "application_deadline", "invited_count", "response_count")
        empty_text = _("No solicitations yet.")

    def render_visibility(self, value):
        return _("Public") if value else _("Private")

    def render_invited_count(self, value, record):
        # Anyone can apply to a public solicitation, so it has no invitations to count.
        return "—" if record.public else value


class SolicitationInvitationTable(tables.Table):
    select = select_column()
    index = IndexColumn()
    organization = columns.Column(verbose_name=_("Organization"), order_by="organization__name")
    invited_by = columns.Column(verbose_name=_("Invited by"), orderable=False)
    date_created = DMYTColumn(verbose_name=_("Invited on"))

    class Meta:
        empty_text = _("No organizations invited yet.")
