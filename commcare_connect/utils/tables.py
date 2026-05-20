import datetime
import itertools
from datetime import timedelta
from functools import cached_property

import django_tables2 as tables
from django.utils.timezone import is_aware, localtime  # For timezone handling
from django_tables2.data import TableQuerysetData
from django_tables2.rows import BoundRow

STOP_CLICK_PROPAGATION_ATTR = {"td": {"@click.stop": ""}}
TEXT_CENTER_ATTR = {"td": {"class": "text-center"}}

DATE_TIME_FORMAT = "%d-%b-%Y %H:%M"
DATE_FORMAT = "%d-%b-%Y"

DEFAULT_PAGE_SIZE = 20
PAGE_SIZE_OPTIONS = [20, 30, 50, 100]


def merge_attrs(*dicts):
    merged = {}
    for d in dicts:
        for key, val in d.items():
            merged.setdefault(key, {}).update(val)
    return merged


class OrgContextTable(tables.Table):
    def __init__(self, *args, **kwargs):
        self.org_slug = kwargs.pop("org_slug", None)
        super().__init__(*args, **kwargs)


class IndexColumn(tables.Column):
    def __init__(self, *args, **kwargs):
        kwargs.setdefault("verbose_name", "#")
        kwargs.setdefault("orderable", False)
        kwargs.setdefault("empty_values", ())
        super().__init__(*args, **kwargs)

    def render(self, value, record, bound_column, bound_row, **kwargs):
        table = bound_row._table
        page = getattr(table, "page", None)
        if page:
            start_index = (page.number - 1) * page.paginator.per_page + 1
        else:
            start_index = 1
        if not hasattr(table, "_row_counter") or getattr(table, "_row_counter_start", None) != start_index:
            table._row_counter = itertools.count(start=start_index)
            table._row_counter_start = start_index
        value = next(table._row_counter)
        return value


def get_duration_min(total_seconds):
    total_seconds = int(total_seconds)
    minutes = (total_seconds // 60) % 60
    hours = (total_seconds // 3600) % 24
    days = total_seconds // 86400

    parts = []
    if days:
        parts.append(f"{days} day{'s' if days != 1 else ''}")
    elif hours:
        parts.append(f"{hours} hr")
    elif minutes or not parts:
        parts.append(f"{minutes} min")

    return " ".join(parts)


class DurationColumn(tables.Column):
    def render(self, value):
        total_seconds = int(value.total_seconds() if isinstance(value, timedelta) else 0)
        return get_duration_min(total_seconds)


class DMYTColumn(tables.Column):
    """
    A custom django-tables2 column that formats datetime objects.
    If the time component is present, it displays both date and time.
    Otherwise, it displays only the date.
    Handles None values by returning a default string.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

    def render(self, value, record=None, bound_column=None):
        if value is None:
            return "—"

        final_value = str(value)  # original_value_for_fallback

        # Handle datetime.datetime objects
        if isinstance(value, datetime.datetime):
            # If the datetime object is timezone-aware, convert it to local time
            # This ensures consistent display regardless of the original timezone
            if is_aware(value):
                value = localtime(value)
                final_value = value.strftime(DATE_TIME_FORMAT)

        # Handle datetime.date objects (which have no time component)
        elif isinstance(value, datetime.date):
            final_value = value.strftime(DATE_FORMAT)

        return final_value


def get_validated_page_size(request):
    try:
        page_size = int(request.GET.get("page_size", DEFAULT_PAGE_SIZE))
        return page_size if page_size in PAGE_SIZE_OPTIONS else DEFAULT_PAGE_SIZE
    except (ValueError, TypeError):
        return DEFAULT_PAGE_SIZE


class GroupedTableData(TableQuerysetData):
    """Data source for `GroupedTable`. Paginates by distinct `pk` count, then
    groups rows for each page into one head row per pk with a `sub_rows` list.
    """

    @cached_property
    def _pks(self):
        return list(dict.fromkeys(self.data.values_list("pk", flat=True)))

    def order_by(self, aliases):
        self.__dict__.pop("_pks", None)
        super().order_by(aliases)

    def __len__(self):
        return len(self._pks)

    def __iter__(self):
        return iter(self._build_page(self._pks))

    def __getitem__(self, key):
        if isinstance(key, int):
            key = slice(key, key + 1)
        return self._build_page(self._pks[key])

    def _build_page(self, pks):
        table = self.table
        qs = self.data.filter(pk__in=pks)
        if table.group_item_order_by:
            qs = qs.order_by(*table.group_item_order_by)
        groups: dict = {}
        for row in qs:
            if row.pk not in groups:
                row.sub_rows = []
                groups[row.pk] = row
            groups[row.pk].sub_rows.append(table.SubBoundRow(record=row, table=table))
        return [groups[pk] for pk in pks if pk in groups]


class GroupedTable(tables.Table):
    """A table where rows are grouped by `pk` into collapsible sections.
    Each group renders as one clickable header row with sub-rows hidden beneath
    it. Pagination counts groups, not individual rows — so a page size of 20
    shows 20 headers, regardless of how many sub-rows they expand to.

    Subclasses define:
      - columns: as usual for django-tables2.
      - `header_columns`: names of columns shown once in the group header row;
        sub-rows leave these blank.
      - `group_item_order_by`: ordering applied to rows within each group.
      - `Meta`: standard django-tables2 Meta (template_name, etc.).

    Pass a raw queryset — it is auto-wrapped into `GroupedTableData`.
    """

    group_item_order_by = ()
    header_columns = ()
    group_item_label = ()  # (singular, plural) — already translated; shown next to the count

    class SubBoundRow(BoundRow):
        def get_cell(self, name):
            if name in self.table.header_columns:
                return ""
            return super().get_cell(name)

    @property
    def non_header_column_count(self):
        return sum(1 for c in self.columns if c.name not in self.header_columns)

    def __init__(self, data, *args, **kwargs):
        if not isinstance(data, GroupedTableData):
            data = GroupedTableData(data)
        super().__init__(data, *args, **kwargs)


def select_column(th_extra=None, td_extra=None):
    """
    Return a CheckBoxColumn wired for Alpine.js table selection.

    Header checkbox binds to `selectAll` and triggers `toggleSelectAll()`.
    Row checkboxes bind to `selected` and use `record.pk` as value.

    Args:
        th_extra (dict, optional): Extra/override attrs for header checkbox.
        td_extra (dict, optional): Extra/override attrs for row checkboxes.
    """
    th_attrs = {
        "@click": "toggleSelectAll()",
        "x-model": "selectAll",
        "name": "select_all",
        "type": "checkbox",
        "class": "checkbox",
    }
    td_attrs = {
        "x-model": "selected",
        "@click.stop": "",
        "name": "row_select",
        "type": "checkbox",
        "class": "checkbox",
        "value": lambda record: record.pk,
        "id": lambda record: f"row_checkbox_{record.pk}",
    }
    if th_extra:
        th_attrs.update(th_extra)
    if td_extra:
        td_attrs.update(td_extra)
    return tables.CheckBoxColumn(
        accessor="pk",
        attrs={"th__input": th_attrs, "td__input": td_attrs},
    )
