import datetime

from django.db.models import Case, Count, IntegerField, Q, Sum, When
from django.db.models.functions import TruncDate

from commcare_connect.opportunity.models import OpportunityAccess, UserVisit


class UserVisitData:
    @classmethod
    def _get_aggregations(cls):
        return {
            "total_visits": Count("id"),
            "flagged_visits": Sum(Case(When(flagged=True, then=1), default=0, output_field=IntegerField())),
            "approved_visits": Sum(Case(When(status="approved", then=1), default=0, output_field=IntegerField())),
            "rejected_visits": Sum(Case(When(status="rejected", then=1), default=0, output_field=IntegerField())),
            "approved_flagged_visits": Sum(
                Case(When(Q(status="approved") & Q(flagged=True), then=1), default=0, output_field=IntegerField())
            ),
            "duplicate_flags": Count(
                Case(When(flag_reason__flags__contains=["duplicate"], then=1), output_field=IntegerField())
            ),
            "gps_flags": Count(Case(When(flag_reason__flags__contains=["gps"], then=1), output_field=IntegerField())),
            "location_flags": Count(
                Case(When(flag_reason__flags__contains=["location"], then=1), output_field=IntegerField())
            ),
            "duration_flags": Count(
                Case(When(flag_reason__flags__contains=["duration"], then=1), output_field=IntegerField())
            ),
        }

    @classmethod
    def _get_queryset(cls, opportunity_ids=None, date_gte=None, date_lte=None):
        queryset = UserVisit.objects.all()
        if opportunity_ids:
            queryset = queryset.filter(opportunity_id__in=opportunity_ids)
        if date_gte:
            queryset = queryset.filter(visit_date__gte=date_gte)
        if date_lte:
            queryset = queryset.filter(visit_date__lte=date_lte)
        return queryset

    @classmethod
    def get_data(cls, filterset):
        queryset = UserVisit.objects.all()
        if filterset.is_valid():
            queryset = filterset.filter_queryset(queryset)
            from_date = filterset.form.cleaned_data["from_date"]
            to_date = filterset.form.cleaned_data["to_date"]
        else:
            to_date = datetime.now().date()
            from_date = to_date - datetime.timedelta(days=30)
            queryset = queryset.filter(visit_date__gte=from_date, visit_date__lte=to_date)

        data = {
            "summary": cls.get_summary(queryset),
            "time_series": cls.get_time_series(queryset),
            "funnel": cls.get_funnel_data(filterset),
        }
        return data

    @classmethod
    def get_summary(cls, queryset):
        return queryset.aggregate(**cls._get_aggregations())

    @classmethod
    def get_time_series(cls, queryset):
        return list(
            queryset.annotate(visit_date_only=TruncDate("visit_date"))
            .values("visit_date_only")
            .annotate(**cls._get_aggregations())
        )

    @classmethod
    def get_funnel_data(cls, filterset):
        """Gets funnel metrics from OpportunityAccess"""
        queryset = OpportunityAccess.objects.all()

        # No date filters for funnel data
        filterset.form.cleaned_data.pop("from_date", None)
        filterset.form.cleaned_data.pop("to_date", None)

        if filterset.is_valid():
            queryset = filterset.filter_queryset(queryset)

        metrics = queryset.aggregate(
            invited=Count("id", filter=Q(invited_date__isnull=False)),
            is_accepted=Count("id", filter=Q(accepted=True)),
            started_learning=Count("id", filter=Q(date_learn_started__isnull=False, accepted=True)),
        )

        return [
            {"value": metrics["invited"], "name": "Invited"},
            {"value": metrics["is_accepted"], "name": "Accepted"},
            {"value": metrics["started_learning"], "name": "Started Learning"},
        ]
