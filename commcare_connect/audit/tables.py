from django.urls import reverse
from django.utils.html import format_html
from django.utils.translation import gettext as _
from django.utils.translation import gettext_lazy as _l
from django_tables2 import columns, tables

from commcare_connect.audit.models import AuditReport


class AuditReportTable(tables.Table):
    audit_id = columns.Column(
        accessor="serial",
        verbose_name=_l("Audit ID"),
        order_by=("period_end",),
    )
    date = columns.Column(
        accessor="period_end",
        verbose_name=_l("Date"),
        orderable=True,
    )
    status = columns.Column(verbose_name=_l("Status"))
    reviewer = columns.Column(
        accessor="completed_by__name",
        verbose_name=_l("Reviewer"),
        default="—",
        orderable=True,
    )
    view = columns.Column(
        accessor="pk",
        verbose_name="",
        orderable=False,
    )

    class Meta:
        model = AuditReport
        fields = ("audit_id", "date", "status", "reviewer", "view")
        empty_text = _l("No audits have been generated yet.")
        order_by = ("-period_end",)
        attrs = {"class": "table table-hover"}

    def __init__(self, *args, opportunity=None, **kwargs):
        self.opportunity = opportunity
        super().__init__(*args, **kwargs)

    def render_audit_id(self, record):
        url = reverse(
            "opportunity:audit:audit_report_detail",
            kwargs={
                "org_slug": self.opportunity.organization.slug,
                "opportunity_id": self.opportunity.opportunity_id,
                "audit_report_id": record.audit_report_id,
            },
        )
        return format_html('<a class="text-brand-deep-purple hover:underline" href="{}">#{}</a>', url, record.serial)

    def render_date(self, value):
        return value.strftime("%b %-d, %Y")

    def render_status(self, record):
        if record.status == AuditReport.Status.COMPLETED:
            modifier = "positive-dark"
            label = _("Complete")
        else:
            modifier = "warning-dark"
            label = _("Pending")
        return format_html('<span class="badge badge-md {}">{}</span>', modifier, label)

    def render_view(self, record):
        url = reverse(
            "opportunity:audit:audit_report_detail",
            kwargs={
                "org_slug": self.opportunity.organization.slug,
                "opportunity_id": self.opportunity.opportunity_id,
                "audit_report_id": record.audit_report_id,
            },
        )
        return format_html('<a href="{}" aria-label="{}">&rsaquo;</a>', url, _("View audit"))
