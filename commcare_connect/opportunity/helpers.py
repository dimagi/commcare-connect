from collections import namedtuple
from datetime import timedelta

from django.db.models import (
    BooleanField,
    Case,
    CharField,
    Count,
    DateTimeField,
    DecimalField,
    DurationField,
    Exists,
    ExpressionWrapper,
    F,
    FloatField,
    IntegerField,
    Max,
    Min,
    OuterRef,
    Q,
    Subquery,
    Sum,
    Value,
    When,
)
from django.db.models.functions import Coalesce, Greatest, Round, TruncDate
from django.utils.timezone import now

from commcare_connect.opportunity.models import (
    Assessment,
    CompletedModule,
    CompletedWork,
    CompletedWorkStatus,
    LearnModule,
    Opportunity,
    OpportunityAccess,
    OpportunityClaimLimit,
    PaymentUnit,
    UserInvite,
    UserInviteStatus,
    UserVisit,
    VisitReviewStatus,
    VisitValidationStatus, OpportunityClaimLimit, Payment,
)


class OpportunityAnnotations:
    @staticmethod
    def inactive_workers(days_ago):
        return Count(
            "opportunityaccess",
            filter=Q(opportunityaccess__last_active__isnull=False) &
                   Q(opportunityaccess__last_active__lt=days_ago),
            distinct=True,
        )

    @staticmethod
    def total_accrued():
        return Coalesce(
            Sum("opportunityaccess__payment_accrued", distinct=True),
            Value(0),
            output_field=DecimalField()
        )

    @staticmethod
    def total_paid():
        return Coalesce(
            Sum(
                "opportunityaccess__payment__amount",
                distinct=True,
            ),
            Value(0),
            output_field=DecimalField()
        )

    @staticmethod
    def pending_invites():
        return Count(
            "userinvite",
            filter=~Q(userinvite__status=UserInviteStatus.not_found)
                   & ~Q(userinvite__status=UserInviteStatus.accepted),
            distinct=True,
        )

    @staticmethod
    def workers_invited():
        return Count("userinvite", distinct=True, filter=~Q(userinvite__status=UserInviteStatus.not_found))

    @staticmethod
    def started_learning():
        return Count(
            "opportunityaccess", filter=Q(opportunityaccess__date_learn_started__isnull=False), distinct=True
        )


def get_deliveries_count_subquery(status=None):
    filters = {
        "opportunity_access__opportunity_id": OuterRef("pk")
    }
    if status is not None:
        filters["status"] = status

    return Coalesce(Subquery(
        CompletedWork.objects
        .filter(**filters)
        .values("opportunity_access__opportunity_id")
        .annotate(count=Count("id", distinct=True))
        .values("count"), output_field=IntegerField()
    ), 0)


def total_accrued_sq():
    return Coalesce(Subquery(OpportunityAccess.objects.filter(
        opportunity_id=OuterRef("pk")
    ).values("opportunity_id").annotate(
        total=Sum("payment_accrued")
    ).values("total"), output_field=IntegerField()), 0)


def total_paid_sq():
    return Coalesce(Subquery(Payment.objects.filter(
        opportunity_access__opportunity_id=OuterRef("pk")
    ).values("opportunity_access__opportunity_id").annotate(
        total=Sum("amount")
    ).values("total"), output_field=IntegerField()), 0)


def deliveries_from_yesterday_sq():
    return Coalesce(Subquery(UserVisit.objects.filter(
        opportunity_id=OuterRef("pk"),
        visit_date__gte=now().date() - timedelta(1)
    ).values("opportunity_id").annotate(
        count=Count("id", distinct=True)
    ).values("count"), output_field=IntegerField()), 0)


def get_annotated_opportunity_access(opportunity: Opportunity):
    learn_modules_count = opportunity.learn_app.learn_modules.count()
    access_objects = (
        UserInvite.objects.filter(opportunity=opportunity)
        .select_related("opportunity_access", "opportunity_access__opportunityclaim", "opportunity_access__user")
        .annotate(
            last_visit_date_d=Max(
                "opportunity_access__user__uservisit__visit_date",
                filter=Q(opportunity_access__user__uservisit__opportunity=opportunity)
                & ~Q(opportunity_access__user__uservisit__status=VisitValidationStatus.trial),
            ),
            date_deliver_started=Min(
                "opportunity_access__user__uservisit__visit_date",
                filter=Q(opportunity_access__user__uservisit__opportunity=opportunity),
            ),
            passed_assessment=Sum(
                Case(
                    When(
                        Q(
                            opportunity_access__user__assessments__opportunity=opportunity,
                            opportunity_access__user__assessments__passed=True,
                        ),
                        then=1,
                    ),
                    default=0,
                )
            ),
            completed_modules_count=Count(
                "opportunity_access__user__completed_modules__module",
                filter=Q(opportunity_access__user__completed_modules__opportunity=opportunity),
                distinct=True,
            ),
            job_claimed=Case(
                When(
                    Q(opportunity_access__opportunityclaim__isnull=False),
                    then="opportunity_access__opportunityclaim__date_claimed",
                )
            ),
        )
        .annotate(
            date_learn_completed=Case(
                When(
                    Q(completed_modules_count=learn_modules_count),
                    then=Max(
                        "opportunity_access__user__completed_modules__date",
                        filter=Q(opportunity_access__user__completed_modules__opportunity=opportunity),
                    ),
                )
            )
        )
        .order_by("opportunity_access__user__name")
    )

    return access_objects


def get_annotated_opportunity_access_deliver_status(opportunity: Opportunity):
    access_objects = []
    for payment_unit in opportunity.paymentunit_set.all():

        total_visits_sq = Subquery(OpportunityClaimLimit.objects.filter(
            opportunity_claim__opportunity_access_id=OuterRef("pk"),
            payment_unit=payment_unit
        ).values("max_visits")[:1], output_field=IntegerField())

        last_visit_sq = Subquery(
            UserVisit.objects.filter(opportunity_access_id=OuterRef("pk"))
            .values("opportunity_access_id")
            .annotate(max_visit_date=Max("visit_date"))
            .values("max_visit_date")[:1],
            output_field=DateTimeField(null=True),
        )

        last_module_sq = Subquery(
            CompletedModule.objects.filter(opportunity_access_id=OuterRef("pk"))
            .values("opportunity_access_id")
            .annotate(max_module_date=Max("date"))
            .values("max_module_date")[:1],
            output_field=DateTimeField(null=True),
        )

        duplicate_sq = Subquery(
            CompletedWork.objects.filter(
                opportunity_access_id=OuterRef("pk"),
                payment_unit=payment_unit,
                saved_completed_count__gt=1,
            )
            .values("opportunity_access_id")
            .annotate(duplicate_count=Count("id", distinct=True))
            .values("duplicate_count")[:1],
            output_field=IntegerField(),
        )

        def completed_work_status_subquery(status_value):
            return Subquery(
                CompletedWork.objects.filter(
                    opportunity_access_id=OuterRef("pk"), payment_unit=payment_unit, status=status_value
                )
                .values("opportunity_access_id")
                .annotate(status_count=Count("id", distinct=True))
                .values("status_count")[:1],
                output_field=IntegerField(),
            )

        def completed_work_status_total_subquery(status_value):
            return Subquery(
                CompletedWork.objects.filter(
                    opportunity_access_id=OuterRef("pk"), status=status_value
                )
                .values("opportunity_access_id")
                .annotate(status_count=Count("id", distinct=True))
                .values("status_count")[:1],
                output_field=IntegerField(),
            )

        pending_count_sq = completed_work_status_subquery(CompletedWorkStatus.pending)
        approved_count_sq = completed_work_status_subquery(CompletedWorkStatus.approved)
        rejected_count_sq = completed_work_status_subquery(CompletedWorkStatus.rejected)
        over_limit_count_sq = completed_work_status_subquery(CompletedWorkStatus.over_limit)
        incomplete_count_sq = completed_work_status_subquery(CompletedWorkStatus.incomplete)

        total_pending_for_user =completed_work_status_total_subquery(CompletedWorkStatus.pending)
        total_approved_for_user =completed_work_status_total_subquery(CompletedWorkStatus.approved)
        total_rejected_for_user =completed_work_status_total_subquery(CompletedWorkStatus.rejected)
        total_over_limit_for_user =completed_work_status_total_subquery(CompletedWorkStatus.over_limit)

        queryset = (
            OpportunityAccess.objects.filter(opportunity=opportunity)
            .annotate(
                payment_unit_id=Value(payment_unit.pk),
                payment_unit=Value(payment_unit.name, output_field=CharField()),
                total_visits=Coalesce(total_visits_sq, Value(None, output_field=IntegerField())),  # Optional
                _last_visit_val=Coalesce(last_visit_sq, Value(None, output_field=DateTimeField())),
                _last_module_val=Coalesce(last_module_sq, Value(None, output_field=DateTimeField())),
                pending=Coalesce(pending_count_sq, Value(0)),
                approved=Coalesce(approved_count_sq, Value(0)),
                rejected=Coalesce(rejected_count_sq, Value(0)),
                duplicate=Coalesce(duplicate_sq, Value(0)),
                over_limit=Coalesce(over_limit_count_sq, Value(0)),
                incomplete=Coalesce(incomplete_count_sq, Value(0)),
                total_pending=Coalesce(total_pending_for_user, Value(0)),
                total_approved=Coalesce(total_approved_for_user, Value(0)),
                total_rejected=Coalesce(total_rejected_for_user, Value(0)),
                total_over_limit=Coalesce(total_over_limit_for_user, Value(0)),
            )
            .annotate(
                completed=(
                    F('pending') + F('approved') + F('rejected') + F('over_limit')
                ),
                total_completed=(F('total_pending') + F('total_approved') + F('total_rejected') + F('total_over_limit'))

            )
            .select_related('user')
            .order_by('user__name')
        )
        access_objects += queryset
    access_objects.sort(key=lambda a: a.user.name)
    return access_objects


def get_payment_report_data(opportunity: Opportunity):
    payment_units = PaymentUnit.objects.filter(opportunity=opportunity)
    PaymentReportData = namedtuple(
        "PaymentReportData", ["payment_unit", "approved", "user_payment_accrued", "nm_payment_accrued"]
    )
    data = []
    total_user_payment_accrued = 0
    total_nm_payment_accrued = 0
    for payment_unit in payment_units:
        completed_works = CompletedWork.objects.filter(
            opportunity_access__opportunity=opportunity, status=CompletedWorkStatus.approved, payment_unit=payment_unit
        )
        completed_work_count = len(completed_works)
        user_payment_accrued = sum([cw.payment_accrued for cw in completed_works])
        nm_payment_accrued = completed_work_count * opportunity.managedopportunity.org_pay_per_visit
        total_user_payment_accrued += user_payment_accrued
        total_nm_payment_accrued += nm_payment_accrued
        data.append(
            PaymentReportData(payment_unit.name, completed_work_count, user_payment_accrued, nm_payment_accrued)
        )
    return data, total_user_payment_accrued, total_nm_payment_accrued


def get_opportunity_list_data(organization, program_manager=False):
    today = now().date()
    three_days_ago = now() - timedelta(days=3)

    pending_approvals_sq = Subquery(
        UserVisit.objects.filter(opportunity_access__opportunity_id=OuterRef("pk"), status="pending")
        .values("opportunity_access__opportunity_id")
        .annotate(count=Count("id", distinct=True))
        .values("count")[:1],
        output_field=IntegerField(),
    )

    base_filter = Q(organization=organization)
    if program_manager:
        base_filter |= Q(managedopportunity__program__organization=organization)

    queryset = Opportunity.objects.filter(base_filter).annotate(
        program=F("managedopportunity__program__name"),
        pending_invites=OpportunityAnnotations.pending_invites(),
        pending_approvals=Coalesce(pending_approvals_sq, Value(0)),
        total_accrued=OpportunityAnnotations.total_accrued(),
        total_paid=OpportunityAnnotations.total_paid(),
        payments_due=ExpressionWrapper(
            F("total_accrued") - F("total_paid"),
            output_field=DecimalField(),
        ),
        inactive_workers=OpportunityAnnotations.inactive_workers(three_days_ago),
        status=Case(
            When(Q(active=True) & Q(end_date__gte=today), then=Value(0)),  # Active
            When(Q(active=True) & Q(end_date__lt=today), then=Value(1)),  # Ended
            default=Value(2),  # Inactive
            output_field=IntegerField(),
        ),
    )

    if program_manager:
        total_deliveries_sq = Subquery(
            CompletedWork.objects.filter(opportunity_access__opportunity_id=OuterRef("pk"))
            .values("opportunity_access__opportunity_id")
            .annotate(total=Sum("saved_completed_count", distinct=True))
            .values("total")[:1],
            output_field=IntegerField(),
        )

        verified_deliveries_sq = Subquery(
            CompletedWork.objects.filter(opportunity_access__opportunity_id=OuterRef("pk"))
            .values("opportunity_access__opportunity_id")
            .annotate(total=Sum("saved_approved_count", distinct=True))
            .values("total")[:1],
            output_field=IntegerField(),
        )

        queryset = queryset.annotate(
            total_workers=Count("opportunityaccess", distinct=True),
            started_learning=OpportunityAnnotations.started_learning(),
            total_deliveries=Coalesce(total_deliveries_sq, Value(0)),
            verified_deliveries=Coalesce(verified_deliveries_sq, Value(0)),
            active_workers=F("started_learning") - F("inactive_workers"),
        )

    return queryset


def get_worker_table_data(opportunity):
    learn_modules_count = opportunity.learn_app.learn_modules.count()

    min_dates_per_module = (
        CompletedModule.objects.filter(opportunity_access=OuterRef("pk"))
        .values("module")
        .annotate(min_date=Min("date"))
        .values("min_date")
    )

    queryset = OpportunityAccess.objects.filter(opportunity=opportunity).annotate(
        completed_modules_count=Count(
            "completedmodule__module",
            distinct=True,
        ),
        completed_learn=Case(
            When(
                Q(completed_modules_count=learn_modules_count),
                then=Subquery(min_dates_per_module.order_by("-min_date")[:1]),
            ),
            default=None,
        ),
        days_to_complete_learn=ExpressionWrapper(
            F("completed_learn") - F("date_learn_started"),
            output_field=DurationField(),
        ),
        first_delivery=Min(
            "uservisit__visit_date",
        ),
        days_to_start_delivery=Case(
            When(
                date_learn_started__isnull=False,
                first_delivery__isnull=False,
                then=ExpressionWrapper(F("first_delivery") - F("date_learn_started"), output_field=DurationField()),
            ),
            default=None,
            output_field=DurationField(),
        ),
    )

    return queryset


def get_worker_learn_table_data(opportunity):
    learn_modules_count = opportunity.learn_app.learn_modules.count()
    min_dates_per_module = (
        CompletedModule.objects.filter(opportunity_access=OuterRef("pk"))
        .values("module")
        .annotate(min_date=Min("date"))
        .values("min_date")
    )

    def assessment_exists_subquery(passed: bool):
        return Assessment.objects.filter(
            opportunity_access_id=OuterRef('pk'),
            passed=passed
        )

    duration_subquery = (
        CompletedModule.objects.filter(opportunity_access=OuterRef("pk"))
        .values("opportunity_access")
        .annotate(total_duration=Sum("duration"))
        .values("total_duration")[:1]
    )
    queryset = OpportunityAccess.objects.filter(opportunity=opportunity, accepted=True).annotate(
        completed_modules_count=Count("completedmodule__module", distinct=True),
        completed_learn=Case(
            When(
                Q(completed_modules_count=learn_modules_count),
                then=Subquery(min_dates_per_module.order_by("-min_date")[:1]),
            ),
            default=None,
        ),
        assesment_count=Count("assessment", distinct=True),
        learning_hours=Subquery(duration_subquery, output_field=DurationField()),
        modules_completed_percentage=Round(
            ExpressionWrapper(F("completed_modules_count") * 100.0 / learn_modules_count, output_field=FloatField()), 1
        ),
        assessment_status_rank=Case(
            When(Exists(assessment_exists_subquery(passed=True)), then=Value(2)),  # Passed
            When(Exists(assessment_exists_subquery(passed=False)), then=Value(1)),  # Failed
            default=Value(0),
            output_field=IntegerField(),
        )
    )
    return queryset


def get_opportunity_delivery_progress(opp_id):
    today = now().replace(hour=0, minute=0, second=0, microsecond=0)
    three_days_ago = today - timedelta(days=3)
    yesterday = today - timedelta(days=1)

    accrued_since_yesterday_sq = Coalesce(Subquery(CompletedWork.objects.filter(
        opportunity_access__opportunity_id=OuterRef("pk"),
        status_modified_date__gte=yesterday,
        status=CompletedWorkStatus.approved,
    ).values("opportunity_access__opportunity_id").annotate(
        total=Sum("saved_payment_accrued")
    ).values("total"), output_field=IntegerField()), 0)

    most_recent_delivery_sq = Subquery(UserVisit.objects.filter(
        opportunity_id=OuterRef("pk")
    ).order_by("-visit_date").values("visit_date")[:1], output_field=DateTimeField())

    flagged_deliveries_waiting_review_sq = Coalesce(Subquery(UserVisit.objects.filter(
        opportunity_id=OuterRef("pk"),
        status=VisitValidationStatus.pending,
    ).values("opportunity_id").annotate(
        count=Count("id", distinct=True)
    ).values("count"), output_field=IntegerField()),0)

    flagged_since_yesterday_sq = Coalesce(Subquery(UserVisit.objects.filter(
        opportunity_id=OuterRef("pk"),
        status=VisitValidationStatus.pending,
        visit_date__gte=yesterday,
    ).values("opportunity_id").annotate(
        count=Count("id", distinct=True)
    ).values("count"),output_field=IntegerField()),0)

    visits_pending_pm_sq = Coalesce(Subquery(UserVisit.objects.filter(
        opportunity_id=OuterRef("pk"),
        review_status=VisitReviewStatus.pending,
        review_created_on__isnull=False
    ).values("opportunity_id").annotate(
        count=Count("id", distinct=True)
    ).values("count"), output_field=IntegerField()),0)

    visits_pending_pm_yesterday_sq = Coalesce(Subquery(UserVisit.objects.filter(
        opportunity_id=OuterRef("pk"),
        review_status=VisitReviewStatus.pending,
        review_created_on__isnull=False,
        review_created_on__gte=yesterday,
    ).values("opportunity_id").annotate(
        count=Count("id", distinct=True)
    ).values("count"), output_field=IntegerField()), 0)

    recent_payment_sq = Subquery(Payment.objects.filter(
        opportunity_access__opportunity_id=OuterRef("pk")
    ).order_by("-date_paid").values("date_paid")[:1], output_field=DateTimeField())

    annotated_opportunity =  Opportunity.objects.filter(id=opp_id).annotate(
        inactive_workers=OpportunityAnnotations.inactive_workers(three_days_ago),
        deliveries_from_yesterday=deliveries_from_yesterday_sq(),
        accrued_since_yesterday=accrued_since_yesterday_sq,
        most_recent_delivery=most_recent_delivery_sq,
        total_deliveries=get_deliveries_count_subquery(),
        flagged_deliveries_waiting_for_review=flagged_deliveries_waiting_review_sq,
        flagged_deliveries_waiting_for_review_since_yesterday=flagged_since_yesterday_sq,
        visits_pending_for_pm_review=visits_pending_pm_sq,
        visits_pending_for_pm_review_since_yesterday=visits_pending_pm_yesterday_sq,
        recent_payment=recent_payment_sq,
        workers_invited=OpportunityAnnotations.workers_invited(),
        pending_invites=OpportunityAnnotations.pending_invites(),
        total_accrued=total_accrued_sq(),
        total_paid=total_paid_sq(),
        payments_due=ExpressionWrapper(
            F("total_accrued") - F("total_paid"),
            output_field=IntegerField()
        ),

    )

    return annotated_opportunity.first()



def get_opportunity_worker_progress(opp_id):
    return Opportunity.objects.filter(id=opp_id).annotate(
        total_deliveries=get_deliveries_count_subquery(),
        approved_deliveries=get_deliveries_count_subquery(CompletedWorkStatus.approved),
        rejected_deliveries=get_deliveries_count_subquery(CompletedWorkStatus.rejected),
        total_accrued=total_accrued_sq(),
        total_paid=total_paid_sq(),
        visits_since_yesterday=deliveries_from_yesterday_sq(),
    ).first()


def get_opportunity_funnel_progress(opp_id):
    started_deliveries_sq = UserVisit.objects.filter(
        opportunity_id=OuterRef("pk")
    ).values("opportunity_id").annotate(
        count=Count("user_id", distinct=True)
    ).values("count")

    return Opportunity.objects.filter(id=opp_id).annotate(
        workers_invited=OpportunityAnnotations.workers_invited(),
        pending_invites=OpportunityAnnotations.pending_invites(),
        started_learning_count=OpportunityAnnotations.started_learning(),
        claimed_job=Count("opportunityaccess__opportunityclaim", distinct=True),
        started_deliveries=Coalesce(
            Subquery(started_deliveries_sq, output_field=IntegerField()),
            0
        ),
        completed_assessments=Count(
            "assessment__user",
            filter=Q(assessment__passed=True),
            distinct=True,
        ),
        completed_learning=Count(
            "opportunityaccess__user",
            filter=Q(opportunityaccess__completed_learn_date__isnull=False),
            distinct=True,
        ),
    ).first()
