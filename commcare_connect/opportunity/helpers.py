from collections import namedtuple
from datetime import timedelta

from django.db.models import  DurationField
from django.db.models.functions import Greatest, Least, Now, Round
from django.db.models import Count, Q, Sum, F, Case, When, Value, IntegerField, DecimalField, ExpressionWrapper, Exists, \
    OuterRef, Subquery, DateField, FloatField, QuerySet
from django.db.models import Max, Min
from django.db.models.functions import Coalesce, TruncDate, Extract
from django.utils.timezone import now

from commcare_connect.opportunity.models import (
    CompletedWork,
    CompletedWorkStatus,
    Opportunity,
    OpportunityAccess,
    PaymentUnit,
    UserInvite,
    Assessment,
    VisitValidationStatus, UserInviteStatus, UserVisit, CompletedModule, LearnModule,
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
                started_delivery=Min(
                    "completedwork",
                    filter=Q(completedwork__uservisit__isnull=False) & Q(
                        completedwork__payment_unit=payment_unit)
                ),
                last_active=Max("completedwork", filter=Q(completedwork__uservisit__isnull=False) & Q(
                    completedwork__payment_unit=payment_unit)),
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
                flagged=Count(
                    "uservisit",
                    filter=Q(uservisit__flagged=True),
                    distinct=True,
                ),
                completed=F("approved") + F("rejected") + F("pending") + F("over_limit"),
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


def get_worker_table_data(opportunity):
    learn_modules_count = opportunity.learn_app.learn_modules.count()

    min_dates_per_module = CompletedModule.objects.filter(
        opportunity_access=OuterRef('pk')
    ).values('module').annotate(
        min_date=Min('date')
    ).values('min_date')

    queryset = OpportunityAccess.objects.filter(opportunity=opportunity).annotate(
        last_active=Greatest(Max("uservisit__visit_date"), Max("completedmodule__date")),
        started_learn=Min("completedmodule__date"),
        completed_modules_count=Count(
            "completedmodule__module",
            distinct=True,
        ),
        completed_learn=Case(
            When(
                Q(completed_modules_count=learn_modules_count),
                then=Subquery(
                    min_dates_per_module.order_by('-min_date')[:1]
                )
            ),
            default=None,
        ),
        days_to_complete_learn=ExpressionWrapper(
            F("completed_learn") - F("started_learn"),
            output_field=DurationField(),
        ),
        first_delivery=Min(
            "uservisit__visit_date",
        ),
        days_to_start_delivery=ExpressionWrapper(
            Coalesce(
                Least(Now(), Value(opportunity.end_date)),
                Now()
            ) - F("first_delivery"),
            output_field=DurationField()
        ),

    )

    return queryset


def get_worker_learn_table_data(opportunity):
    learn_modules_count = opportunity.learn_app.learn_modules.count()
    min_dates_per_module = CompletedModule.objects.filter(
        opportunity_access=OuterRef('pk')
    ).values('module').annotate(
        min_date=Min('date')
    ).values('min_date')

    assessments_qs = Assessment.objects.filter(
        user=OuterRef("user"),
        opportunity=OuterRef("opportunity"),
        passed=True
    )

    queryset = OpportunityAccess.objects.filter(opportunity=opportunity).annotate(
        last_active=Max("completedmodule__date"),
        started_learning=Max("completedmodule__date"),
        completed_modules_count=Count("completedmodule__module", distinct=True),
        completed_learn=Case(
            When(
                Q(completed_modules_count=learn_modules_count),
                then=Subquery(
                    min_dates_per_module.order_by('-min_date')[:1]
                )
            ),
            default=None,
        ),
        passed_assessment=Exists(assessments_qs),
        assesment_count=Count("assessment", distinct=True),
        learning_hours=Sum("completedmodule__duration", distinct=True),
        modules_completed_percentage=Round(
            ExpressionWrapper(
                F("completed_modules_count") * 100.0 / learn_modules_count,
                output_field=FloatField()
            ),
            1
        )
    )
    return queryset


def get_opportunity_list_data(organization):
    today = now().date()
    three_days_ago = now() - timedelta(days=3)

    queryset = Opportunity.objects.filter(organization=organization).annotate(
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
            Sum('opportunityaccess__payment_accrued', distinct=True),
            Value(0),
            output_field=DecimalField()
        ),
        total_paid=Coalesce(
            Sum(
                'opportunityaccess__payment__amount_usd',
                filter=Q(opportunityaccess__payment__confirmed=True),
                distinct=True
            ),
            Value(0),
            output_field=DecimalField()
        ),
        payments_due=ExpressionWrapper(
            F('total_accrued') - F('total_paid'),
            output_field=DecimalField(),
        ),

        inactive_workers=Count(
            "opportunityaccess",
            filter=Q(
                ~Exists(
                    UserVisit.objects.filter(
                        opportunity_access=OuterRef('opportunityaccess'),
                        visit_date__gte=three_days_ago,
                    )
                )
            ),
            distinct=True,
        ),
        status=Case(
            When(Q(active=True) & Q(end_date__gte=today), then=Value(0)),  # Active
            When(Q(active=True) & Q(end_date__lt=today), then=Value(1)),  # Ended
            default=Value(2),  # Inactive
            output_field=IntegerField()
        )
    )

    return queryset


def get_opportunity_dashboard_data(opp_id: int, org_slug=None) -> QuerySet[Opportunity]:
    today = now()
    three_days_ago = today - timedelta(days=3)
    yesterday = today - timedelta(days=1)


    completed_user_ids = CompletedModule.objects.filter(
        opportunity=OuterRef("pk")
    ).values("user").annotate(
        completed_modules=Count("module", distinct=True),
        total_modules=Subquery(
            LearnModule.objects.filter(app=OuterRef("opportunity__learn_app"))
            .values("app")
            .annotate(count=Count("id"))
            .values("count")[:1]
        )
    ).filter(
        completed_modules=F("total_modules")
    ).values("user")


    started_users_subquery = CompletedModule.objects.filter(
        opportunity=OuterRef("pk")
    ).values("user").distinct()


    effective_end_date = Case(
        When(end_date__isnull=True, then=Value(today)),
        When(end_date__gt=today, then=Value(today)),
        default=F('end_date'),
        output_field=DateField()
    )


    queryset = Opportunity.objects.annotate(
        workers_invited=Count("userinvite", distinct=True),
        pending_invites=Count(
            "userinvite",
            filter=~Q(userinvite__status=UserInviteStatus.accepted),
            distinct=True,
        ),
        inactive_workers=Count(
            "opportunityaccess",
            filter=Q(
                ~Exists(
                    UserVisit.objects.filter(
                        opportunity_access=OuterRef('opportunityaccess'),
                        visit_date__gte=three_days_ago,
                    )
                )
            ),
            distinct=True,
        ),
        total_accrued=Coalesce(
            Sum('opportunityaccess__payment_accrued', distinct=True),
            Value(0),
            output_field=DecimalField()
        ),
        total_paid=Coalesce(
            Sum(
                'opportunityaccess__payment__amount_usd',
                distinct=True
            ),
            Value(0),
            output_field=DecimalField()
        ),
        payments_due=ExpressionWrapper(
            F('total_accrued') - F('total_paid'),
            output_field=DecimalField(),
        ),
        total_deliveries=Count("opportunityaccess__completedwork", distinct=True),
        approved_deliveries=Count("opportunityaccess__completedwork",
                                  filter=Q(opportunityaccess__completedwork__status=CompletedWorkStatus.approved),
                                  distinct=True),
        rejected_deliveries=Count("opportunityaccess__completedwork",
                                  filter=Q(opportunityaccess__completedwork__status=CompletedWorkStatus.rejected),
                                  distinct=True),
        flagged_deliveries_waiting_for_review=Count(
            "opportunityaccess__completedwork",
            filter=Q(opportunityaccess__completedwork__status=CompletedWorkStatus.pending),
            distinct=True
        ),
        most_recent_delivery=Max(
            "uservisit__visit_date",
            filter=Q(uservisit__completed_work__isnull=False)
        ),
        deliveries_from_yesterday=Count(
            "uservisit",
            filter=Q(
                uservisit__completed_work__isnull=False,
                uservisit__visit_date__gte=yesterday
            ),
            distinct=True,
        ),
        started_learning_count=Count(
            'opportunityaccess__user',
            filter=Q(opportunityaccess__user__in=Subquery(started_users_subquery)),
            distinct=True
        ),
        completed_learning=Count(
            'opportunityaccess__user',
            filter=Q(opportunityaccess__user__in=Subquery(completed_user_ids)),
            distinct=True
        ),
        claimed_job=Count("opportunityaccess__opportunityclaim", distinct=True),
        started_deleveries=Count("uservisit__user", distinct=True),
        completed_assessments=Count(
            "assessment__user",
            filter=Q(assessment__passed=True),
            distinct=True
        ),
        recent_payment=Max("opportunityaccess__payment__date_paid"),
        maximum_visit_in_a_day=Subquery(
            UserVisit.objects.filter(
                opportunity=OuterRef('pk')
            ).annotate(
                visit_day=TruncDate('visit_date')
            ).values('visit_day')
            .annotate(
                day_count=Count('id')
            ).order_by('-day_count')
            .values('day_count')[:1]
        ),
        total_visits=Count('uservisit', distinct=True),
        total_days=ExpressionWrapper(
            Extract(effective_end_date, 'day') - Extract(F('start_date'), 'day'),
            output_field=IntegerField()
        ),
        average_visits_per_day=Case(
            When(total_days__gt=0, then=F('total_visits') / F('total_days')),
            default=Value(0),
            output_field=FloatField()
        )
    ).filter(id=opp_id)


    return queryset


