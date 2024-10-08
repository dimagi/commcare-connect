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
from django.db.models.functions import Cast, Round

from commcare_connect.opportunity.models import UserVisit, VisitValidationStatus
from commcare_connect.program.models import ManagedOpportunity, Program


def calculate_safe_percentage(numerator, denominator):
    return Case(
        When(**{denominator: 0}, then=Value(0)),  # Handle division by zero
        default=Round(Cast(F(numerator), FloatField()) / Cast(F(denominator), FloatField()) * 100, 2),
        output_field=FloatField(),
    )


def get_annotated_managed_opportunity(program: Program):
    excluded_status = [
        VisitValidationStatus.over_limit,
        VisitValidationStatus.trial,
    ]

    filter_for_valid__visit_date = ~Q(opportunityaccess__uservisit__status__in=excluded_status)

    earliest_visits = (
        UserVisit.objects.filter(
            opportunity_access=OuterRef("opportunityaccess"),
        )
        .exclude(status__in=excluded_status)
        .order_by("visit_date")
        .values("visit_date")[:1]
    )

    managed_opportunities = (
        ManagedOpportunity.objects.filter(program=program)
        .order_by("start_date")
        .annotate(
            workers_invited=Count("opportunityaccess", distinct=True),
            workers_passing_assessment=Count(
                "opportunityaccess__assessment",
                filter=Q(
                    opportunityaccess__assessment__passed=True,
                ),
                distinct=True,
            ),
            workers_starting_delivery=Count(
                "opportunityaccess__uservisit__user",
                filter=filter_for_valid__visit_date,
                distinct=True,
            ),
            percentage_conversion=calculate_safe_percentage("workers_starting_delivery", "workers_invited"),
            average_time_to_convert=Avg(
                ExpressionWrapper(
                    Subquery(earliest_visits) - F("opportunityaccess__invited_date"), output_field=DurationField()
                ),
                filter=filter_for_valid__visit_date,
            ),
        )
        .prefetch_related(
            "opportunityaccess_set",
            "opportunityaccess_set__uservisit_set",
            "opportunityaccess_set__assessment_set",
        )
    )

    return managed_opportunities
