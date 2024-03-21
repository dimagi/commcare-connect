from django.conf import settings
from rest_framework import serializers

from commcare_connect.cache import quickcache
from commcare_connect.opportunity.models import (
    Assessment,
    CommCareApp,
    CompletedModule,
    CompletedWork,
    LearnModule,
    Opportunity,
    OpportunityAccess,
    OpportunityClaim,
    Payment,
    UserVisit,
)


class LearnModuleSerializer(serializers.ModelSerializer):
    class Meta:
        model = LearnModule
        fields = ["slug", "name", "description", "time_estimate"]


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
        ]

    def get_install_url(self, obj):
        return f"{settings.COMMCARE_HQ_URL}/a/{obj.cc_domain}/apps/download/{obj.cc_app_id}/media_profile.ccpr"

    def get_passing_score(self, obj):
        if obj.passing_score is None:
            return -1
        return obj.passing_score


class OpportunityClaimSerializer(serializers.ModelSerializer):
    class Meta:
        model = OpportunityClaim
        fields = ["max_payments", "end_date", "date_claimed"]


class OpportunitySerializer(serializers.ModelSerializer):
    organization = serializers.SlugRelatedField(read_only=True, slug_field="slug")
    learn_app = CommCareAppSerializer()
    deliver_app = CommCareAppSerializer()
    claim = serializers.SerializerMethodField()
    learn_progress = serializers.SerializerMethodField()
    deliver_progress = serializers.SerializerMethodField()

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
            "end_date",
            "max_visits_per_user",
            "daily_max_visits_per_user",
            "budget_per_visit",
            "total_budget",
            "claim",
            "learn_progress",
            "deliver_progress",
            "currency",
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
        completed_modules = CompletedModule.objects.filter(opportunity=opp_access.opportunity, user=opp_access.user)
        return {"total_modules": total_modules.count(), "completed_modules": completed_modules.count()}

    def get_deliver_progress(self, obj):
        opp_access = _get_opp_access(self.context.get("request").user, obj)
        return opp_access.visit_count


@quickcache(vary_on=["user.pk", "opportunity.pk"], timeout=60 * 60)
def _get_opp_access(user, opportunity):
    return OpportunityAccess.objects.filter(user=user, opportunity=opportunity).first()


class CompletedModuleSerializer(serializers.ModelSerializer):
    class Meta:
        model = CompletedModule
        fields = ["module", "date", "duration"]


class AssessmentSerializer(serializers.ModelSerializer):
    class Meta:
        model = Assessment
        fields = ["date", "score", "passing_score", "passed"]


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
    deliver_unit_slug = serializers.CharField(default="dummy_payment_unit")
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
        fields = ["amount", "date_paid"]


class DeliveryProgressSerializer(serializers.Serializer):
    deliveries = serializers.SerializerMethodField()
    payments = serializers.SerializerMethodField()
    max_payments = serializers.IntegerField(source="opportunityclaim.max_payments")
    payment_accrued = serializers.IntegerField()
    end_date = serializers.DateField(source="opportunityclaim.end_date")

    def get_payments(self, obj):
        return PaymentSerializer(obj.payment_set.all(), many=True).data

    def get_deliveries(self, obj):
        completed_works = CompletedWork.objects.filter(opportunity_access=obj)
        return CompletedWorkSerializer(completed_works, many=True).data
