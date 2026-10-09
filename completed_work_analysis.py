from collections import defaultdict

from django.db.models import Case, Count, F, IntegerField, Value, When
from django.db.models.functions import Cast, Coalesce

from commcare_connect.opportunity.models import CompletedWork, CompletedWorkStatus


def get_avg_completed_work():
    """
    Get the average amount of completed work per user, categorized by delivery type and status.
    """
    aggregated_data = (
        CompletedWork.objects.filter(status=CompletedWorkStatus.approved)
        .values(
            delivery_type_name=Coalesce(
                "opportunity_access__opportunity__delivery_type__name", Value("no_delivery_type")
            ),
            status_name=F("status"),
        )
        .annotate(user_count=Count("opportunity_access__user_id", distinct=True), total_approved_work=Count("id"))
        .annotate(
            avg_completed_work_per_user=Case(
                When(
                    user_count__gt=0,
                    then=Cast(F("total_approved_work"), IntegerField()) / Cast(F("user_count"), IntegerField()),
                ),
                default=Value(0),
                output_field=IntegerField(),
            )
        )
        .values("delivery_type_name", "status_name", "avg_completed_work_per_user")
    )

    result = defaultdict(lambda: defaultdict(int))
    for item in aggregated_data:
        delivery_type = item["delivery_type_name"]
        status = item["status_name"]
        avg_value = int(item["avg_completed_work_per_user"] or 0)
        result[delivery_type][status] = avg_value

    return {dt: dict(statuses) for dt, statuses in result.items()}
