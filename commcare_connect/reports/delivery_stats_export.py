"""Delivery Stats CSV export and the recipient list for the monthly KPI email.

Recipients are the users on the ``delivery_stats_monthly_export`` flag. Only users who can see the
Delivery Stats report themselves receive it.
"""

import logging
from datetime import date

from dateutil.relativedelta import relativedelta
from django_tables2.export.export import TableExport

from commcare_connect.flags.flag_names import DELIVERY_STATS_MONTHLY_EXPORT
from commcare_connect.flags.models import Flag
from commcare_connect.opportunity.models import CompletedWork
from commcare_connect.reports.helpers import get_table_data_for_year_month
from commcare_connect.reports.tables import AdminReportTable
from commcare_connect.users.models import User
from commcare_connect.utils.permission_const import KPI_REPORT_ACCESS

logger = logging.getLogger(__name__)

FLAG_NOTE = "Recipients of the monthly Delivery Stats CSV email. Long-lived; not a release toggle."


def previous_month_filters(today: date) -> dict:
    # from_date == to_date because the report treats both as months; a later to_date adds a row.
    month = today.replace(day=1) - relativedelta(months=1)
    return {"from_date": month, "to_date": month, "period": "monthly"}


def get_filter_values(data: dict) -> dict:
    # Imported here: views imports the tasks that import this module.
    from commcare_connect.reports.views import DeliveryReportFilters

    # The filterset filters nothing (see NonModelFilterView); it only cleans the values.
    filterset = DeliveryReportFilters(data, queryset=CompletedWork.objects.none())
    if not filterset.form.is_valid():
        return {}
    return filterset.form.cleaned_data


def build_delivery_stats_csv(filters: dict) -> str:
    rows = get_table_data_for_year_month(**filters)
    table = AdminReportTable(rows, period=filters.get("period") or "monthly")
    return TableExport("csv", table).export()


def describe_filters(filters: dict) -> list[str]:
    lines = []
    for name, value in filters.items():
        if not value:
            continue
        if isinstance(value, date):
            value = value.strftime("%B %Y")
        elif name == "period":
            value = value.title()
        lines.append(f"{name.replace('_', ' ').title()}: {value}")
    return lines


def is_eligible_recipient(user: User) -> bool:
    return bool(user.is_active and user.email and user.has_perm(KPI_REPORT_ACCESS))


def get_recipient_flag(create: bool = False) -> Flag | None:
    if create:
        flag, _ = Flag.objects.get_or_create(
            name=DELIVERY_STATS_MONTHLY_EXPORT, defaults={"note": FLAG_NOTE, "everyone": None}
        )
        return flag
    return Flag.objects.filter(name=DELIVERY_STATS_MONTHLY_EXPORT).first()


def get_recipients() -> list[User]:
    flag = get_recipient_flag()
    if not flag:
        return []
    return list(flag.users.order_by("email"))


def get_eligible_recipients() -> list[User]:
    eligible = []
    for user in get_recipients():
        if is_eligible_recipient(user):
            eligible.append(user)
        else:
            logger.info("Skipping Delivery Stats export recipient %s: inactive, no email or no report access", user)
    return eligible


def add_recipient(user: User) -> None:
    get_recipient_flag(create=True).users.add(user)


def remove_recipient(user: User) -> None:
    flag = get_recipient_flag()
    if flag:
        flag.users.remove(user)
