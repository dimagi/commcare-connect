from collections import defaultdict
from datetime import datetime

from django.db import models
from django.db.models.functions import Coalesce, ExtractDay, TruncMonth
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
from commcare_connect.utils.datetime import get_month_series, get_start_end_dates_from_month_range

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
    connectid_user_count = fetch_user_counts()
    return _get_cumulative_count(connectid_user_count)


def get_eligible_user_counts_cumulative():
    visit_data = (
        CompletedWork.objects.filter(status=CompletedWorkStatus.approved, saved_approved_count__gt=0)
        .annotate(month_group=TruncMonth(Coalesce("status_modified_date", "date_created")))
        .values("month_group")
        .annotate(users=models.Count("opportunity_access__user_id", distinct=True))
        .order_by("month_group")
    )
    visit_data_dict = {item["month_group"].strftime("%Y-%m"): item["users"] for item in visit_data}
    return _get_cumulative_count(visit_data_dict)


def get_table_data_for_year_month(
    from_date=None,
    to_date=None,
    delivery_type=None,
    program=None,
    network_manager=None,
    opportunity=None,
    country_currency=None,
):
    from_date = from_date or now().date()
    to_date = to_date if to_date and to_date <= now().date() else now().date()
    timeseries = get_month_series(from_date, to_date)
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
            "nm_amount_earned": 0,
            "avg_time_to_payment": 0,
            "max_time_to_payment": 0,
            "nm_amount_paid": 0,
            "nm_other_amount_paid": 0,
            "flw_amount_paid": 0,
            "avg_top_paid_flws": 0,
        }
    )
    for date in timeseries:
        key = date.strftime("%Y-%m"), delivery_type_name
        visit_data_dict[key].update({"month_group": date, "delivery_type_name": delivery_type_name})

    filter_kwargs = {"opportunity_access__opportunity__is_test": False}
    filter_kwargs_nm = {"invoice__opportunity__is_test": False}
    if delivery_type:
        filter_kwargs.update({"opportunity_access__opportunity__delivery_type__slug": delivery_type})
        filter_kwargs_nm.update({"invoice__opportunity__delivery_type__slug": delivery_type})
    if program:
        filter_kwargs.update({"opportunity_access__opportunity__managedopportunity__program": program})
        filter_kwargs_nm.update({"invoice__opportunity__managedopportunity__program": program})
    if network_manager:
        filter_kwargs.update({"opportunity_access__opportunity__organization": network_manager})
        filter_kwargs_nm.update({"invoice__opportunity__organization": network_manager})
    if opportunity:
        filter_kwargs.update({"opportunity_access__opportunity": opportunity})
        filter_kwargs_nm.update({"invoice__opportunity": opportunity})
    if country_currency:
        filter_kwargs.update({"opportunity_access__opportunity__currency": country_currency})
        filter_kwargs_nm.update({"invoice__opportunity__currency": country_currency})

    max_visit_date = (
        UserVisit.objects.filter(completed_work_id=models.OuterRef("id"), status=VisitValidationStatus.approved)
        .values_list("visit_date")
        .order_by("-visit_date")[:1]
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
        .annotate(month_group=TruncMonth("filter_date"))
        .values("month_group")
        .annotate(
            users=models.Count("opportunity_access__user_id", distinct=True),
            services=models.Sum("saved_approved_count", default=0),
            flw_amount_earned=models.Sum("saved_payment_accrued_usd", default=0),
            nm_amount_earned=models.Sum(
                models.F("saved_org_payment_accrued_usd") + models.F("saved_payment_accrued_usd"), default=0
            ),
        )
        .order_by("month_group")
    )
    for item in visit_data:
        group_key = item["month_group"].strftime("%Y-%m"), item.get("delivery_type_name", delivery_type_name)
        visit_data_dict[group_key].update(item)

    visit_time_to_payment_data = (
        base_visit_data_qs.filter(
            payment_date__range=(start_date, end_date),
            saved_payment_accrued_usd__gt=0,
        )
        .annotate(month_group=TruncMonth("payment_date"))
        .values("month_group")
        .annotate(
            avg_time_to_payment=models.Avg(ExtractDay(time_to_payment), default=0),
            max_time_to_payment=models.Max(ExtractDay(time_to_payment), default=0),
        )
        .order_by("month_group")
    )
    for item in visit_time_to_payment_data:
        group_key = item["month_group"].strftime("%Y-%m"), item.get("delivery_type_name", delivery_type_name)
        visit_data_dict[group_key].update(item)

    payment_query = Payment.objects.filter(date_paid__range=(start_date, end_date))
    nm_amount_paid_data = (
        payment_query.filter(**filter_kwargs_nm)
        .annotate(month_group=TruncMonth("date_paid"))
        .values("month_group")
        .annotate(
            nm_amount_paid=models.Sum("amount_usd", default=0, filter=models.Q(invoice__service_delivery=True)),
            nm_other_amount_paid=models.Sum("amount_usd", default=0, filter=models.Q(invoice__service_delivery=False)),
        )
        .order_by("month_group")
    )
    for item in nm_amount_paid_data:
        group_key = item["month_group"].strftime("%Y-%m"), item.get("delivery_type_name", delivery_type_name)
        visit_data_dict[group_key].update(item)

    avg_top_flw_amount_paid = (
        payment_query.filter(**filter_kwargs)
        .annotate(month_group=TruncMonth("date_paid"))
        .values("month_group", "opportunity_access__user_id")
        .annotate(approved_sum=models.Sum("amount_usd", default=0))
        .order_by("month_group")
    )
    delivery_type_grouped_users = defaultdict(set)
    for item in avg_top_flw_amount_paid:
        group_key = item["month_group"].strftime("%Y-%m"), item.get("delivery_type_name", delivery_type_name)
        delivery_type_grouped_users[group_key].add((item["opportunity_access__user_id"], item["approved_sum"]))
    for group_key, users in delivery_type_grouped_users.items():
        month_group, delivery_type_name = group_key
        sum_total_users = defaultdict(int)
        for user, amount in users:
            sum_total_users[user] += amount

        # take atleast 1 top user in cases where this variable is 0
        top_five_percent_len = len(sum_total_users) // 20 or 1
        flw_amount_paid = sum(sum_total_users.values())
        avg_top_paid_flws = sum(sorted(sum_total_users.values(), reverse=True)[:top_five_percent_len])
        visit_data_dict[group_key].update(
            {
                "month_group": datetime.strptime(month_group, "%Y-%m"),
                "delivery_type_name": delivery_type_name,
                "flw_amount_paid": flw_amount_paid,
                "avg_top_paid_flws": avg_top_paid_flws,
            }
        )

    connectid_user_count = get_connectid_user_counts_cumulative()
    total_eligible_user_counts = get_eligible_user_counts_cumulative()
    for group_key in visit_data_dict.keys():
        month_group = group_key[0]
        visit_data_dict[group_key].update(
            {
                "connectid_users": connectid_user_count.get(month_group, 0),
                "total_eligible_users": total_eligible_user_counts.get(month_group, 0),
            }
        )
    return list(visit_data_dict.values())
