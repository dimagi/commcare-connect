from datetime import timedelta

from django.db.models import (
    Avg,
    Case,
    Count,
    DurationField,
    ExpressionWrapper,
    F,
    FloatField,
    OuterRef,
    Q,
    Subquery,
    Value,
    When,
)
from django.db.models.functions import Cast, Coalesce, Round

from commcare_connect.cache import quickcache
from commcare_connect.opportunity.models import UserVisit, VisitValidationStatus
from commcare_connect.program.models import ManagedOpportunity, Program

EXCLUDED_STATUS = [
    VisitValidationStatus.over_limit,
    VisitValidationStatus.trial,
]

FILTER_FOR_VALID_VISIT_DATE = ~Q(opportunityaccess__uservisit__status__in=EXCLUDED_STATUS)


def calculate_safe_percentage(numerator, denominator):
    return Case(
        When(**{denominator: 0}, then=Value(0)),  # Handle division by zero
        default=Round(Cast(F(numerator), FloatField()) / Cast(F(denominator), FloatField()) * 100, 2),
        output_field=FloatField(),
    )


def get_annotated_managed_opportunity(program: Program):
    earliest_visits = (
        UserVisit.objects.filter(
            opportunity_access=OuterRef("opportunityaccess"),
            user=OuterRef("opportunityaccess__uservisit__user"),
        )
        .exclude(status__in=EXCLUDED_STATUS)
        .order_by("visit_date")
        .values("visit_date")[:1]
    )

    managed_opportunities = (
        ManagedOpportunity.objects.filter(program=program)
        .order_by("start_date")
        .annotate(
            workers_invited=Count("opportunityaccess", distinct=True),
            workers_passing_assessment=Count(
                "opportunityaccess",
                filter=Q(
                    opportunityaccess__assessment__passed=True,
                ),
                distinct=True,
            ),
            workers_starting_delivery=Count(
                "opportunityaccess__uservisit__user",
                filter=FILTER_FOR_VALID_VISIT_DATE,
                distinct=True,
            ),
            percentage_conversion=calculate_safe_percentage("workers_starting_delivery", "workers_invited"),
            average_time_to_convert=Coalesce(
                Avg(
                    ExpressionWrapper(
                        earliest_visits - F("opportunityaccess__invited_date"),
                        output_field=DurationField(),
                    ),
                    filter=FILTER_FOR_VALID_VISIT_DATE
                    & Q(opportunityaccess__invited_date__lte=Subquery(earliest_visits)),
                    distinct=True,
                ),
                Value(timedelta(seconds=0)),
            ),
        )
    )
    return managed_opportunities


def get_delivery_performance_report(program: Program, start_date, end_date):
    date_filter = FILTER_FOR_VALID_VISIT_DATE

    if start_date:
        date_filter &= Q(opportunityaccess__uservisit__visit_date__gte=start_date)

    if end_date:
        date_filter &= Q(opportunityaccess__uservisit__visit_date__lte=end_date)

    flagged_visits_filter = (
        Q(opportunityaccess__uservisit__flagged=True)
        & date_filter
        & Q(opportunityaccess__uservisit__completed_work__isnull=False)
    )

    managed_opportunities = (
        ManagedOpportunity.objects.filter(program=program)
        .order_by("start_date")
        .annotate(
            total_workers_starting_delivery=Count(
                "opportunityaccess__uservisit__user",
                filter=FILTER_FOR_VALID_VISIT_DATE,
                distinct=True,
            ),
            active_workers=Count(
                "opportunityaccess__uservisit__user",
                filter=date_filter,
                distinct=True,
            ),
            total_payment_units_with_flags=Count(
                "opportunityaccess__uservisit", distinct=True, filter=flagged_visits_filter
            ),
            total_payment_since_start_date=Count(
                "opportunityaccess__uservisit",
                distinct=True,
                filter=date_filter & Q(opportunityaccess__uservisit__completed_work__isnull=False),
            ),
            deliveries_per_worker=Case(
                When(active_workers=0, then=Value(0)),
                default=Round(F("total_payment_since_start_date") / F("active_workers"), 2),
                output_field=FloatField(),
            ),
            records_flagged_percentage=calculate_safe_percentage(
                "total_payment_units_with_flags", "total_payment_since_start_date"
            ),
        )
    )

    return managed_opportunities


@quickcache(vary_on=["current_org_slug", "opp_id"], timeout=24 * 60 * 60)
def is_org_manages_opportunity_via_program(current_org_slug, opp_id: int):
    """
    Check if the current organization manages the given managed opportunity
    through any of its programs.
    """
    programs = Program.objects.filter(organization__slug=current_org_slug)
    if programs:
        return ManagedOpportunity.objects.filter(program__in=programs, id=opp_id).exists()

    return False
