from collections import namedtuple
from datetime import timedelta

from django.db.models import (
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
        started_delivery_sq = Subquery(
            UserVisit.objects.filter(opportunity_access_id=OuterRef("pk"))
            .values("opportunity_access_id")
            .annotate(min_visit_date=Min("visit_date"))
            .values("min_visit_date")[:1],
            output_field=DateTimeField(null=True)
        )

        last_visit_sq = Subquery(
            UserVisit.objects.filter(opportunity_access_id=OuterRef("pk"))
            .values("opportunity_access_id")
            .annotate(max_visit_date=Max("visit_date"))
            .values("max_visit_date")[:1],
            output_field=DateTimeField(null=True)
        )

        last_module_sq = Subquery(
            CompletedModule.objects.filter(opportunity_access_id=OuterRef("pk"))
            .values("opportunity_access_id")
            .annotate(max_module_date=Max("date"))
            .values("max_module_date")[:1],
            output_field=DateTimeField(null=True)
        )

        def completed_work_status_subquery(status_value):
            return Subquery(
                CompletedWork.objects.filter(
                    opportunity_access_id=OuterRef("pk"),
                    payment_unit=payment_unit,
                    status=status_value
                )
                .values("opportunity_access_id")
                .annotate(status_count=Count("id", distinct=True))
                .values("status_count")[:1],
                output_field=IntegerField()
            )

        pending_count_sq = completed_work_status_subquery(CompletedWorkStatus.pending)
        approved_count_sq = completed_work_status_subquery(CompletedWorkStatus.approved)
        rejected_count_sq = completed_work_status_subquery(CompletedWorkStatus.rejected)
        over_limit_count_sq = completed_work_status_subquery(CompletedWorkStatus.over_limit)
        incomplete_count_sq = completed_work_status_subquery(CompletedWorkStatus.incomplete)

        queryset = (
            OpportunityAccess.objects.filter(opportunity=opportunity)
            .annotate(
                payment_unit=Value(payment_unit.name, output_field=CharField()),
                started_delivery=Coalesce(started_delivery_sq, Value(None, output_field=DateTimeField())),
                _last_visit_val=Coalesce(last_visit_sq, Value(None, output_field=DateTimeField())),
                _last_module_val=Coalesce(last_module_sq, Value(None, output_field=DateTimeField())),
                pending=Coalesce(pending_count_sq, Value(0)),
                approved=Coalesce(approved_count_sq, Value(0)),
                rejected=Coalesce(rejected_count_sq, Value(0)),
                over_limit=Coalesce(over_limit_count_sq, Value(0)),
                incomplete=Coalesce(incomplete_count_sq, Value(0)),
            )
            .annotate(
                last_active=Greatest(
                    F('_last_visit_val'),
                    F('_last_module_val'),
                    F('date_learn_started')
                ),
                completed=(
                    F('pending') + F('approved') + F('rejected') + F('over_limit')
                )
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
            verified_deliveries=Sum("opportunityaccess__completedwork__saved_approved_count", distinct=True)
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
