from collections import defaultdict
from datetime import datetime

from dateutil.relativedelta import relativedelta
from django.contrib.postgres.aggregates import ArrayAgg
from django.db import models
from django.db.models.functions import Coalesce, ExtractDay, TruncMonth, TruncQuarter
from django.utils.timezone import now

from commcare_connect.connect_id_client import fetch_user_counts
from commcare_connect.opportunity.models import (
    CompletedWork,
    CompletedWorkStatus,
    DeliveryType,
    Payment,
    UserVisit,
    VisitValidationStatus,
)
from commcare_connect.reports.models import UserAnalyticsData
from commcare_connect.utils.datetime import get_month_series, get_quarter_series, get_start_end_dates_from_month_range

ADMIN_REPORT_START = "2023-01"


def _get_cumulative_count(count_data: dict[str, int]):
    data = defaultdict(int)
    from_date = datetime.strptime(ADMIN_REPORT_START, "%Y-%m").date()
    to_date = datetime.today().date()
    timeseries = get_month_series(from_date, to_date)
    total_count = 0
    for month in timeseries:
        key = month.strftime("%Y-%m")
        total_count += count_data.get(key, 0)
        data[key] = total_count
    return data


def get_connectid_user_counts_cumulative():
    user_counts = fetch_user_counts()
    total_user_counts = user_counts.get("total_users", {})
    return _get_cumulative_count(total_user_counts)


def get_activated_connect_user_counts_cumulative(delivery_type=None):
    qs = CompletedWork.objects.filter(
        status=CompletedWorkStatus.approved,
        saved_approved_count__gt=0,
        opportunity_access__opportunity__is_test=False,
    )

    if delivery_type:
        qs = qs.filter(opportunity_access__opportunity__delivery_type__slug=delivery_type)

    visit_data = (
        qs.annotate(month_group=TruncMonth(Coalesce("status_modified_date", "date_created")))
        .values("month_group")
        .annotate(users=ArrayAgg("opportunity_access__user_id", distinct=True))
        .order_by("month_group")
    )

    seen_users = set()
    visit_data_dict = {}
    for item in visit_data:
        month_group = item["month_group"].strftime("%Y-%m")
        users = set(item["users"]) - seen_users
        visit_data_dict[month_group] = len(users)
        seen_users.update(users)
    return _get_cumulative_count(visit_data_dict)


def _quarter_number(month: int) -> int:
    return (month - 1) // 3 + 1


def _get_trunc_func(period):
    return TruncQuarter if period == "quarterly" else TruncMonth


def _period_key(dt, period):
    """Format a date into the period key string used for dict lookups."""
    if period == "quarterly":
        return f"{dt.year}-Q{_quarter_number(dt.month)}"
    return dt.strftime("%Y-%m")


def _get_timeseries(from_date, to_date, period):
    if period == "quarterly":
        return get_quarter_series(from_date, to_date)
    return get_month_series(from_date, to_date)


def _cumulative_lookup_key(quarter_start_date, cumulative_data):
    """For a quarter start date, find the latest month in that quarter with cumulative data."""
    for offset in [2, 1, 0]:
        month_key = (quarter_start_date + relativedelta(months=offset)).strftime("%Y-%m")
        if month_key in cumulative_data:
            return month_key
    return quarter_start_date.strftime("%Y-%m")


def get_table_data_for_year_month(
    from_date=None,
    to_date=None,
    delivery_type=None,
    program=None,
    llo=None,
    opportunity=None,
    country=None,
    period=None,
):
    """Return KPI report rows aggregated by month or quarter.

    Args:
        period: "monthly" (default) or "quarterly". In quarterly mode all DB queries
                use TruncQuarter so averages are true weighted averages, not
                average-of-monthly-averages. The date range is automatically expanded
                to cover complete quarters so labels are never misleading.
        to_date: Clamped to today if in the future.
    """
    from_date = from_date or now().date()
    to_date = to_date if to_date and to_date <= now().date() else now().date()
    period = period or "monthly"
    is_quarterly = period == "quarterly"
    timeseries = _get_timeseries(from_date, to_date, period)
    trunc_func = _get_trunc_func(period)
    if is_quarterly:
        # Expand the DB range to cover complete quarters so that labels are not misleading.
        q_from_month = (_quarter_number(from_date.month) - 1) * 3 + 1
        q_to_month = _quarter_number(to_date.month) * 3
        start_date, end_date = get_start_end_dates_from_month_range(
            from_date.replace(month=q_from_month, day=1),
            to_date.replace(month=q_to_month, day=1),
        )
    else:
        start_date, end_date = get_start_end_dates_from_month_range(from_date, to_date)

    delivery_type_name = "All"
    if delivery_type:
        d_type = DeliveryType.objects.filter(slug=delivery_type).first()
        delivery_type_name = d_type.name if d_type else "All"

    visit_data_dict = defaultdict(
        lambda: {
            "month_group": from_date,
            "delivery_type_name": delivery_type_name,
            "connectid_users": 0,
            "users": 0,
            "services": 0,
            "flw_amount_earned": 0,
            "intervention_funding_deployed": 0,
            "avg_time_to_payment": 0,
            "max_time_to_payment": 0,
            "organization_funding_deployed": 0,
            "flw_amount_paid": 0,
            "avg_top_earned_flws": 0,
        }
    )
    for date in timeseries:
        key = _period_key(date, period), delivery_type_name
        row_data = {"month_group": date, "delivery_type_name": delivery_type_name}
        if is_quarterly:
            row_data["quarter_label"] = f"Q{_quarter_number(date.month)} {date.year}"
        visit_data_dict[key].update(row_data)

    filter_kwargs = {"opportunity_access__opportunity__is_test": False}
    filter_kwargs_nm = {"invoice__opportunity__is_test": False}
    if delivery_type:
        filter_kwargs.update({"opportunity_access__opportunity__delivery_type__slug": delivery_type})
        filter_kwargs_nm.update({"invoice__opportunity__delivery_type__slug": delivery_type})
    if program:
        filter_kwargs.update({"opportunity_access__opportunity__managedopportunity__program": program})
        filter_kwargs_nm.update({"invoice__opportunity__managedopportunity__program": program})
    if llo:
        filter_kwargs.update({"opportunity_access__opportunity__organization__llo_entity": llo})
        filter_kwargs_nm.update({"invoice__opportunity__organization__llo_entity": llo})
    if opportunity:
        filter_kwargs.update({"opportunity_access__opportunity": opportunity})
        filter_kwargs_nm.update({"invoice__opportunity": opportunity})
    if country:
        filter_kwargs.update({"opportunity_access__opportunity__country": country})
        filter_kwargs_nm.update({"invoice__opportunity__country": country})

    max_visit_date = (
        UserVisit.objects.filter(completed_work_id=models.OuterRef("id"), status=VisitValidationStatus.approved)
        .values_list("date_created")
        .order_by("-date_created")[:1]
    )
    time_to_payment = models.ExpressionWrapper(
        models.F("payment_date") - models.Subquery(max_visit_date), output_field=models.DurationField()
    )
    base_visit_data_qs = CompletedWork.objects.filter(
        **filter_kwargs, status=CompletedWorkStatus.approved, saved_approved_count__gt=0
    )
    visit_data = (
        base_visit_data_qs.annotate(filter_date=Coalesce("status_modified_date", "date_created"))
        .filter(filter_date__range=(start_date, end_date))
        .annotate(month_group=trunc_func("filter_date"))
        .values("month_group")
        .annotate(
            users=models.Count("opportunity_access__user_id", distinct=True),
            services=models.Sum("saved_approved_count", default=0),
            flw_amount_earned=models.Sum("saved_payment_accrued_usd", default=0),
            intervention_funding_deployed=models.Sum(
                models.F("saved_org_payment_accrued_usd") + models.F("saved_payment_accrued_usd"), default=0
            ),
        )
        .order_by("month_group")
    )
    for item in visit_data:
        group_key = _period_key(item["month_group"], period), item.get("delivery_type_name", delivery_type_name)
        visit_data_dict[group_key].update(item)

    visit_time_to_payment_data = (
        base_visit_data_qs.filter(
            payment_date__range=(start_date, end_date),
            saved_payment_accrued_usd__gt=0,
        )
        .annotate(month_group=trunc_func("payment_date"))
        .values("month_group")
        .annotate(
            avg_time_to_payment=models.Avg(ExtractDay(time_to_payment), default=0),
            max_time_to_payment=models.Max(ExtractDay(time_to_payment), default=0),
        )
        .order_by("month_group")
    )
    for item in visit_time_to_payment_data:
        group_key = _period_key(item["month_group"], period), item.get("delivery_type_name", delivery_type_name)
        visit_data_dict[group_key].update(item)

    payment_query = Payment.objects.filter(created_at__range=(start_date, end_date))
    flw_amount_paid_data = (
        payment_query.filter(**filter_kwargs)
        .annotate(month_group=trunc_func("created_at"))
        .values("month_group")
        .annotate(flw_amount_paid=models.Sum("amount_usd", default=0))
        .order_by("month_group")
    )
    for item in flw_amount_paid_data:
        group_key = _period_key(item["month_group"], period), item.get("delivery_type_name", delivery_type_name)
        visit_data_dict[group_key].update(item)

    nm_amount_paid_data = (
        payment_query.filter(**filter_kwargs_nm)
        .annotate(month_group=trunc_func("created_at"))
        .values("month_group")
        .annotate(
            organization_funding_deployed=models.Sum(
                "amount_usd", default=0, filter=models.Q(invoice__service_delivery=False)
            ),
        )
        .order_by("month_group")
    )
    for item in nm_amount_paid_data:
        group_key = _period_key(item["month_group"], period), item.get("delivery_type_name", delivery_type_name)
        visit_data_dict[group_key].update(item)

    _calculate_avg_top_earned_flws(
        base_visit_data_qs, start_date, end_date, visit_data_dict, delivery_type_name, trunc_func, period
    )

    connectid_user_count = get_connectid_user_counts_cumulative()
    total_activated_connect_user_counts = get_activated_connect_user_counts_cumulative(delivery_type)

    hq_sso_users = (
        UserAnalyticsData.objects.filter(has_sso_on_hq_app__isnull=False)
        .annotate(month_group=TruncMonth("has_sso_on_hq_app"))
        .values("month_group")
        .annotate(users=models.Count("username"))
        .order_by("month_group")
    )
    hq_sso_user_months = {item["month_group"].strftime("%Y-%m"): item["users"] for item in hq_sso_users}
    hq_sso_users_data = _get_cumulative_count(hq_sso_user_months)

    for group_key in visit_data_dict.keys():
        row_date = visit_data_dict[group_key]["month_group"]
        if is_quarterly:
            cid_key = _cumulative_lookup_key(row_date, connectid_user_count)
            act_key = _cumulative_lookup_key(row_date, total_activated_connect_user_counts)
            sso_key = _cumulative_lookup_key(row_date, hq_sso_users_data)
        else:
            cid_key = act_key = sso_key = group_key[0]
        connectid_users = connectid_user_count.get(cid_key, 0)
        activated_connect_users = total_activated_connect_user_counts.get(act_key, 0)
        activated_commcare_users = hq_sso_users_data.get(sso_key, 0)
        visit_data_dict[group_key].update(
            {
                "connectid_users": connectid_users,
                "activated_connect_users": activated_connect_users,
                "activated_commcare_users": activated_commcare_users,
                "activated_personalid_accounts": activated_commcare_users + activated_connect_users,
            }
        )
    return list(visit_data_dict.values())


def _calculate_avg_top_earned_flws(
    base_visit_data_qs,
    start_date,
    end_date,
    visit_data_dict,
    delivery_type_name,
    trunc_func=TruncMonth,
    period="monthly",
):
    avg_top_flw_amount_earned = (
        base_visit_data_qs.annotate(filter_date=Coalesce("status_modified_date", "date_created"))
        .filter(filter_date__range=(start_date, end_date))
        .annotate(month_group=trunc_func("filter_date"))
        .values("month_group", "opportunity_access__user_id")
        .annotate(earned_sum=models.Sum("saved_payment_accrued_usd", default=0))
        .order_by("month_group")
    )
    delivery_type_grouped_users = defaultdict(set)
    for item in avg_top_flw_amount_earned:
        group_key = _period_key(item["month_group"], period), item.get("delivery_type_name", delivery_type_name)
        delivery_type_grouped_users[group_key].add((item["opportunity_access__user_id"], item["earned_sum"]))
    for group_key, users in delivery_type_grouped_users.items():
        sum_total_users = defaultdict(int)
        for user, amount in users:
            sum_total_users[user] += amount
        # Take at least 1 top user in cases where this variable is 0
        top_five_percent_flw_count = len(sum_total_users) // 20 or 1
        avg_top_earned_flws = (
            sum(sorted(sum_total_users.values(), reverse=True)[:top_five_percent_flw_count])
            // top_five_percent_flw_count
        )
        visit_data_dict[group_key].update({"avg_top_earned_flws": avg_top_earned_flws})
