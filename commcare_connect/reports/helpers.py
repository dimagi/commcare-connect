import calendar
from collections import defaultdict
from datetime import datetime

from django.db.models import Max, Q, Sum
from django.utils.timezone import make_aware

from commcare_connect.opportunity.models import CompletedWork, CompletedWorkStatus, Payment


def get_table_data_for_year_month(year, month=None, delivery_type=None, group_by_delivery_type=False):
    delivery_type_filter = Q()
    if delivery_type:
        delivery_type_filter = Q(opportunity_access__opportunity__delivery_type__slug=delivery_type)

    _, month_end = calendar.monthrange(year, month or 1)
    start_date = make_aware(datetime(year, month or 1, 1))
    end_date = make_aware(datetime(year, month or 12, month_end))
    data = []

    user_set = defaultdict(set)
    beneficiary_set = defaultdict(set)
    service_count = defaultdict(int)

    visit_data = (
        CompletedWork.objects.annotate(work_date=Max("uservisit__visit_date"))
        .filter(
            delivery_type_filter,
            opportunity_access__opportunity__is_test=False,
            status=CompletedWorkStatus.approved,
            work_date__gte=start_date,
            work_date__lt=end_date,
        )
        .select_related("opportunity_access__opportunity__delivery_type")
    )
    for v in visit_data:
        delivery_type_name = "All"
        if group_by_delivery_type and v.opportunity_access.opportunity.delivery_type:
            delivery_type_name = v.opportunity_access.opportunity.delivery_type.name

        user_set[delivery_type_name].add(v.opportunity_access.user_id)
        beneficiary_set[delivery_type_name].add(v.entity_id)
        service_count[delivery_type_name] += v.saved_approved_count

    payment_query = Payment.objects.filter(
        delivery_type_filter,
        opportunity_access__opportunity__is_test=False,
        date_paid__gte=start_date,
        date_paid__lt=end_date,
    )

    approved_payment_data = (
        payment_query.filter(confirmed=True)
        .values("opportunity_access__opportunity__delivery_type__name")
        .annotate(approved_sum=Sum("amount_usd", default=0))
    )
    total_payment_data = payment_query.values("opportunity_access__opportunity__delivery_type__name").annotate(
        total_sum=Sum("amount_usd", default=0)
    )
    approved_payment_dict = {
        item["opportunity_access__opportunity__delivery_type__name"]: item["approved_sum"]
        for item in approved_payment_data
    }
    total_payment_dict = {
        item["opportunity_access__opportunity__delivery_type__name"]: item["total_sum"] for item in total_payment_data
    }

    for delivery_type_name in user_set.keys():
        delivery_type_data = {
            "delivery_type": delivery_type_name,
            "month": (month, year),
            "users": len(user_set[delivery_type_name]),
            "services": service_count[delivery_type_name],
            "approved_payments": approved_payment_dict.get(delivery_type_name, 0),
            "total_payments": total_payment_dict.get(delivery_type_name, 0),
            "beneficiaries": len(beneficiary_set[delivery_type_name]),
        }
        if delivery_type_name == "All":
            delivery_type_data.update(
                {
                    "approved_payments": sum(approved_payment_dict.values()),
                    "total_payments": sum(total_payment_dict.values()),
                    "beneficiaries": len(beneficiary_set[delivery_type_name]),
                }
            )
        data.append(delivery_type_data)
    return data
