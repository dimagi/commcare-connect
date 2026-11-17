import django_tables2 as tables
from django.urls import reverse
from django.utils.translation import gettext_lazy as _
from django_tables2 import columns

from commcare_connect.opportunity.tables import STAT_COLUMN_ATTRS
from commcare_connect.organization.tables import WRAPPING_COLUMN_ATTRS, DirectoryTable
from commcare_connect.solicitation.models import Solicitation
from commcare_connect.utils.tables import DMYTColumn, IndexColumn, select_column


def details_url(record, table):
    if record.is_editable:
        return reverse("solicitation:details", args=(table.request.org.slug, record.solicitation_id))
    return None


def invitations_url(record, table):
    # Invitations are only managed while a solicitation is open.
    if record.is_open:
        return reverse("solicitation:invitations", args=(table.request.org.slug, record.solicitation_id))
    return None


class SolicitationTable(tables.Table):
    title = columns.Column(verbose_name=_("Title"), linkify=details_url, attrs={"a": {"class": "underline"}})
    type = columns.Column(verbose_name=_("Type"))
    visibility = columns.Column(verbose_name=_("Visibility"), accessor="public")
    status = columns.TemplateColumn(verbose_name=_("Status"), template_name="solicitation/status_badge.html")
    application_deadline = DMYTColumn(verbose_name=_("Deadline"))
    invited_count = columns.Column(
        verbose_name=_("Invited"), linkify=invitations_url, attrs=STAT_COLUMN_ATTRS | {"a": {"class": "underline"}}
    )
    response_count = columns.Column(verbose_name=_("Responses"), attrs=STAT_COLUMN_ATTRS)

    class Meta:
        model = Solicitation
        fields = ("title", "type", "visibility", "status", "application_deadline", "invited_count", "response_count")
        empty_text = _("No solicitations yet.")

    def render_visibility(self, value):
        return _("Public") if value else _("Private")


# The date headers are longer than the dates under them, so they wrap rather than widen the table.
WRAPPING_HEADER_ATTRS = {"th": {"class": "whitespace-normal"}}


class SolicitationDirectoryTable(DirectoryTable):
    organization = columns.Column(
        verbose_name=_("Organization"), accessor="organization__name", attrs=WRAPPING_COLUMN_ATTRS
    )
    program = columns.Column(verbose_name=_("Program"), accessor="program__name", attrs=WRAPPING_COLUMN_ATTRS)
    title = columns.Column(verbose_name=_("Title"), attrs=WRAPPING_COLUMN_ATTRS)
    type = columns.Column(verbose_name=_("Type"))
    status = columns.TemplateColumn(verbose_name=_("Status"), template_name="solicitation/status_badge.html")
    public = columns.Column(verbose_name=_("Public"))
    expected_start_date = DMYTColumn(verbose_name=_("Expected Start Date"), attrs=WRAPPING_HEADER_ATTRS)
    expected_end_date = DMYTColumn(verbose_name=_("Expected End Date"), attrs=WRAPPING_HEADER_ATTRS)
    application_deadline = DMYTColumn(verbose_name=_("Application Deadline"), attrs=WRAPPING_HEADER_ATTRS)
    actions = columns.TemplateColumn(
        verbose_name=_("Actions"),
        template_name="organization/directory/solicitation_row_actions.html",
        orderable=False,
        empty_values=(),
    )

    class Meta:
        model = Solicitation
        fields = (
            "organization",
            "program",
            "title",
            "type",
            "status",
            "public",
            "expected_start_date",
            "expected_end_date",
            "application_deadline",
        )
        sequence = (*fields, "actions")
        default = "—"
        empty_text = _("No solicitations found.")

    def render_type(self, record):
        # EOI or RFP; the full names would widen the table.
        return Solicitation.Type(record.type).name

    def render_public(self, value):
        return _("Yes") if value else _("No")


class SolicitationInvitationTable(tables.Table):
    select = select_column()
    index = IndexColumn()
    organization = columns.Column(verbose_name=_("Organization"), accessor="organization__name")
    invited_by = columns.Column(verbose_name=_("Invited by"), orderable=False)
    date_created = DMYTColumn(verbose_name=_("Invited on"))

    class Meta:
        empty_text = _("No organizations invited yet.")
