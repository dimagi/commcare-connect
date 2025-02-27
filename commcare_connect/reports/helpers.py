from collections import defaultdict

from django.db import models
from django.db.models.functions import ExtractDay, TruncMonth
from django.utils.timezone import now

from commcare_connect.opportunity.models import (
    CompletedWork,
    CompletedWorkStatus,
    Payment,
    UserVisit,
    VisitValidationStatus,
)


def get_table_data_for_year_month(
    from_date=None,
    to_date=None,
    delivery_type=None,
    group_by_delivery_type=False,
    program=None,
    network_manager=None,
    opportunity=None,
    country_currency=None,
):
    from_date = from_date or now()
    to_date = to_date or now()

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

    group_values = ["month_group"]
    if group_by_delivery_type:
        group_values.append("delivery_type_name")

    max_visit_date = (
        UserVisit.objects.filter(completed_work_id=models.OuterRef("id"), status=VisitValidationStatus.approved)
        .values_list("visit_date")
        .order_by("-visit_date")[:1]
    )
    time_to_payment = models.ExpressionWrapper(
        models.F("payment_date") - models.Subquery(max_visit_date), output_field=models.DurationField()
    )
    visit_data = (
        CompletedWork.objects.annotate(
            filter_date=models.Case(
                models.When(status_modified_date__isnull=True, then=models.F("date_created")),
                default=models.F("status_modified_date"),
            )
        )
        .filter(
            filter_date__month__gte=from_date.month,
            filter_date__year__gte=from_date.year,
            filter_date__month__lte=to_date.month,
            filter_date__year__lte=to_date.year,
            **filter_kwargs,
            status=CompletedWorkStatus.approved,
            saved_approved_count__gt=0,
            saved_payment_accrued_usd__gt=0,
            saved_org_payment_accrued_usd__gt=0,
        )
        .annotate(
            month_group=TruncMonth("filter_date"),
            delivery_type_name=models.F("opportunity_access__opportunity__delivery_type__name"),
        )
        .values(*group_values)
        .annotate(
            users=models.Count("opportunity_access__user_id", distinct=True),
            services=models.Sum("saved_approved_count", default=0),
            flw_amount_earned=models.Sum("saved_payment_accrued_usd", default=0),
            nm_amount_earned=models.Sum(
                models.F("saved_org_payment_accrued_usd") + models.F("saved_payment_accrued_usd"), default=0
            ),
            avg_time_to_payment=models.Avg(
                ExtractDay(time_to_payment), default=0, filter=models.Q(payment_date__gte=models.F("date_created"))
            ),
            max_time_to_payment=models.Max(
                ExtractDay(time_to_payment), default=0, filter=models.Q(payment_date__gte=models.F("date_created"))
            ),
        )
        .order_by("month_group")
    )
    visit_data_dict = defaultdict(
        lambda: {
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
    for item in visit_data:
        group_key = item["month_group"], item.get("delivery_type_name", "All")
        visit_data_dict[group_key].update(item)

    payment_query = Payment.objects.filter(
        date_paid__month__gte=from_date.month,
        date_paid__year__gte=from_date.year,
        date_paid__month__lte=to_date.month,
        date_paid__year__lte=to_date.year,
    )
    nm_amount_paid_data = (
        payment_query.filter(**filter_kwargs_nm)
        .annotate(
            delivery_type_name=models.F("invoice__opportunity__delivery_type__name"),
            month_group=TruncMonth("date_paid"),
        )
        .values(*group_values)
        .annotate(
            nm_amount_paid=models.Sum("amount_usd", default=0, filter=models.Q(invoice__service_delivery=True)),
            nm_other_amount_paid=models.Sum("amount_usd", default=0, filter=models.Q(invoice__service_delivery=False)),
        )
        .order_by("month_group")
    )
    for item in nm_amount_paid_data:
        group_key = item["month_group"], item.get("delivery_type_name", "All")
        visit_data_dict[group_key].update(item)

    avg_top_flw_amount_paid = (
        payment_query.filter(**filter_kwargs, confirmed=True)
        .annotate(
            delivery_type_name=models.F("opportunity_access__opportunity__delivery_type__name"),
            month_group=TruncMonth("date_paid"),
        )
        .values(*group_values, "opportunity_access__user_id")
        .annotate(approved_sum=models.Sum("amount_usd", default=0))
        .order_by("month_group")
    )
    delivery_type_grouped_users = defaultdict(set)
    for item in avg_top_flw_amount_paid:
        delivery_type_grouped_users[(item["month_group"], item.get("delivery_type_name", "All"))].add(
            (item["opportunity_access__user_id"], item["approved_sum"])
        )
    for group_key, users in delivery_type_grouped_users.items():
        sum_total_users = defaultdict(int)
        for user, amount in users:
            sum_total_users[user] += amount

        # take atleast 1 top user in cases where this variable is 0
        top_five_percent_len = len(sum_total_users) // 20 or 1
        flw_amount_paid = sum(sum_total_users.values())
        avg_top_paid_flws = sum(sorted(sum_total_users.values(), reverse=True)[:top_five_percent_len])
        visit_data_dict[group_key].update({"flw_amount_paid": flw_amount_paid, "avg_top_paid_flws": avg_top_paid_flws})
    return list(visit_data_dict.values())
