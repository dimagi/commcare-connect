import logging

from django.db import connection, transaction
from django.db.utils import OperationalError
from django.http import HttpResponse, HttpResponseBadRequest
from django.shortcuts import render
from django.urls import reverse
from django.utils.text import slugify
from django.utils.translation import gettext as _
from django_tables2.export import TableExport
from waffle.decorators import waffle_flag

from commcare_connect.flags.flag_names import MICROPLANNING
from commcare_connect.microplanning.coverage_progress import CoverageProgressReport
from commcare_connect.microplanning.filters import CoverageProgressFilterSet
from commcare_connect.microplanning.models import WorkArea
from commcare_connect.microplanning.tables import CoverageWAGTable, CoverageWardTable
from commcare_connect.microplanning.utils import quoted_missing_deliver_units
from commcare_connect.organization.decorators import (
    opp_admin_access_required,
    opportunity_required,
)

logger = logging.getLogger(__name__)


STATEMENT_TIMEOUT = "30s"
PG_QUERY_CANCELED = "57014"  # SQLSTATE raised when statement_timeout cancels a query


@opp_admin_access_required
@opportunity_required
@waffle_flag(MICROPLANNING)
def coverage_progress(request, *args, **kwargs):
    """Coverage Progress Tracker page: a header saturation goal plus the per-ward "Core Metrics"
    table and the per-work-area-group "Metrics by Work Area Group" table. Each table has its own
    download button, handled via the ``export``/``table`` query params (see ``_export_coverage_table``).
    """
    opportunity = request.opportunity
    filterset = CoverageProgressFilterSet(request.GET, queryset=WorkArea.objects.none())
    date_filter = filterset.to_date_filter()
    try:
        with connection.cursor() as cursor:
            cursor.execute("SET LOCAL statement_timeout = %s", [STATEMENT_TIMEOUT])
        report = CoverageProgressReport(opportunity, date_filter)
        header = report.header()
        ward_table = CoverageWardTable(report.ward_rows())
        wag_table = CoverageWAGTable(report.wag_rows())
    except OperationalError as exc:
        # Only a statement_timeout (QueryCanceled) is an expected degradation; re-raise anything else
        # (e.g. a real connection error) so it isn't masked as a timeout.
        if getattr(exc.__cause__, "pgcode", None) != PG_QUERY_CANCELED:
            raise
        # The timeout aborted the txn; roll back (else ATOMIC_REQUESTS' COMMIT fails) and return a
        # query-free 503 — rendering base.html here would re-hit the DB via its context processors.
        transaction.set_rollback(True)
        logger.exception("Coverage progress query timed out for opportunity %s", opportunity.id)
        return HttpResponse(
            _("Report timed out. Please reach out to support if the error persists."),
            status=503,
            content_type="text/plain",
        )

    tables = {"ward": ward_table, "wag": wag_table}
    export_response = _export_coverage_table(request, opportunity, tables)
    if export_response is not None:
        return export_response

    # Pre-build the per-table download links so each carries the active filter (a download then
    # matches the on-screen filtered view rather than silently exporting the overall report). The
    # export param names come from the same constants the export view reads, so they can't drift.
    export_hrefs = {
        table_key: {
            fmt: "?"
            + filterset.export_querystring({COVERAGE_EXPORT_FORMAT_PARAM: fmt, COVERAGE_EXPORT_TABLE_PARAM: table_key})
            for fmt in ("csv", "xlsx")
        }
        for table_key in tables
    }

    context = {
        "opportunity": opportunity,
        "header": header,
        "ward_table": ward_table,
        "wag_table": wag_table,
        "filter_form": filterset.form,
        "export_hrefs": export_hrefs,
        "quoted_missing_deliver_units": quoted_missing_deliver_units(opportunity),
        "path": [
            {"title": _("Opportunities"), "url": reverse("opportunity:list", kwargs={"org_slug": request.org.slug})},
            {
                "title": opportunity.name,
                "url": reverse("opportunity:detail", args=(request.org.slug, opportunity.opportunity_id)),
            },
            {
                "title": _("Progress Map"),
                "url": reverse(
                    "microplanning:microplanning_home", args=(request.org.slug, opportunity.opportunity_id)
                ),
            },
            {"title": _("Progress Tracker")},
        ],
    }
    return render(request, "microplanning/coverage_progress.html", context)


# Query params used by the per-table download buttons: ``?export=<format>&table=<ward|wag>``.
COVERAGE_EXPORT_FORMAT_PARAM = "export"
COVERAGE_EXPORT_TABLE_PARAM = "table"
DEFAULT_COVERAGE_EXPORT_TABLE = "ward"
# Maps each ``table`` value to the file-name stem used in the download.
COVERAGE_EXPORT_FILENAME_STEMS = {"ward": "core_metrics", "wag": "metrics_by_work_area_group"}


def _export_coverage_table(request, opportunity, tables):
    """Return a file response for the requested table/format, or None if no export was requested.

    ``tables`` maps a ``table`` value (e.g. "ward"/"wag") to its built table. An unsupported
    ``export`` format or an unknown ``table`` value returns a 400 rather than silently serving
    the wrong table.
    """
    export_format = request.GET.get(COVERAGE_EXPORT_FORMAT_PARAM)
    if not export_format:
        return None
    if not TableExport.is_valid_format(export_format):
        return HttpResponseBadRequest(_("Unsupported export format."))

    export_table = request.GET.get(COVERAGE_EXPORT_TABLE_PARAM, DEFAULT_COVERAGE_EXPORT_TABLE)
    if export_table not in tables:
        return HttpResponseBadRequest(_("Unknown table."))

    exporter = TableExport(export_format, tables[export_table])
    return exporter.response(
        f"{slugify(opportunity.name)}_{COVERAGE_EXPORT_FILENAME_STEMS[export_table]}.{export_format}"
    )
