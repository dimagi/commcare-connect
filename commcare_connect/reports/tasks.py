import logging
from collections import defaultdict
from itertools import chain

from django.db.models import Case, Count, DateTimeField, F, IntegerField, Max, Min, Q, Sum, Value, When
from django.db.models.lookups import GreaterThanOrEqual

from commcare_connect.connect_id_client.main import fetch_user_analytics
from commcare_connect.opportunity.models import CompletedWorkStatus, OpportunityAccess
from commcare_connect.reports.models import UserAnalyticsData
from commcare_connect.users.models import User
from config import celery_app

logger = logging.getLogger(__name__)


@celery_app.task()
def sync_user_analytics_data():
    users = User.objects.filter(username__isnull=False, email__isnull=True, is_active=True).values_list(
        "id", "username"
    )
    personalid_analytics_data = fetch_user_analytics()
    logger.info(f"Fetched data for {len(personalid_analytics_data)} PersonalID users.")

    access_objects = (
        OpportunityAccess.objects.filter(user_id__in=[user_id for user_id, _ in users])
        .annotate(
            max_total=Sum("opportunity__paymentunit__max_total"),
            max_total_done=Count(
                "completedwork__id", filter=Q(completedwork__status=CompletedWorkStatus.approved.value)
            ),
        )
        .values("user_id")
        .annotate(
            total=Count("id", distinct=True),
            accepted_count=Count("id", filter=Q(accepted=True), distinct=True),
            completed_count=Sum(
                Case(
                    When(Q(GreaterThanOrEqual("max_total_done", "max_total"), accepted=True), then=Value(1)),
                    default=Value(0),
                    output_field=IntegerField(),
                )
            ),
        )
        .annotate(
            username=F("user__username"),
            has_opp_invite=Min("invited_date"),
            has_ever_earned_payment=Min(
                "completedwork__status_modified_date",
                filter=Q(completedwork__status=CompletedWorkStatus.approved),
            ),
            has_started_learning=Min("date_learn_started"),
            has_completed_learning=Min("completed_learn_date"),
            has_completed_assessment=Min("assessment__date", filter=Q(assessment__passed=True)),
            has_claimed_job=Min("opportunityclaim__date_claimed"),
            has_started_job=Min("completedwork__date_created"),
            has_been_paid=Min("payment__date_paid"),
            has_completed_opp=Case(
                When(Q(completed_count__gte=1), then=Max("completedwork__status_modified_date")),
                default=None,
                output_field=DateTimeField(),
            ),
            has_offered_multiple_opps=Case(
                When(Q(total__gt=1), then=Min("invited_date")),
                default=None,
                output_field=DateTimeField(),
            ),
            has_accepted_multiple_opps=Case(
                When(Q(accepted_count__gt=1), then=Min("invited_date", filter=Q(accepted=True))),
                default=None,
                output_field=DateTimeField(),
            ),
            has_completed_multiple_opps=Case(
                When(Q(completed_count__gt=1), then=Max("completedwork__status_modified_date")),
                default=None,
                output_field=DateTimeField(),
            ),
        )
        .values(
            "username",
            "user_id",
            "has_opp_invite",
            "has_ever_earned_payment",
            "has_started_learning",
            "has_completed_learning",
            "has_completed_assessment",
            "has_claimed_job",
            "has_started_job",
            "has_been_paid",
            "has_completed_opp",
            "has_completed_multiple_opps",
            "has_offered_multiple_opps",
            "has_accepted_multiple_opps",
        )
    )

    user_data_map = defaultdict(lambda: {})

    for user_id, username in users:
        user_data_map[username] = {"user_id": user_id, "username": username}

    for data in chain(access_objects, personalid_analytics_data):
        username = data.get("username")
        if username is not None:
            user_data_map[username]["has_sso_on_hq_app"] = data.pop("hq_sso_date", None)
            user_data_map[username].update(data)

    result = UserAnalyticsData.objects.bulk_create(
        [UserAnalyticsData(**data) for data in user_data_map.values()],
        update_conflicts=True,
        update_fields=[
            "has_opp_invite",
            "has_ever_earned_payment",
            "has_started_learning",
            "has_completed_learning",
            "has_completed_assessment",
            "has_claimed_job",
            "has_started_job",
            "has_been_paid",
            "has_completed_opp",
            "has_completed_multiple_opps",
            "has_offered_multiple_opps",
            "has_accepted_multiple_opps",
            "has_sso_on_hq_app",
        ],
        unique_fields=["username"],
        batch_size=500,
    )

    logger.info(f"Updated UserAnalyticsData for {len(result)} users.")
