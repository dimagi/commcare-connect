import django_tables2 as tables
from django.utils.translation import gettext_lazy as _
from django_tables2 import columns

from commcare_connect.opportunity.tables import STAT_COLUMN_ATTRS
from commcare_connect.solicitation.models import Solicitation
from commcare_connect.utils.tables import DMYTColumn


class SolicitationTable(tables.Table):
    status = columns.TemplateColumn(verbose_name=_("Status"), template_name="solicitation/status_badge.html")
    application_deadline = DMYTColumn(verbose_name=_("Deadline"))
    response_count = columns.Column(verbose_name=_("Responses"), attrs=STAT_COLUMN_ATTRS)

    class Meta:
        model = Solicitation
        fields = ("title", "type", "status", "application_deadline", "response_count")
        empty_text = _("No solicitations yet.")
