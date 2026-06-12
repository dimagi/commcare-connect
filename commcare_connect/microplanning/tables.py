from django.utils.translation import gettext_lazy as _
from django_tables2 import columns, tables

# Columns for the two Coverage Progress Tracker tables. Each entry maps a key in the row dicts
# emitted by ``CoverageProgressReport`` (see coverage_progress.py) to its human-readable header.
# Order here is the column order in the rendered table and in exports.

WARD_LABEL_COLUMNS = (("ward", _("Ward")),)

WARD_METRIC_COLUMNS = (
    ("target_population", _("Ward Population Target")),
    ("building_count", _("Building Count")),
    ("num_work_areas", _("Work Areas")),
    ("visits_approved", _("Approved Visits")),
    ("pct_visits_approved", _("% Visits Completed")),
    ("WAs_visited", _("WAs Visited")),
    ("pct_WAs_visited", _("% WAs Visited")),
    ("WAs_visited_last_week", _("WAs Visited (Last Week)")),
    ("pct_WAs_visited_last_week", _("% WAs Visited (Last Week)")),
    ("WAs_evc_reached", _("WAs EVC Reached")),
    ("pct_WAs_evc_reached", _("% WAs EVC Reached")),
    ("WAs_evc_reached_last_week", _("WAs EVC Reached (Last Week)")),
    ("pct_WAs_evc_reached_last_week", _("% WAs EVC Reached (Last Week)")),
    ("Buildings_covered_in_WAs_visited", _("Buildings Covered (WAs Visited)")),
    ("pct_Buildings_covered_in_WAs_visited", _("% Buildings Covered (WAs Visited)")),
    ("Buildings_covered_in_WAs_visited_last_week", _("Buildings Covered (WAs Visited, Last Week)")),
    ("pct_buildings_covered_in_WAs_visited_last_week", _("% Buildings Covered (WAs Visited, Last Week)")),
    ("Buildings_covered_in_WAs_evc_reached", _("Buildings Covered (WAs EVC Reached)")),
    ("pct_Buildings_covered_in_WAs_evc_reached", _("% Buildings Covered (WAs EVC Reached)")),
    ("Buildings_covered_in_WAs_evc_reached_last_week", _("Buildings Covered (WAs EVC Reached, Last Week)")),
    ("pct_buildings_covered_in_WAs_evc_reached_last_week", _("% Buildings Covered (WAs EVC Reached, Last Week)")),
    ("pct_WA_visited_to_pct_visits", _("WA Visited : Visits Ratio")),
    ("pct_WA_visited_to_pct_visits_last_week", _("WA Visited : Visits Ratio (Last Week)")),
    ("pct_WA_evc_reached_to_pct_visit", _("WA EVC Reached : Visits Ratio")),
    ("pct_WA_evc_reached_to_pct_visits_last_week", _("WA EVC Reached : Visits Ratio (Last Week)")),
    ("pct_buildings_covered_in_WA_evc_reached_to_pct_visit", _("Buildings EVC Reached : Visits Ratio")),
    (
        "pct_buildings_covered_in_WA_evc_reached_to_pct_visits_last_week",
        _("Buildings EVC Reached : Visits Ratio (Last Week)"),
    ),
    ("pct_buildings_covered_in_WA_visited_to_pct_visit", _("Buildings Visited : Visits Ratio")),
    ("pct_buildings_covered_in_WA_visited_to_pct_visits_last_week", _("Buildings Visited : Visits Ratio (Last Week)")),
)

WAG_LABEL_COLUMNS = (
    ("work_area_group", _("Work Area Group")),
    ("ward", _("Ward")),
)

WAG_METRIC_COLUMNS = (
    ("target_population", _("Population Target")),
    ("pct_visits_approved", _("% Visits Completed")),
    ("pct_visits_approved_last_week", _("% Visits Completed (Last Week)")),
    ("pct_WAs_evc_reached", _("% WAs EVC Reached")),
    ("pct_WAs_evc_reached_last_week", _("% WAs EVC Reached (Last Week)")),
    ("pct_WA_visited_to_pct_visits", _("WA Visited : Visits Ratio")),
    ("pct_WA_visited_to_pct_visits_last_week", _("WA Visited : Visits Ratio (Last Week)")),
)


class NumberColumn(columns.Column):
    """Formats numbers with thousands separators (and 2 decimals for floats) for display, renders
    ``None`` as an em-dash, and exports the raw, unformatted value so spreadsheets get real numbers.
    """

    def __init__(self, *args, **kwargs):
        kwargs.setdefault("orderable", False)
        kwargs.setdefault("empty_values", ())  # render None ourselves instead of the default empty value
        super().__init__(*args, **kwargs)

    def render(self, value):
        if value is None:
            return "—"
        if isinstance(value, float):
            return f"{value:,.2f}"
        return f"{value:,}"

    def value(self, value):
        # Raw value for exports — no separators so CSV/Excel parse it as a number.
        return value


class TextColumn(columns.Column):
    def __init__(self, *args, **kwargs):
        kwargs.setdefault("orderable", False)
        super().__init__(*args, **kwargs)


def _build_columns(label_columns, metric_columns):
    text_cols = [(key, TextColumn(verbose_name=header, accessor=key)) for key, header in label_columns]
    number_cols = [(key, NumberColumn(verbose_name=header, accessor=key)) for key, header in metric_columns]
    return text_cols + number_cols


class CoverageWardTable(tables.Table):
    """Top "Core Metrics" table — one row per ward (``CoverageProgressReport.ward_rows()``)."""

    class Meta:
        orderable = False
        empty_text = _("No coverage data available.")

    def __init__(self, data, **kwargs):
        super().__init__(
            data,
            extra_columns=_build_columns(WARD_LABEL_COLUMNS, WARD_METRIC_COLUMNS),
            **kwargs,
        )


class CoverageWAGTable(tables.Table):
    """Bottom "Metrics by Work Area Group" table — one row per work area group
    (``CoverageProgressReport.wag_rows()``)."""

    class Meta:
        orderable = False
        empty_text = _("No work area group data available.")

    def __init__(self, data, **kwargs):
        super().__init__(
            data,
            extra_columns=_build_columns(WAG_LABEL_COLUMNS, WAG_METRIC_COLUMNS),
            **kwargs,
        )
