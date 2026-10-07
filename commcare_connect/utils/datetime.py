import calendar
import datetime

from dateutil.relativedelta import relativedelta
from django.db.models import TextChoices
from django.utils.timezone import localdate, now
from django.utils.translation import gettext_lazy as _


def is_date_before(date: datetime.datetime, days: int):
    before_date = now() - datetime.timedelta(days=days)
    return date.date() == before_date.date()


def get_month_start_date(value: datetime.date | datetime.datetime) -> datetime.date:
    if isinstance(value, datetime.datetime):
        value = localdate(value)
    return value.replace(day=1)


def parse_year_month(value: str | None) -> datetime.date | None:
    """Parse a `YYYY-MM` string into the first day of that month, or None if absent or malformed."""
    if not value:
        return None
    try:
        return datetime.datetime.strptime(value, "%Y-%m").date()
    except ValueError:
        return None


def get_month_series(from_date: datetime.date, to_date: datetime.date):
    series = [from_date]
    current_date = from_date
    while current_date < to_date:
        current_date += relativedelta(months=1)
        series.append(current_date)
    return series


def get_start_end_date_range_with_time(
    from_date: datetime.date, to_date: datetime.date
) -> tuple[datetime.datetime, datetime.datetime]:
    """Return (start_datetime, end_datetime) spanning the full days of from_date and to_date in UTC."""
    start_time = datetime.datetime.combine(from_date, datetime.time.min, tzinfo=datetime.UTC)
    end_time = datetime.datetime.combine(to_date, datetime.time.max, tzinfo=datetime.UTC)
    return start_time, end_time


def get_start_end_dates_from_month_range(from_date: datetime.date, to_date: datetime.date):
    start_date = datetime.date(from_date.year, from_date.month, 1)
    start_time = datetime.datetime.combine(start_date, datetime.time.min, tzinfo=datetime.UTC)
    end_date = datetime.date(to_date.year, to_date.month, calendar.monthrange(to_date.year, to_date.month)[1])
    end_time = datetime.datetime.combine(end_date, datetime.time.max, tzinfo=datetime.UTC)
    return start_time, end_time


def get_quarter_series(from_date: datetime.date, to_date: datetime.date):
    """Return list of first-day-of-quarter dates covering from_date to to_date.

    from_date is snapped back to the start of its containing quarter, so passing
    a mid-quarter date (e.g. 2025-02-15) will include the full quarter (2025-01-01).
    """
    q_start_month = ((from_date.month - 1) // 3) * 3 + 1
    current = datetime.date(from_date.year, q_start_month, 1)
    series = []
    while current <= to_date:
        series.append(current)
        current += relativedelta(months=3)
    return series


def get_end_date_previous_month():
    return datetime.date.today().replace(day=1) - datetime.timedelta(days=1)


class DateRanges(TextChoices):
    """Date ranges relative to today. Each place that offers them picks the subset it needs."""

    LAST_7_DAYS = "last_7_days", _("Last 7 days")
    LAST_30_DAYS = "last_30_days", _("Last 30 days")
    LAST_90_DAYS = "last_90_days", _("Last 90 days")
    LAST_3_MONTHS = "last_3_months", _("Last 3 months")
    LAST_6_MONTHS = "last_6_months", _("Last 6 months")
    LAST_12_MONTHS = "last_12_months", _("Last 12 months")
    LAST_YEAR = "last_year", _("Last year")
    NEXT_30_DAYS = "next_30_days", _("Next 30 days")
    NEXT_3_MONTHS = "next_3_months", _("Next 3 months")
    NEXT_6_MONTHS = "next_6_months", _("Next 6 months")
    ALL = "all", _("All")
    CUSTOM = "custom", _("Custom range")

    def bounds(self, today: datetime.date) -> tuple[datetime.date, datetime.date]:
        """The inclusive (from, to) dates of a relative range, counted back or forward from today.

        ALL and CUSTOM have no dates of their own, so they have no bounds.
        """
        edge = today + _DATE_RANGE_OFFSETS[self]
        return min(today, edge), max(today, edge)


_DATE_RANGE_OFFSETS = {
    DateRanges.LAST_7_DAYS: relativedelta(days=-7),
    DateRanges.LAST_30_DAYS: relativedelta(days=-30),
    DateRanges.LAST_90_DAYS: relativedelta(days=-90),
    DateRanges.LAST_3_MONTHS: relativedelta(months=-3),
    DateRanges.LAST_6_MONTHS: relativedelta(months=-6),
    DateRanges.LAST_12_MONTHS: relativedelta(months=-12),
    DateRanges.LAST_YEAR: relativedelta(years=-1),
    DateRanges.NEXT_30_DAYS: relativedelta(days=30),
    DateRanges.NEXT_3_MONTHS: relativedelta(months=3),
    DateRanges.NEXT_6_MONTHS: relativedelta(months=6),
}
