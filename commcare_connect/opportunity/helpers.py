from collections import namedtuple
from datetime import timedelta

from django.db.models import (
    Case,
    Count,
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
    PaymentUnit,
    UserInvite,
    UserInviteStatus,
    UserVisit,
    VisitReviewStatus,
    VisitValidationStatus,
)


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
        access_objects += (
            OpportunityAccess.objects.filter(opportunity=opportunity)
            .select_related("user")
            .annotate(
                payment_unit=Value(payment_unit.name),
                last_active=Greatest(Max("uservisit__visit_date"), Max("completedmodule__date"), "date_learn_started"),
                started_delivery=Min("uservisit__visit_date"),
                pending=Count(
                    "completedwork",
                    filter=Q(
                        completedwork__opportunity_access_id=F("pk"),
                        completedwork__payment_unit=payment_unit,
                        completedwork__status=CompletedWorkStatus.pending,
                    ),
                    distinct=True,
                ),
                approved=Count(
                    "completedwork",
                    filter=Q(
                        completedwork__opportunity_access_id=F("pk"),
                        completedwork__payment_unit=payment_unit,
                        completedwork__status=CompletedWorkStatus.approved,
                    ),
                    distinct=True,
                ),
                rejected=Count(
                    "completedwork",
                    filter=Q(
                        completedwork__opportunity_access_id=F("pk"),
                        completedwork__payment_unit=payment_unit,
                        completedwork__status=CompletedWorkStatus.rejected,
                    ),
                    distinct=True,
                ),
                over_limit=Count(
                    "completedwork",
                    filter=Q(
                        completedwork__opportunity_access_id=F("pk"),
                        completedwork__payment_unit=payment_unit,
                        completedwork__status=CompletedWorkStatus.over_limit,
                    ),
                    distinct=True,
                ),
                incomplete=Count(
                    "completedwork",
                    filter=Q(
                        completedwork__opportunity_access_id=F("pk"),
                        completedwork__payment_unit=payment_unit,
                        completedwork__status=CompletedWorkStatus.incomplete,
                    ),
                    distinct=True,
                ),
                completed=F("approved") + F("rejected") + F("pending") + F("over_limit"),
                duplicate=Count(
                    "uservisit",
                    filter=Q(
                        uservisit__opportunity_access_id=F("pk"),
                        uservisit__deliver_unit__payment_unit=payment_unit,
                        uservisit__status=VisitValidationStatus.duplicate,
                    ),
                ),
            )
            .order_by("user__name")
        )
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

    base_filter = Q(organization=organization)
    if program_manager:
        base_filter |= Q(managedopportunity__program__organization=organization)

    queryset = Opportunity.objects.filter(base_filter).annotate(
        program=F("managedopportunity__program__name"),
        pending_invites=Count(
            "userinvite",
            filter=~Q(userinvite__status=UserInviteStatus.accepted),
            distinct=True,
        ),
        pending_approvals=Count(
            "uservisit",
            filter=Q(uservisit__status=VisitValidationStatus.pending),
            distinct=True,
        ),
        total_accrued=Coalesce(
            Sum("opportunityaccess__payment_accrued", distinct=True), Value(0), output_field=DecimalField()
        ),
        total_paid=Coalesce(
            Sum(
                "opportunityaccess__payment__amount",
                filter=Q(opportunityaccess__payment__confirmed=True),
                distinct=True,
            ),
            Value(0),
            output_field=DecimalField(),
        ),
        payments_due=ExpressionWrapper(
            F("total_accrued") - F("total_paid"),
            output_field=DecimalField(),
        ),
        inactive_workers=Count(
            "opportunityaccess",
            filter=Q(
                ~Exists(
                    UserVisit.objects.filter(
                        opportunity_access=OuterRef("opportunityaccess"),
                        visit_date__gte=three_days_ago,
                    )
                )
                & ~Exists(
                    CompletedModule.objects.filter(
                        opportunity_access=OuterRef("opportunityaccess"),
                        date__gte=three_days_ago,
                    )
                )
            ),
            distinct=True,
        ),
        status=Case(
            When(Q(active=True) & Q(end_date__gte=today), then=Value(0)),  # Active
            When(Q(active=True) & Q(end_date__lt=today), then=Value(1)),  # Ended
            default=Value(2),  # Inactive
            output_field=IntegerField(),
        ),
    )

    if program_manager:
        queryset = queryset.annotate(
            total_workers=Count("opportunityaccess", distinct=True),
            active_workers=F("total_workers") - F("inactive_workers"),
            total_deliveries=Sum("opportunityaccess__completedwork__saved_completed_count", distinct=True),
            verified_deliveries=Sum("opportunityaccess__completedwork__saved_approved_count", distinct=True),
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
        last_active=Greatest(Max("uservisit__visit_date"), Max("completedmodule__date"), "date_learn_started"),
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

    assessments_qs = Assessment.objects.filter(user=OuterRef("user"), opportunity=OuterRef("opportunity"), passed=True)

    duration_subquery = (
        CompletedModule.objects.filter(opportunity_access=OuterRef("pk"))
        .values("opportunity_access")
        .annotate(total_duration=Sum("duration"))
        .values("total_duration")[:1]
    )
    queryset = OpportunityAccess.objects.filter(opportunity=opportunity).annotate(
        last_active=Greatest(Max("uservisit__visit_date"), Max("completedmodule__date"), "date_learn_started"),
        completed_modules_count=Count("completedmodule__module", distinct=True),
        completed_learn=Case(
            When(
                Q(completed_modules_count=learn_modules_count),
                then=Subquery(min_dates_per_module.order_by("-min_date")[:1]),
            ),
            default=None,
        ),
        passed_assessment=Exists(assessments_qs),
        assesment_count=Count("assessment", distinct=True),
        learning_hours=Subquery(duration_subquery, output_field=DurationField()),
        modules_completed_percentage=Round(
            ExpressionWrapper(F("completed_modules_count") * 100.0 / learn_modules_count, output_field=FloatField()), 1
        ),
    )
    return queryset


def get_opportunity_delivery_progress(opp_id):
    today = now()
    three_days_ago = today - timedelta(days=3)
    yesterday = today - timedelta(days=1)

    aggregates = Opportunity.objects.filter(id=opp_id).aggregate(
        inactive_workers=Count(
            "opportunityaccess__id",
            filter=~Q(opportunityaccess__uservisit__visit_date__gte=three_days_ago)
            & ~Q(opportunityaccess__completedmodule__date__gte=three_days_ago),
            distinct=True,
        ),
        deliveries_from_yesterday=Count(
            "uservisit",
            filter=Q(uservisit__completed_work__isnull=False, uservisit__visit_date__gte=yesterday),
            distinct=True,
        ),
        most_recent_delivery=Max("uservisit__visit_date", filter=Q(uservisit__completed_work__isnull=False)),
        total_deliveries=Count("opportunityaccess__completedwork", distinct=True),
        flagged_deliveries_waiting_for_review=Count(
            "opportunityaccess__completedwork",
            filter=Q(opportunityaccess__completedwork__status=CompletedWorkStatus.pending),
            distinct=True,
        ),
        visits_pending_for_pm_review=Count(
            "uservisit",
            filter=Q(uservisit__review_status=VisitReviewStatus.pending)
            & Q(uservisit__review_created_on__isnull=False),
        ),
        recent_payment=Max("opportunityaccess__payment__date_paid"),
        total_accrued=Coalesce(
            Sum("opportunityaccess__payment_accrued", distinct=True), Value(0), output_field=DecimalField()
        ),
        total_paid=Coalesce(
            Sum("opportunityaccess__payment__amount", distinct=True), Value(0), output_field=DecimalField()
        ),
    )
    aggregates["payments_due"] = aggregates["total_accrued"] - aggregates["total_paid"]

    invites = UserInvite.objects.filter(opportunity_id=opp_id).aggregate(
        workers_invited=Count("id"), pending_invites=Count("id", filter=~Q(status=UserInviteStatus.accepted))
    )

    return {**invites, **aggregates}


def get_opportunity_worker_progress(opp_id):
    today = now().date()
    opportunity = Opportunity.objects.filter(id=opp_id).values("start_date", "end_date", "total_budget").first()

    aggregates = Opportunity.objects.filter(id=opp_id).aggregate(
        total_deliveries=Count("opportunityaccess__completedwork", distinct=True),
        approved_deliveries=Count(
            "opportunityaccess__completedwork",
            filter=Q(opportunityaccess__completedwork__status=CompletedWorkStatus.approved),
            distinct=True,
        ),
        rejected_deliveries=Count(
            "opportunityaccess__completedwork",
            filter=Q(opportunityaccess__completedwork__status=CompletedWorkStatus.rejected),
            distinct=True,
        ),
        total_accrued=Coalesce(
            Sum("opportunityaccess__payment_accrued", distinct=True), Value(0), output_field=DecimalField()
        ),
        total_paid=Coalesce(
            Sum("opportunityaccess__payment__amount_usd", distinct=True), Value(0), output_field=DecimalField()
        ),
        total_visits=Count("uservisit", distinct=True),
    )
    aggregates.update(opportunity)

    start_date = aggregates["start_date"]
    end_date = aggregates["end_date"] or today
    effective_end_date = min(end_date, today)

    total_days = max((effective_end_date - start_date).days, 1)

    max_visits_qs = (
        UserVisit.objects.filter(opportunity_id=opp_id)
        .annotate(visit_day=TruncDate("visit_date"))
        .values("visit_day")
        .annotate(day_count=Count("id"))
        .order_by("-day_count")
        .values_list("day_count", flat=True)
    )

    maximum_visit_in_a_day = max_visits_qs.first() or 0

    aggregates["total_days"] = total_days
    aggregates["average_visits_per_day"] = (
        round(float(aggregates["total_visits"]) / total_days, 1) if aggregates["total_visits"] and total_days else 0
    )

    aggregates["maximum_visit_in_a_day"] = maximum_visit_in_a_day

    return aggregates


def get_opportunity_funnel_progress(opp_id):
    completed_user_ids = (
        CompletedModule.objects.filter(opportunity=OuterRef("pk"))
        .values("user")
        .annotate(
            completed_modules=Count("module", distinct=True),
            total_modules=Subquery(
                LearnModule.objects.filter(app=OuterRef("opportunity__learn_app"))
                .values("app")
                .annotate(count=Count("id"))
                .values("count")[:1]
            ),
        )
        .filter(completed_modules=F("total_modules"))
        .values("user")
    )

    aggregates = Opportunity.objects.filter(id=opp_id).aggregate(
        workers_invited=Count("userinvite", distinct=True),
        pending_invites=Count(
            "userinvite",
            filter=~Q(userinvite__status=UserInviteStatus.accepted),
            distinct=True,
        ),
        started_learning_count=Count(
            "opportunityaccess__user", filter=Q(opportunityaccess__date_learn_started__isnull=False), distinct=True
        ),
        claimed_job=Count("opportunityaccess__opportunityclaim", distinct=True),
        started_deliveries=Count("uservisit__user", distinct=True),
        completed_assessments=Count("assessment__user", filter=Q(assessment__passed=True), distinct=True),
        completed_learning=Count(
            "opportunityaccess__user",
            filter=Q(opportunityaccess__user__in=Subquery(completed_user_ids)),
            distinct=True,
        ),
    )

    return aggregates
