import calendar
import datetime

from dateutil.relativedelta import relativedelta
from django.utils.timezone import now


def is_date_before(date: datetime.datetime, days: int):
    before_date = now() - datetime.timedelta(days=days)
    return date.date() == before_date.date()


def get_month_series(from_date: datetime.date, to_date: datetime.date):
    series = [from_date]
    current_date = from_date
    while current_date < to_date:
        current_date += relativedelta(months=1)
        series.append(current_date)
    return series


def get_start_end_dates_from_month_range(from_date: datetime.date, to_date: datetime.date):
    start_date = datetime.date(from_date.year, from_date.month, 1)
    start_time = datetime.datetime.combine(start_date, datetime.time.min, tzinfo=datetime.UTC)
    end_date = datetime.date(to_date.year, to_date.month, calendar.monthrange(to_date.year, to_date.month)[1])
    end_time = datetime.datetime.combine(end_date, datetime.time.max, tzinfo=datetime.UTC)
    return start_time, end_time
