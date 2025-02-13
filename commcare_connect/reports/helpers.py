import calendar
from collections import defaultdict
from datetime import datetime, timedelta

from django.db.models import Avg, Count, DurationField, ExpressionWrapper, F, Max, OuterRef, Q, Subquery, Sum
from django.utils.timezone import make_aware

from commcare_connect.opportunity.models import (
    CompletedWork,
    CompletedWorkStatus,
    Payment,
    UserVisit,
    VisitValidationStatus,
)


def get_table_data_for_year_month(
    year=None,
    month=None,
    delivery_type=None,
    group_by_delivery_type=False,
    program=None,
    network_manager=None,
    opportunity=None,
):
    year = year or datetime.now().year
    _, month_end = calendar.monthrange(year, month or 1)
    start_date = make_aware(datetime(year, month or 1, 1))
    end_date = make_aware(datetime(year, month or 12, month_end))

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

    data = []

    max_visit_date = (
        UserVisit.objects.filter(completed_work_id=OuterRef("id"), status=VisitValidationStatus.approved)
        .values_list("visit_date")
        .order_by("-visit_date")[:1]
    )
    time_to_payment = ExpressionWrapper(F("payment_date") - Subquery(max_visit_date), output_field=DurationField())
    visit_data = (
        CompletedWork.objects.annotate(work_date=Max("uservisit__visit_date"))
        .filter(
            Q(status_modified_date__gte=start_date, status_modified_date__lt=end_date)
            | Q(status_modified_date__isnull=True, work_date__gte=start_date, work_date__lt=end_date),
            **filter_kwargs,
            status=CompletedWorkStatus.approved,
        )
        .values("opportunity_access__opportunity__delivery_type__name")
        .annotate(
            users=Count("opportunity_access__user_id", distinct=True),
            service_count=Sum("saved_approved_count", default=0),
            flw_amount_earned=Sum("saved_payment_accrued", default=0),
            nm_amount_earned=Sum(F("saved_org_payment_accrued") + F("saved_payment_accrued"), default=0),
            avg_time_to_payment=Avg(time_to_payment, default=timedelta(days=0)),
            max_time_to_payment=Max(time_to_payment, default=timedelta(days=0)),
        )
    )

    payment_query = Payment.objects.filter(
        date_paid__gte=start_date,
        date_paid__lt=end_date,
    )

    user_count_data = {
        item["opportunity_access__opportunity__delivery_type__name"]: item["users"] for item in visit_data
    }
    service_count_data = {
        item["opportunity_access__opportunity__delivery_type__name"]: item["service_count"] for item in visit_data
    }
    avg_time_to_payment_data = {
        item["opportunity_access__opportunity__delivery_type__name"]: item["avg_time_to_payment"]
        for item in visit_data
    }
    max_time_to_payment_data = {
        item["opportunity_access__opportunity__delivery_type__name"]: item["max_time_to_payment"]
        for item in visit_data
    }
    flw_amount_earned_data = {
        item["opportunity_access__opportunity__delivery_type__name"]: item["flw_amount_earned"] for item in visit_data
    }
    nm_amount_earned_data = {
        item["opportunity_access__opportunity__delivery_type__name"]: item["nm_amount_earned"] for item in visit_data
    }
    nm_amount_paid = (
        payment_query.filter(**filter_kwargs_nm)
        .values("invoice__opportunity__delivery_type__name")
        .annotate(approved_sum=Sum("amount_usd", default=0))
    )
    nm_amount_paid_data = {
        item["invoice__opportunity__delivery_type__name"]: item["approved_sum"] for item in nm_amount_paid
    }

    flw_amount_paid_data = {}
    avg_top_flw_amount_paid = (
        payment_query.filter(**filter_kwargs, confirmed=True)
        .values("opportunity_access__opportunity__delivery_type__name", "opportunity_access__user_id")
        .annotate(approved_sum=Sum("amount_usd", default=0))
    )
    delivery_type_grouped_users = defaultdict(set)
    for item in avg_top_flw_amount_paid:
        delivery_type_grouped_users[item["opportunity_access__opportunity__delivery_type__name"]].add(
            (item["opportunity_access__user_id"], item["approved_sum"])
        )
    avg_top_flw_amount_paid_data = {}
    for d_name, users in delivery_type_grouped_users.items():
        sum_total_users = defaultdict(int)
        for user, amount in users:
            sum_total_users[user] += amount

        flw_amount_paid_data[d_name] = sum(sum_total_users.values())
        # take atleast 1 top user in cases where this variable is 0
        top_five_percent_len = len(sum_total_users) * 5 // 100 or 1
        avg_top_flw_amount_paid_data[d_name] = sum(sorted(sum_total_users.values())[:top_five_percent_len])

    if group_by_delivery_type:
        for delivery_type_name in user_count_data.keys():
            nm_amount_earned = nm_amount_earned_data.get(delivery_type_name, 0)
            nm_amount_paid = nm_amount_paid_data.get(delivery_type_name, 0)
            data.append(
                {
                    "delivery_type": delivery_type_name,
                    "month": (month, year),
                    "users": user_count_data[delivery_type_name],
                    "services": service_count_data[delivery_type_name],
                    "avg_time_to_payment": avg_time_to_payment_data.get(delivery_type_name, timedelta(days=0)).days,
                    "max_time_to_payment": max_time_to_payment_data.get(delivery_type_name, timedelta(days=0)).days,
                    "flw_amount_earned": flw_amount_earned_data.get(delivery_type_name, 0),
                    "flw_amount_paid": flw_amount_paid_data.get(delivery_type_name, 0),
                    "nm_amount_earned": nm_amount_earned,
                    "nm_amount_paid": nm_amount_paid,
                    "avg_top_paid_flws": avg_top_flw_amount_paid_data.get(delivery_type_name, 0),
                }
            )

    else:
        nm_amount_earned = sum(nm_amount_earned_data.values())
        nm_amount_paid = sum(nm_amount_paid_data.values())
        data.append(
            {
                "delivery_type": "All",
                "month": (month, year),
                "users": sum(user_count_data.values()),
                "services": sum(service_count_data.values()),
                "avg_time_to_payment": sum(avg_time_to_payment_data.values(), start=timedelta(days=0)).days,
                "max_time_to_payment": sum(max_time_to_payment_data.values(), start=timedelta(days=0)).days,
                "flw_amount_earned": sum(flw_amount_earned_data.values()),
                "flw_amount_paid": sum(flw_amount_paid_data.values()),
                "nm_amount_earned": nm_amount_earned,
                "nm_amount_paid": nm_amount_paid,
                "avg_top_paid_flws": sum(avg_top_flw_amount_paid_data.values()),
            }
        )
    return data
