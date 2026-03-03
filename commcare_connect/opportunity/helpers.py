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
    UUIDField,
    Value,
    When,
)
from django.db.models.functions import Coalesce, Round
from django.utils.timezone import now

from commcare_connect.opportunity.models import (
    Assessment,
    CompletedModule,
    CompletedWork,
    CompletedWorkStatus,
    Opportunity,
    OpportunityAccess,
    OpportunityClaim,
    OpportunityClaimLimit,
    Payment,
    UserInvite,
    UserInviteStatus,
    UserVisit,
    VisitReviewStatus,
    VisitValidationStatus,
)


def inactive_workers_subquery(days_ago):
    subquery = (
        OpportunityAccess.objects.filter(
            opportunity_id=OuterRef("pk"),
            last_active__isnull=False,
            last_active__lt=days_ago,
        )
        .values("opportunity_id")
        .annotate(count=Count("id", distinct=True))
        .values("count")
    )

    return Coalesce(Subquery(subquery, output_field=IntegerField()), Value(0))


def get_deliveries_count_subquery(status=None):
    filters = {"opportunity_access__opportunity_id": OuterRef("pk")}
    if status is not None:
        filters["status"] = status

    return Coalesce(
        Subquery(
            CompletedWork.objects.filter(**filters)
            .values("opportunity_access__opportunity_id")
            .annotate(count=Count("id", distinct=True))
            .values("count"),
            output_field=IntegerField(),
        ),
        0,
    )


def total_accrued_sq():
    return Coalesce(
        Subquery(
            OpportunityAccess.objects.filter(opportunity_id=OuterRef("pk"))
            .values("opportunity_id")
            .annotate(total=Sum("payment_accrued"))
            .values("total"),
            output_field=IntegerField(),
        ),
        0,
    )


def total_paid_sq():
    return Coalesce(
        Subquery(
            Payment.objects.filter(opportunity_access__opportunity_id=OuterRef("pk"))
            .values("opportunity_access__opportunity_id")
            .annotate(total=Sum("amount"))
            .values("total"),
            output_field=DecimalField(),
        ),
        Value(0, output_field=DecimalField()),
    )


def deliveries_from_yesterday_sq():
    return Coalesce(
        Subquery(
            UserVisit.objects.filter(opportunity_id=OuterRef("pk"), visit_date__gte=now().date() - timedelta(days=1))
            .values("opportunity_id")
            .annotate(count=Count("id", distinct=True))
            .values("count"),
            output_field=IntegerField(),
        ),
        0,
    )


def workers_invited_subquery():
    return Coalesce(
        Subquery(
            UserInvite.objects.filter(opportunity_id=OuterRef("pk"))
            .exclude(status=UserInviteStatus.not_found)
            .values("opportunity_id")
            .annotate(count=Count("id", distinct=True))
            .values("count"),
            output_field=IntegerField(),
        ),
        0,
    )


def pending_invites_subquery():
    return Coalesce(
        Subquery(
            UserInvite.objects.filter(opportunity_id=OuterRef("pk"))
            .exclude(status__in=[UserInviteStatus.not_found, UserInviteStatus.accepted])
            .values("opportunity_id")
            .annotate(count=Count("id", distinct=True))
            .values("count"),
            output_field=IntegerField(),
        ),
        0,
    )


def started_learning_subquery():
    return Coalesce(
        Subquery(
            OpportunityAccess.objects.filter(opportunity_id=OuterRef("pk"), date_learn_started__isnull=False)
            .values("opportunity_id")
            .annotate(count=Count("id", distinct=True))
            .values("count"),
            output_field=IntegerField(),
        ),
        Value(0),
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


def get_annotated_opportunity_access_deliver_status(opportunity: Opportunity, filters: dict):
    filters = filters or {}
    access_objects = []
    for payment_unit in opportunity.paymentunit_set.all():
        queryset = OpportunityAccess.objects.filter(opportunity=opportunity, accepted=True)
        if last_active_days := filters.get("last_active"):
            last_active_date = now() - timedelta(days=int(last_active_days))
            queryset = queryset.filter(last_active__lt=last_active_date)

        total_visits_sq = Subquery(
            OpportunityClaimLimit.objects.filter(
                opportunity_claim__opportunity_access_id=OuterRef("pk"), payment_unit=payment_unit
            ).values("max_visits")[:1],
            output_field=IntegerField(),
        )

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
                CompletedWork.objects.filter(opportunity_access_id=OuterRef("pk"), status=status_value)
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

        total_pending_for_user = completed_work_status_total_subquery(CompletedWorkStatus.pending)
        total_approved_for_user = completed_work_status_total_subquery(CompletedWorkStatus.approved)
        total_rejected_for_user = completed_work_status_total_subquery(CompletedWorkStatus.rejected)
        total_over_limit_for_user = completed_work_status_total_subquery(CompletedWorkStatus.over_limit)

        queryset = queryset.annotate(
            payment_unit_id=Value(payment_unit.pk, output_field=IntegerField()),
            payment_unit_payment_unit_id=Value(payment_unit.payment_unit_id, output_field=UUIDField()),
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

        queryset = (
            queryset.annotate(
                completed=(F("pending") + F("approved") + F("rejected") + F("over_limit")),
                total_completed=(
                    F("total_pending") + F("total_approved") + F("total_rejected") + F("total_over_limit")
                ),
            )
            .select_related("user")
            .order_by("user__name")
        )

        if filters.get("has_duplicates", None) is not None:
            has_duplicates = filters["has_duplicates"]
            if has_duplicates:
                queryset = queryset.filter(duplicate__gt=0)
            else:
                queryset = queryset.filter(duplicate=0)
        if filters.get("has_overlimit", None) is not None:
            has_overlimit = filters["has_overlimit"]
            if has_overlimit:
                queryset = queryset.filter(over_limit__gt=0)
            else:
                queryset = queryset.filter(over_limit=0)
        if filters.get("review_pending", None) is not None:
            review_pending = filters["review_pending"]
            if review_pending:
                queryset = queryset.filter(pending__gt=0)
            else:
                queryset = queryset.filter(pending=0)

        if filters.get("has_flags", None) not in (None, ""):
            flagged_visits_sq = UserVisit.objects.filter(
                deliver_unit__payment_unit=payment_unit, opportunity_access_id=OuterRef("pk"), flagged=True
            )
            queryset = queryset.annotate(has_flags=Exists(flagged_visits_sq)).filter(has_flags=filters["has_flags"])

        access_objects += queryset
    access_objects.sort(key=lambda a: a.user.name)
    return access_objects


def get_payment_report_data(opportunity: Opportunity, usd=False):
    PaymentReportData = namedtuple(
        "PaymentReportData", ["payment_unit", "approved", "user_payment_accrued", "nm_payment_accrued"]
    )

    accrued_attr = "saved_payment_accrued_usd" if usd else "saved_payment_accrued"
    org_accrued_attr = "saved_org_payment_accrued_usd" if usd else "saved_org_payment_accrued"
    report_data_qs = (
        CompletedWork.objects.filter(
            opportunity_access__opportunity=opportunity,
            status=CompletedWorkStatus.approved,
        )
        .values("payment_unit__name")
        .annotate(
            approved=Count("id"),
            user_payment_accrued=Sum(accrued_attr),
            nm_payment_accrued=Sum(org_accrued_attr),
        )
        .order_by("payment_unit__name")
    )

    data = []
    total_user_payment_accrued = 0
    total_nm_payment_accrued = 0

    for group in report_data_qs:
        user_payment = group["user_payment_accrued"] or 0
        nm_payment = group["nm_payment_accrued"] or 0

        total_user_payment_accrued += user_payment
        total_nm_payment_accrued += nm_payment

        data.append(
            PaymentReportData(
                group["payment_unit__name"],
                group["approved"],
                user_payment,
                nm_payment,
            )
        )

    return data, total_user_payment_accrued, total_nm_payment_accrued


class TieredQueryset:
    # This queryset enables targetted count, order_by, pagination
    #   for large querysets on which it may not be efficient to run
    #   them.
    #
    # This works by passing a base_queryset that just returns ids
    #   and data_qs_fn which returns the data for those ids
    def __init__(self, base_qs, data_qs_fn):
        self.base_qs = base_qs
        self.data_qs_fn = data_qs_fn
        self._ordered_qs = base_qs  # default unless order_by is called

    def count(self):
        return self.base_qs.count()

    def __getitem__(self, key):
        if isinstance(key, slice):
            page_qs = self._ordered_qs[key]
            ids = list(page_qs.values_list("id", flat=True))
            return list(self.data_qs_fn(ids))
        else:
            obj = self._ordered_qs[key]
            result = list(self.data_qs_fn([obj.id]))
            return result[0]

    def __iter__(self):
        return iter(self[:])

    def order_by(self, *fields):
        self._ordered_qs = self.base_qs.order_by(*fields)
        return self


class OpportunityData:
    def __init__(self, org, is_program_manager, filters):
        self.org = org
        self.is_program_manager = is_program_manager
        self.filters = filters

    def get_data(self):
        base_qs = self.get_base_qs(self.org, self.filters, self.is_program_manager)

        def data_qs(ids):
            return self.get_data_qs(ids, self.is_program_manager)

        return TieredQueryset(base_qs, data_qs)

    @staticmethod
    def get_base_qs(organization, filters, program_manager=False):
        today = now().date()
        base_filter = Q(organization=organization)
        if program_manager:
            base_filter |= Q(managedopportunity__program__organization=organization)
        is_test = filters.get("is_test", None)
        if is_test not in ["", None]:
            base_filter &= Q(is_test=is_test)
        programs = filters.get("program", [])
        if programs:
            base_filter &= Q(managedopportunity__program__slug__in=programs)
        queryset = Opportunity.objects.filter(base_filter).annotate(
            program=F("managedopportunity__program__name"),
            status=Case(
                When(Q(active=True) & Q(end_date__gte=today), then=Value(0)),  # Active
                When(Q(active=True) & Q(end_date__lt=today), then=Value(1)),  # Ended
                default=Value(2),  # Inactive
                output_field=IntegerField(),
            ),
        )
        status = filters.get("status", [])
        if status:
            queryset = queryset.filter(status__in=status)
        return queryset

    @staticmethod
    def get_data_qs(opp_ids, program_manager=False):
        # Meant to be used with a small page of opp_ids (10/20)
        today = now().date()
        three_days_ago = now() - timedelta(days=3)

        pending_approvals_sq = Subquery(
            CompletedWork.objects.filter(
                opportunity_access__opportunity_id=OuterRef("pk"),
            )
            .filter(
                Q(uservisit__status=VisitValidationStatus.pending)  # NM reviews
                | Q(
                    uservisit__review_status=VisitReviewStatus.pending, uservisit__review_created_on__isnull=False
                ),  # PM reviews
            )
            .values("opportunity_access__opportunity_id")
            .annotate(count=Count("id", distinct=True))
            .values("count")[:1],
            output_field=IntegerField(),
        )

        queryset = Opportunity.objects.filter(id__in=opp_ids).annotate(
            program=F("managedopportunity__program__name"),
            pending_invites=pending_invites_subquery(),
            pending_approvals=Coalesce(pending_approvals_sq, Value(0)),
            total_accrued=total_accrued_sq(),
            total_paid=total_paid_sq(),
            payments_due=ExpressionWrapper(
                F("total_accrued") - F("total_paid"),
                output_field=DecimalField(),
            ),
            inactive_workers=inactive_workers_subquery(three_days_ago),
            status=Case(
                When(Q(active=True) & Q(end_date__gte=today), then=Value(0)),  # Active
                When(Q(active=True) & Q(end_date__lt=today), then=Value(1)),  # Ended
                default=Value(2),  # Inactive
                output_field=IntegerField(),
            ),
        )

        if program_manager:
            deliveries_agg = (
                CompletedWork.objects.filter(opportunity_access__opportunity_id__in=opp_ids)
                .values("opportunity_access__opportunity_id")
                .annotate(
                    total_deliveries=Count("id", distinct=True),
                    verified_deliveries=Count("id", filter=Q(status=CompletedWorkStatus.approved), distinct=True),
                )
            )
            deliveries_by_opp = {
                item["opportunity_access__opportunity_id"]: {
                    "total_deliveries": item["total_deliveries"],
                    "verified_deliveries": item["verified_deliveries"],
                }
                for item in deliveries_agg
            }

            workers_agg = (
                OpportunityAccess.objects.filter(opportunity_id__in=opp_ids)
                .values("opportunity_id")
                .annotate(
                    total_workers=Count("id", distinct=True),
                    started_learning=Count("id", filter=Q(date_learn_started__isnull=False), distinct=True),
                )
            )
            workers_by_opp = {
                item["opportunity_id"]: {
                    "total_workers": item["total_workers"],
                    "started_learning": item["started_learning"],
                }
                for item in workers_agg
            }

            for opp in queryset:
                delivery_stats = deliveries_by_opp.get(opp.id, {})
                worker_stats = workers_by_opp.get(opp.id, {})
                opp.total_deliveries = delivery_stats.get("total_deliveries", 0)
                opp.verified_deliveries = delivery_stats.get("verified_deliveries", 0)
                opp.total_workers = worker_stats.get("total_workers", 0)
                opp.started_learning = worker_stats.get("started_learning", 0)
                opp.active_workers = opp.started_learning - opp.inactive_workers

        # preserve the order of opp_ids argument
        qs_by_id = {opp.id: opp for opp in queryset}
        return [qs_by_id[oid] for oid in opp_ids if oid in qs_by_id]


def get_worker_table_data(opportunity):
    return (
        UserInvite.objects.filter(opportunity=opportunity)
        .annotate(
            days_to_complete_learn=ExpressionWrapper(
                F("opportunity_access__completed_learn_date") - F("opportunity_access__date_learn_started"),
                output_field=DurationField(),
            ),
            first_delivery=Min(
                "opportunity_access__uservisit__visit_date",
            ),
            days_to_start_delivery=Case(
                When(
                    opportunity_access__date_learn_started__isnull=False,
                    first_delivery__isnull=False,
                    then=ExpressionWrapper(
                        F("first_delivery") - F("opportunity_access__date_learn_started"), output_field=DurationField()
                    ),
                ),
                default=None,
                output_field=DurationField(),
            ),
        )
        .select_related("opportunity_access", "opportunity_access__user")
    )


def get_worker_learn_table_data(opportunity):
    learn_modules_count = opportunity.learn_app.learn_modules.count()

    def assessment_exists_subquery(passed: bool):
        return Assessment.objects.filter(opportunity_access_id=OuterRef("pk"), passed=passed)

    duration_subquery = (
        CompletedModule.objects.filter(opportunity_access=OuterRef("pk"))
        .values("opportunity_access")
        .annotate(total_duration=Sum("duration"))
        .values("total_duration")[:1]
    )
    queryset = OpportunityAccess.objects.filter(opportunity=opportunity, accepted=True).annotate(
        completed_modules_count=Count("completedmodule__module", distinct=True),
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
        ),
    )
    return queryset


def get_opportunity_delivery_progress(opp_id):
    today = now().replace(hour=0, minute=0, second=0, microsecond=0)
    three_days_ago = today - timedelta(days=3)
    yesterday = today - timedelta(days=1)

    accrued_since_yesterday_sq = Coalesce(
        Subquery(
            CompletedWork.objects.filter(
                opportunity_access__opportunity_id=OuterRef("pk"),
                status_modified_date__gte=yesterday,
                status=CompletedWorkStatus.approved,
            )
            .values("opportunity_access__opportunity_id")
            .annotate(total=Sum("saved_payment_accrued"))
            .values("total"),
            output_field=IntegerField(),
        ),
        0,
    )

    most_recent_delivery_sq = Subquery(
        UserVisit.objects.filter(opportunity_id=OuterRef("pk"))
        .values("opportunity_id")
        .annotate(latest=Max("visit_date"))
        .values("latest")[:1],
        output_field=DateTimeField(),
    )

    flagged_deliveries_waiting_review_sq = Coalesce(
        Subquery(
            CompletedWork.objects.filter(
                opportunity_access__opportunity_id=OuterRef("pk"),
                uservisit__status=VisitValidationStatus.pending,
            )
            .values("opportunity_access__opportunity_id")
            .annotate(count=Count("id", distinct=True))
            .values("count"),
            output_field=IntegerField(),
        ),
        0,
    )

    flagged_deliveries_since_yesterday_sq = Coalesce(
        Subquery(
            CompletedWork.objects.filter(
                opportunity_access__opportunity_id=OuterRef("pk"),
                uservisit__status=VisitValidationStatus.pending,
                uservisit__visit_date__gte=yesterday,
            )
            .values("opportunity_access__opportunity_id")
            .annotate(count=Count("id", distinct=True))
            .values("count"),
            output_field=IntegerField(),
        ),
        0,
    )

    deliveries_pending_pm_sq = Coalesce(
        Subquery(
            CompletedWork.objects.filter(
                opportunity_access__opportunity_id=OuterRef("pk"),
                uservisit__review_status=VisitReviewStatus.pending,
                uservisit__status=VisitValidationStatus.approved,
                uservisit__review_created_on__isnull=False,
            )
            .values("opportunity_access__opportunity_id")
            .annotate(count=Count("id", distinct=True))
            .values("count"),
            output_field=IntegerField(),
        ),
        0,
    )

    deliveries_pending_pm_yesterday_sq = Coalesce(
        Subquery(
            CompletedWork.objects.filter(
                opportunity_access__opportunity_id=OuterRef("pk"),
                uservisit__review_status=VisitReviewStatus.pending,
                uservisit__review_created_on__isnull=False,
                uservisit__status=VisitValidationStatus.approved,
                uservisit__review_created_on__gte=yesterday,
            )
            .values("opportunity_access__opportunity_id")
            .annotate(count=Count("id", distinct=True))
            .values("count"),
            output_field=IntegerField(),
        ),
        0,
    )

    recent_payment_sq = Subquery(
        Payment.objects.filter(opportunity_access__opportunity_id=OuterRef("pk"))
        .values("opportunity_access__opportunity_id")
        .annotate(latest=Max("date_paid"))
        .values("latest")[:1],
        output_field=DateTimeField(),
    )

    annotated_opportunity = Opportunity.objects.filter(id=opp_id).annotate(
        inactive_workers=inactive_workers_subquery(three_days_ago),
        deliveries_from_yesterday=deliveries_from_yesterday_sq(),
        accrued_since_yesterday=accrued_since_yesterday_sq,
        most_recent_delivery=most_recent_delivery_sq,
        total_deliveries=get_deliveries_count_subquery(),
        flagged_deliveries_waiting_for_review=flagged_deliveries_waiting_review_sq,
        flagged_deliveries_waiting_for_review_since_yesterday=flagged_deliveries_since_yesterday_sq,
        deliveries_pending_for_pm_review=deliveries_pending_pm_sq,
        deliveries_pending_for_pm_review_since_yesterday=deliveries_pending_pm_yesterday_sq,
        recent_payment=recent_payment_sq,
        workers_invited=workers_invited_subquery(),
        pending_invites=pending_invites_subquery(),
        total_accrued=total_accrued_sq(),
        total_paid=total_paid_sq(),
        payments_due=ExpressionWrapper(F("total_accrued") - F("total_paid"), output_field=DecimalField()),
    )

    return annotated_opportunity.first()


def get_opportunity_worker_progress(opp_id):
    return (
        Opportunity.objects.filter(id=opp_id)
        .annotate(
            total_deliveries=get_deliveries_count_subquery(),
            approved_deliveries=get_deliveries_count_subquery(CompletedWorkStatus.approved),
            rejected_deliveries=get_deliveries_count_subquery(CompletedWorkStatus.rejected),
            total_accrued=total_accrued_sq(),
            total_paid=total_paid_sq(),
            visits_since_yesterday=deliveries_from_yesterday_sq(),
        )
        .first()
    )


def get_opportunity_funnel_progress(opp_id):
    claimed_job_subquery = Coalesce(
        Subquery(
            OpportunityClaim.objects.filter(opportunity_access__opportunity_id=OuterRef("pk"))
            .values("opportunity_access__opportunity_id")
            .annotate(count=Count("id", distinct=True))
            .values("count"),
            output_field=IntegerField(),
        ),
        Value(0),
    )

    started_deliveries_subquery = Coalesce(
        Subquery(
            UserVisit.objects.filter(opportunity_id=OuterRef("pk"))
            .values("opportunity_id")
            .annotate(count=Count("user_id", distinct=True))
            .values("count"),
            output_field=IntegerField(),
        ),
        Value(0),
    )

    completed_assessments_subquery = Coalesce(
        Subquery(
            Assessment.objects.filter(opportunity_id=OuterRef("pk"), passed=True)
            .values("opportunity_id")
            .annotate(count=Count("user_id", distinct=True))
            .values("count"),
            output_field=IntegerField(),
        ),
        Value(0),
    )

    completed_learning_subquery = Coalesce(
        Subquery(
            OpportunityAccess.objects.filter(opportunity_id=OuterRef("pk"), completed_learn_date__isnull=False)
            .values("opportunity_id")
            .annotate(count=Count("user_id", distinct=True))
            .values("count"),
            output_field=IntegerField(),
        ),
        Value(0),
    )

    return (
        Opportunity.objects.filter(id=opp_id)
        .annotate(
            workers_invited=workers_invited_subquery(),
            pending_invites=pending_invites_subquery(),
            started_learning_count=started_learning_subquery(),
            claimed_job=claimed_job_subquery,
            started_deliveries=started_deliveries_subquery,
            completed_assessments=completed_assessments_subquery,
            completed_learning=completed_learning_subquery,
        )
        .first()
    )
