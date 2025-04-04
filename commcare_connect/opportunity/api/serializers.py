from django.conf import settings
from django.db.models import Sum
from rest_framework import serializers

from commcare_connect.cache import quickcache
from commcare_connect.opportunity.models import (
    Assessment,
    CatchmentArea,
    CommCareApp,
    CompletedModule,
    CompletedWork,
    CompletedWorkStatus,
    LearnModule,
    Opportunity,
    OpportunityAccess,
    OpportunityClaim,
    OpportunityClaimLimit,
    OpportunityVerificationFlags,
    Payment,
    PaymentUnit,
    UserVisit,
)


class LearnModuleSerializer(serializers.ModelSerializer):
    class Meta:
        model = LearnModule
        fields = ["slug", "name", "description", "time_estimate", "id"]


class CommCareAppSerializer(serializers.ModelSerializer):
    organization = serializers.SlugRelatedField(read_only=True, slug_field="slug")
    learn_modules = LearnModuleSerializer(many=True)
    install_url = serializers.SerializerMethodField()
    passing_score = serializers.SerializerMethodField()

    class Meta:
        model = CommCareApp
        fields = [
            "cc_domain",
            "cc_app_id",
            "name",
            "description",
            "organization",
            "learn_modules",
            "passing_score",
            "install_url",
            "id",
        ]

    def get_install_url(self, obj):
        return f"{settings.COMMCARE_HQ_URL}/a/{obj.cc_domain}/apps/download/{obj.cc_app_id}/media_profile.ccpr"

    def get_passing_score(self, obj):
        if obj.passing_score is None:
            return -1
        return obj.passing_score


class PaymentUnitSerializer(serializers.ModelSerializer):
    class Meta:
        model = PaymentUnit
        fields = ["id", "name", "max_total", "max_daily", "amount", "end_date"]


class OpportunityClaimLimitSerializer(serializers.ModelSerializer):
    class Meta:
        model = OpportunityClaimLimit
        fields = ["max_visits", "payment_unit"]


class OpportunityClaimSerializer(serializers.ModelSerializer):
    max_payments = serializers.SerializerMethodField()
    payment_units = serializers.SerializerMethodField()

    class Meta:
        model = OpportunityClaim
        fields = ["max_payments", "end_date", "date_claimed", "payment_units", "id"]

    def get_max_payments(self, obj):
        # return 1 for old opportunities
        return obj.opportunityclaimlimit_set.aggregate(max_visits=Sum("max_visits")).get("max_visits", 0) or -1

    def get_payment_units(self, obj):
        return OpportunityClaimLimitSerializer(
            obj.opportunityclaimlimit_set.order_by("payment_unit_id"), many=True
        ).data


class CatchmentAreaSerializer(serializers.ModelSerializer):
    class Meta:
        model = CatchmentArea
        fields = ["id", "name", "latitude", "longitude", "radius", "active"]


class OpportunityVerificationFlagsSerializer(serializers.ModelSerializer):
    class Meta:
        model = OpportunityVerificationFlags
        fields = ["form_submission_start", "form_submission_end"]


class OpportunitySerializer(serializers.ModelSerializer):
    organization = serializers.SlugRelatedField(read_only=True, slug_field="slug")
    learn_app = CommCareAppSerializer()
    deliver_app = CommCareAppSerializer()
    claim = serializers.SerializerMethodField()
    learn_progress = serializers.SerializerMethodField()
    deliver_progress = serializers.SerializerMethodField()
    max_visits_per_user = serializers.SerializerMethodField()
    daily_max_visits_per_user = serializers.SerializerMethodField()
    budget_per_visit = serializers.SerializerMethodField()
    budget_per_user = serializers.SerializerMethodField()
    payment_units = serializers.SerializerMethodField()
    is_user_suspended = serializers.SerializerMethodField()
    catchment_areas = serializers.SerializerMethodField()
    verification_flags = OpportunityVerificationFlagsSerializer(source="opportunityverificationflags", read_only=True)

    class Meta:
        model = Opportunity
        fields = [
            "id",
            "name",
            "description",
            "short_description",
            "date_created",
            "date_modified",
            "organization",
            "learn_app",
            "deliver_app",
            "start_date",
            "end_date",
            "max_visits_per_user",
            "daily_max_visits_per_user",
            "budget_per_visit",
            "total_budget",
            "claim",
            "learn_progress",
            "deliver_progress",
            "currency",
            "is_active",
            "budget_per_user",
            "payment_units",
            "is_user_suspended",
            "catchment_areas",
            "verification_flags",
        ]

    def get_claim(self, obj):
        opp_access = _get_opp_access(self.context.get("request").user, obj)
        claim = OpportunityClaim.objects.filter(opportunity_access=opp_access)
        if claim.exists():
            return OpportunityClaimSerializer(claim.first()).data
        return None

    def get_learn_progress(self, obj):
        opp_access = _get_opp_access(self.context.get("request").user, obj)
        total_modules = LearnModule.objects.filter(app=opp_access.opportunity.learn_app)
        completed_modules = opp_access.unique_completed_modules
        return {"total_modules": total_modules.count(), "completed_modules": completed_modules.count()}

    def get_deliver_progress(self, obj):
        opp_access = _get_opp_access(self.context.get("request").user, obj)
        return opp_access.visit_count

    def get_max_visits_per_user(self, obj):
        # return 1 for older opportunities
        return obj.max_visits_per_user_new or -1

    def get_daily_max_visits_per_user(self, obj):
        return obj.daily_max_visits_per_user_new or -1

    def get_budget_per_visit(self, obj):
        return obj.budget_per_visit_new or -1

    def get_budget_per_user(self, obj):
        return obj.budget_per_user

    def get_payment_units(self, obj):
        payment_units = PaymentUnit.objects.filter(opportunity=obj).order_by("pk")
        return PaymentUnitSerializer(payment_units, many=True).data

    def get_is_user_suspended(self, obj):
        opp_access = _get_opp_access(self.context.get("request").user, obj)
        return opp_access.suspended

    def get_catchment_areas(self, obj):
        opp_access = _get_opp_access(self.context.get("request").user, obj)
        catchments = CatchmentArea.objects.filter(opportunity_access=opp_access)
        return CatchmentAreaSerializer(catchments, many=True).data


@quickcache(vary_on=["user.pk", "opportunity.pk"], timeout=60 * 60)
def _get_opp_access(user, opportunity):
    return OpportunityAccess.objects.filter(user=user, opportunity=opportunity).first()


def remove_opportunity_access_cache(user, opportunity):
    return _get_opp_access.clear(user, opportunity)


class CompletedModuleSerializer(serializers.ModelSerializer):
    class Meta:
        model = CompletedModule
        fields = ["module", "date", "duration", "id"]


class AssessmentSerializer(serializers.ModelSerializer):
    class Meta:
        model = Assessment
        fields = ["date", "score", "passing_score", "passed", "id"]


class UserLearnProgressSerializer(serializers.Serializer):
    completed_modules = serializers.SerializerMethodField()
    assessments = serializers.SerializerMethodField()

    def get_completed_modules(self, obj: dict):
        return CompletedModuleSerializer(obj.get("completed_modules"), many=True).data

    def get_assessments(self, obj: dict):
        return AssessmentSerializer(obj.get("assessments"), many=True).data


class UserVisitSerializer(serializers.ModelSerializer):
    deliver_unit_name = serializers.CharField(source="deliver_unit.name")
    deliver_unit_slug = serializers.CharField(source="deliver_unit.slug")

    class Meta:
        model = UserVisit
        fields = [
            "id",
            "status",
            "visit_date",
            "deliver_unit_name",
            "deliver_unit_slug",
            "entity_id",
            "entity_name",
            "reason",
        ]


# NOTE: this serializer is only required to avoid introducing breaking changes
# to the deliver progress API
class CompletedWorkSerializer(serializers.ModelSerializer):
    deliver_unit_name = serializers.CharField(source="payment_unit.name")
    deliver_unit_slug = serializers.CharField(source="payment_unit.pk")
    visit_date = serializers.DateTimeField(source="completion_date")

    class Meta:
        model = CompletedWork
        fields = [
            "id",
            "status",
            "visit_date",
            "deliver_unit_name",
            "deliver_unit_slug",
            "entity_id",
            "entity_name",
            "reason",
        ]


class PaymentSerializer(serializers.ModelSerializer):
    class Meta:
        model = Payment
        fields = ["id", "amount", "date_paid", "confirmed", "confirmation_date"]


class DeliveryProgressSerializer(serializers.Serializer):
    deliveries = serializers.SerializerMethodField()
    payments = serializers.SerializerMethodField()
    max_payments = serializers.SerializerMethodField()
    payment_accrued = serializers.IntegerField()
    end_date = serializers.DateField(source="opportunityclaim.end_date")

    def get_max_payments(self, obj):
        return (
            obj.opportunityclaim.opportunityclaimlimit_set.aggregate(max_visits=Sum("max_visits")).get("max_visits", 0)
            or -1
        )

    def get_payments(self, obj):
        return PaymentSerializer(obj.payment_set.all(), many=True).data

    def get_deliveries(self, obj):
        completed_works = (
            CompletedWork.objects.filter(opportunity_access=obj)
            .exclude(status=CompletedWorkStatus.over_limit)
            .exclude(status=CompletedWorkStatus.incomplete)
        )
        return CompletedWorkSerializer(completed_works, many=True).data
