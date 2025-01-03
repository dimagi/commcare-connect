import datetime

from django.db.models import Case, Count, IntegerField, Q, Sum, When
from django.db.models.functions import TruncDate

from commcare_connect.opportunity.models import UserVisit


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
    def get_data(cls, opportunity_ids=None, date_gte=None, date_lte=None):
        data = {
            "summary": cls.get_summary(opportunity_ids, date_gte, date_lte),
            "time_series": cls.get_time_series(opportunity_ids, date_gte, date_lte),
        }
        if date_gte and date_gte == date_lte:
            data["summary_lastday"] = {}
        else:
            last_day = date_lte or datetime.date.today()
            data["summary_lastday"] = cls.get_summary(opportunity_ids, last_day - datetime.timedelta(days=1), last_day)
        return data

    @classmethod
    def get_summary(cls, opportunity_ids=None, date_gte=None, date_lte=None):
        queryset = cls._get_queryset(opportunity_ids, date_gte, date_lte)
        return queryset.aggregate(**cls._get_aggregations())

    @classmethod
    def get_time_series(cls, opportunity_ids=None, date_gte=None, date_lte=None):
        queryset = cls._get_queryset(opportunity_ids, date_gte, date_lte)
        return list(
            queryset.annotate(visit_date_only=TruncDate("visit_date"))
            .values("visit_date_only")
            .annotate(**cls._get_aggregations())
        )
