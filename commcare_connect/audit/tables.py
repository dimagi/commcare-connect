from django.urls import reverse
from django.utils.html import format_html
from django.utils.translation import gettext as _
from django.utils.translation import gettext_lazy as _l
from django_tables2 import columns

from commcare_connect.audit.calculations import format_value
from commcare_connect.audit.models import AuditReport, AuditReportEntry
from commcare_connect.utils.tables import DMYTColumn, IndexColumn, OrgContextTable


class AuditReportTable(OrgContextTable):
    index = IndexColumn()
    date = DMYTColumn(
        accessor="date_created",
        verbose_name=_l("Generation Date"),
        orderable=True,
        order_by=("date_created",),
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
        attrs={"th": {"class": "col-action"}, "td": {"class": "col-action"}},
    )

    class Meta:
        model = AuditReport
        fields = ("index", "date", "status", "reviewer", "view")
        empty_text = _l("No audits have been generated yet.")
        order_by = ("-date_created",)
        row_attrs = {"class": "group"}

    def __init__(self, *args, opportunity=None, **kwargs):
        self.opportunity = opportunity
        super().__init__(*args, **kwargs)

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
                "org_slug": self.org_slug,
                "opp_id": self.opportunity.opportunity_id,
                "audit_report_id": record.audit_report_id,
            },
        )
        return format_html(
            '<div class="opacity-0 group-hover:opacity-100 transition-opacity duration-200 text-end">'
            '<a href="{}" aria-label="{}">'
            '<i class="fa-solid fa-chevron-right text-brand-deep-purple"></i>'
            "</a></div>",
            url,
            _("View audit"),
        )


class CalcColumn(columns.Column):
    """A column that reads a specific calculation result from ``record.results``.

    Shows the value, "N/A" for insufficient data, or the value styled as a
    negative badge when ``in_range`` is False.
    """

    def __init__(self, calc_name, tooltip="", **kw):
        self.calc_name = calc_name
        self.calc_tooltip = tooltip
        kw.setdefault("attrs", {"th": {"class": "whitespace-normal align-bottom w-28"}})
        super().__init__(empty_values=(), order_by=(f"results__{calc_name}__value",), **kw)

    @property
    def header(self):
        label = self.verbose_name
        if not self.calc_tooltip:
            return label
        return format_html(
            '{} <span x-data x-tooltip.raw="{}" class="inline-flex items-center cursor-help">'
            '<i class="fa-solid fa-circle-info text-xs text-gray-400"></i></span>',
            label,
            self.calc_tooltip,
        )

    def render(self, record):
        r = record.results.get(self.calc_name, {})
        if not r.get("has_sufficient_data"):
            return format_html('<span class="text-gray-400">{}</span>', _("N/A"))
        display = format_value(r, with_fraction=True)
        if not r.get("in_range"):
            return format_html('<span class="badge badge-md negative-dark">{}</span>', display)
        return display


class ActionColumn(columns.Column):
    """Rightmost column: Review button for flagged+unreviewed, Done badge for reviewed."""

    def __init__(self):
        super().__init__(
            accessor="pk",
            verbose_name="",
            orderable=False,
            empty_values=(),
            attrs={
                "th": {"class": "w-32"},
                "td": {"class": "w-32 text-right"},
            },
        )

    def render(self, record, table):
        if record.flagged and not record.reviewed:
            url = reverse(
                "opportunity:audit:audit_report_task_modal",
                kwargs={
                    "org_slug": table.org_slug,
                    "opp_id": table.opportunity.opportunity_id,
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


class AuditReportEntryTable(OrgContextTable):
    user = columns.Column(
        accessor="opportunity_access__user__name",
        verbose_name=_l("Connect Worker"),
        attrs={"th": {"class": "whitespace-normal align-bottom w-40"}},
    )

    class Meta:
        model = AuditReportEntry
        fields = ("user",)
        empty_text = _l("No workers.")

    def __init__(self, data, *, opportunity, report, columns_spec, **kw):
        self.opportunity = opportunity
        self.report = report
        extra = [
            (name, CalcColumn(calc_name=name, verbose_name=label, tooltip=tooltip))
            for name, label, tooltip in columns_spec
        ]
        extra.append(("action", ActionColumn()))
        super().__init__(data, extra_columns=extra, **kw)
