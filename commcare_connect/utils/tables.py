import itertools
from datetime import timedelta

import django_tables2 as tables
from django.utils.html import escape
from django.utils.timezone import is_aware, localtime # For timezone handling
import datetime


STOP_CLICK_PROPAGATION_ATTR = {"td": {"@click.stop": ""}}
TEXT_CENTER_ATTR = {"td": {"class": "text-center"}}

DATE_TIME_FORMAT  ="%d-%b-%Y %H:%M"
DATE_FORMAT = "%d-%b-%Y"

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
