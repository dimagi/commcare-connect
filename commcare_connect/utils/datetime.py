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
