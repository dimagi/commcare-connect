from django.urls import reverse
from django.utils.html import format_html
from django.utils.translation import gettext as _
from django.utils.translation import gettext_lazy as _l
from django_tables2 import columns, tables

from commcare_connect.audit.models import AuditReport, AuditReportEntry


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


class CalcColumn(columns.Column):
    """A column that reads a specific calculation result from ``record.results``.

    Shows the value, "N/A" for insufficient data, or the value styled as a
    negative badge when ``in_range`` is False.
    """

    def __init__(self, calc_name, **kw):
        self.calc_name = calc_name
        super().__init__(empty_values=(), orderable=False, **kw)

    def render(self, record):
        r = record.results.get(self.calc_name, {})
        if not r.get("has_sufficient_data"):
            return format_html('<span class="text-gray-400">{}</span>', _("N/A"))
        value = r.get("value")
        if not r.get("in_range"):
            return format_html('<span class="badge badge-md negative-dark">{}</span>', value)
        return value if value is not None else ""


class ActionColumn(columns.Column):
    """Rightmost column: Review button for flagged+unreviewed, Done badge for reviewed."""

    def __init__(self):
        super().__init__(
            accessor="pk",
            verbose_name="",
            orderable=False,
            empty_values=(),
        )

    def render(self, record, table):
        if record.flagged and not record.reviewed:
            url = reverse(
                "opportunity:audit:audit_report_task_modal",
                kwargs={
                    "org_slug": table.opportunity.organization.slug,
                    "opportunity_id": table.opportunity.opportunity_id,
                    "audit_report_id": table.report.audit_report_id,
                    "entry_id": record.audit_report_entry_id,
                },
            )
            return format_html(
                '<button type="button" class="button button-md primary-dark"'
                ' hx-get="{}" hx-target="#modal-root" hx-swap="innerHTML">{}</button>',
                url,
                _("Review"),
            )
        if record.reviewed:
            return format_html('<span class="badge badge-md positive-dark">{}</span>', _("Done"))
        return ""


class AuditReportEntryTable(tables.Table):
    user = columns.Column(
        accessor="opportunity_access__user__name",
        verbose_name=_l("Connect Worker"),
    )

    class Meta:
        model = AuditReportEntry
        fields = ("user",)
        empty_text = _l("No workers.")
        order_by = ("user",)

    def __init__(self, data, *, opportunity, report, columns_spec, **kw):
        self.opportunity = opportunity
        self.report = report
        extra = [(name, CalcColumn(calc_name=name, verbose_name=label)) for name, label in columns_spec]
        extra.append(("action", ActionColumn()))
        super().__init__(data, extra_columns=extra, **kw)
