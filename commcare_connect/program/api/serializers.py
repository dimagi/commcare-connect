from django.db import IntegrityError
from django.db.models import Sum
from django.utils.translation import gettext_lazy as _
from rest_framework import serializers

from commcare_connect.commcarehq.models import HQServer
from commcare_connect.opportunity.models import (
    CommCareApp,
    Country,
    Currency,
    DeliverUnit,
    DeliveryType,
    HQApiKey,
    LearnModule,
)
from commcare_connect.organization.models import Organization
from commcare_connect.program.models import ManagedOpportunity, Program, ProgramApplication, ProgramApplicationStatus
from commcare_connect.utils.commcarehq_api import CommCareHQAPIException, get_applications_for_user_by_domain


class ProgramCreateSerializer(serializers.ModelSerializer):
    organization = serializers.SlugRelatedField(slug_field="slug", queryset=Organization.objects.all())
    delivery_type = serializers.SlugRelatedField(slug_field="slug", queryset=DeliveryType.objects.all())
    currency = serializers.SlugRelatedField(slug_field="code", queryset=Currency.objects.all())
    country = serializers.SlugRelatedField(slug_field="name", queryset=Country.objects.all())

    class Meta:
        model = Program
        fields = [
            "name",
            "description",
            "organization",
            "delivery_type",
            "budget",
            "currency",
            "country",
            "start_date",
            "end_date",
        ]

    def validate_organization(self, value):
        if not value.program_manager:
            raise serializers.ValidationError(_("Organization must be a program manager organization."))
        return value

    def validate(self, data):
        if data["end_date"] <= data["start_date"]:
            raise serializers.ValidationError({"end_date": _("End date must be after start date.")})
        return data

    def create(self, validated_data):
        user = self.context["request"].user
        return Program.objects.create(
            created_by=user.email,
            modified_by=user.email,
            **validated_data,
        )


class ProgramResponseSerializer(serializers.ModelSerializer):
    organization = serializers.SlugRelatedField(slug_field="slug", read_only=True)
    delivery_type = serializers.SlugRelatedField(slug_field="slug", read_only=True)
    currency = serializers.SlugRelatedField(slug_field="code", read_only=True)
    country = serializers.SlugRelatedField(slug_field="name", read_only=True)

    class Meta:
        model = Program
        fields = [
            "program_id",
            "name",
            "slug",
            "description",
            "organization",
            "delivery_type",
            "budget",
            "currency",
            "country",
            "start_date",
            "end_date",
        ]


class ProgramApplicationCreateSerializer(serializers.ModelSerializer):
    organization = serializers.SlugRelatedField(slug_field="slug", queryset=Organization.objects.all())

    class Meta:
        model = ProgramApplication
        fields = ["organization"]

    def validate_organization(self, value):
        program = self.context["program"]
        if ProgramApplication.objects.filter(program=program, organization=value).exists():
            raise serializers.ValidationError(_("Organization already has an application for this program."))
        return value

    def create(self, validated_data):
        user = self.context["request"].user
        return ProgramApplication.objects.create(
            program=self.context["program"],
            organization=validated_data["organization"],
            status=ProgramApplicationStatus.INVITED,
            created_by=user.email,
            modified_by=user.email,
        )


class ProgramApplicationResponseSerializer(serializers.ModelSerializer):
    program = serializers.UUIDField(source="program.program_id", read_only=True)
    organization = serializers.SlugRelatedField(slug_field="slug", read_only=True)

    class Meta:
        model = ProgramApplication
        fields = ["program_application_id", "program", "organization", "status"]


class CommCareAppInputSerializer(serializers.Serializer):
    hq_server_url = serializers.SlugRelatedField(slug_field="url", queryset=HQServer.objects.all(), source="hq_server")
    api_key = serializers.CharField(max_length=50)
    cc_domain = serializers.CharField(max_length=255)
    cc_app_id = serializers.CharField(max_length=50)


class LearnAppInputSerializer(CommCareAppInputSerializer):
    description = serializers.CharField()
    passing_score = serializers.IntegerField(min_value=0, max_value=100)


class ManagedOpportunityCreateSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=255)
    description = serializers.CharField()
    short_description = serializers.CharField(max_length=255)
    organization = serializers.SlugRelatedField(slug_field="slug", queryset=Organization.objects.all())
    start_date = serializers.DateField()
    end_date = serializers.DateField()
    total_budget = serializers.IntegerField(min_value=1)
    learn_app = LearnAppInputSerializer()
    deliver_app = CommCareAppInputSerializer()

    def validate_organization(self, value):
        program = self.context["program"]
        if not ProgramApplication.objects.filter(
            program=program,
            organization=value,
            status=ProgramApplicationStatus.ACCEPTED,
        ).exists():
            raise serializers.ValidationError(_("Organization must have an accepted application for this program."))
        return value

    def validate(self, data):
        program = self.context["program"]

        if data["learn_app"]["cc_app_id"] == data["deliver_app"]["cc_app_id"]:
            raise serializers.ValidationError(_("Learn app and deliver app must be different."))

        start_date = data["start_date"]
        end_date = data["end_date"]
        if start_date >= end_date:
            raise serializers.ValidationError({"end_date": _("End date must be after start date.")})
        if not (program.start_date <= start_date <= program.end_date):
            raise serializers.ValidationError(
                {"start_date": _("Start date must be within the program's start and end dates.")}
            )
        if not (program.start_date <= end_date <= program.end_date):
            raise serializers.ValidationError(
                {"end_date": _("End date must be within the program's start and end dates.")}
            )

        other_budgets = (
            ManagedOpportunity.objects.filter(program=program).aggregate(total=Sum("total_budget"))["total"] or 0
        )
        if other_budgets + data["total_budget"] > program.budget:
            raise serializers.ValidationError({"total_budget": _("Budget exceeds the program budget.")})

        return data

    def _resolve_api_key(self, user, hq_server, api_key_string):
        try:
            api_key, __ = HQApiKey.objects.get_or_create(
                user=user,
                hq_server=hq_server,
                api_key=api_key_string,
            )
            return api_key
        except IntegrityError:
            raise serializers.ValidationError({"api_key": _("This API key is already registered to another user.")})

    def _get_hq_app_name(self, api_key, domain, cc_app_id):
        """Fetch the app name from HQ. Results cached per (api_key, domain) to avoid duplicate calls."""
        if not hasattr(self, "_apps_cache"):
            self._apps_cache = {}
        cache_key = (api_key.pk, domain)
        if cache_key not in self._apps_cache:
            try:
                self._apps_cache[cache_key] = get_applications_for_user_by_domain(api_key, domain)
            except CommCareHQAPIException:
                raise serializers.ValidationError(
                    {"non_field_errors": [_("Failed to fetch apps from CommCare HQ.")]}
                )
        for app in self._apps_cache[cache_key]:
            if app["id"] == cc_app_id:
                return app["name"]
        raise serializers.ValidationError(
            {
                "non_field_errors": [
                    _("App '{cc_app_id}' not found in domain '{domain}'.").format(cc_app_id=cc_app_id, domain=domain)
                ]
            }
        )

    def create(self, validated_data):
        user = self.context["request"].user
        program = self.context["program"]
        organization = validated_data["organization"]
        learn_data = validated_data["learn_app"]
        deliver_data = validated_data["deliver_app"]

        learn_api_key = self._resolve_api_key(user, learn_data["hq_server"], learn_data["api_key"])
        deliver_api_key = self._resolve_api_key(user, deliver_data["hq_server"], deliver_data["api_key"])

        learn_app_name = self._get_hq_app_name(learn_api_key, learn_data["cc_domain"], learn_data["cc_app_id"])
        deliver_app_name = self._get_hq_app_name(deliver_api_key, deliver_data["cc_domain"], deliver_data["cc_app_id"])

        learn_app, __ = CommCareApp.objects.get_or_create(
            cc_app_id=learn_data["cc_app_id"],
            cc_domain=learn_data["cc_domain"],
            organization=organization,
            hq_server=learn_data["hq_server"],
            defaults={
                "name": learn_app_name,
                "description": learn_data["description"],
                "passing_score": learn_data["passing_score"],
                "created_by": user.email,
                "modified_by": user.email,
            },
        )

        deliver_app, __ = CommCareApp.objects.get_or_create(
            cc_app_id=deliver_data["cc_app_id"],
            cc_domain=deliver_data["cc_domain"],
            organization=organization,
            hq_server=deliver_data["hq_server"],
            defaults={
                "name": deliver_app_name,
                "description": "",
                "created_by": user.email,
                "modified_by": user.email,
            },
        )

        opportunity = ManagedOpportunity.objects.create(
            name=validated_data["name"],
            description=validated_data["description"],
            short_description=validated_data["short_description"],
            organization=organization,
            program=program,
            learn_app=learn_app,
            deliver_app=deliver_app,
            start_date=validated_data["start_date"],
            end_date=validated_data["end_date"],
            total_budget=validated_data["total_budget"],
            currency=program.currency,
            country=program.country,
            delivery_type=program.delivery_type,
            api_key=learn_api_key,
            hq_server=learn_data["hq_server"],
            active=False,
            created_by=user.email,
            modified_by=user.email,
        )
        return opportunity


class DeliverUnitResponseSerializer(serializers.ModelSerializer):
    class Meta:
        model = DeliverUnit
        fields = ["id", "slug", "name"]


class LearnModuleResponseSerializer(serializers.ModelSerializer):
    class Meta:
        model = LearnModule
        fields = ["id", "slug", "name", "description", "time_estimate"]


class ManagedOpportunityResponseSerializer(serializers.ModelSerializer):
    organization = serializers.SlugRelatedField(slug_field="slug", read_only=True)
    program_id = serializers.UUIDField(source="program.program_id", read_only=True)
    learn_app = serializers.SerializerMethodField()
    deliver_app = serializers.SerializerMethodField()
    currency = serializers.SlugRelatedField(slug_field="code", read_only=True)
    country = serializers.SlugRelatedField(slug_field="name", read_only=True)

    class Meta:
        model = ManagedOpportunity
        fields = [
            "id",
            "opportunity_id",
            "name",
            "description",
            "short_description",
            "organization",
            "managed",
            "program_id",
            "start_date",
            "end_date",
            "total_budget",
            "learn_app",
            "deliver_app",
            "currency",
            "country",
            "active",
        ]

    def get_learn_app(self, obj):
        app = obj.learn_app
        return {
            "cc_domain": app.cc_domain,
            "cc_app_id": app.cc_app_id,
            "name": app.name,
            "learn_modules": LearnModuleResponseSerializer(LearnModule.objects.filter(app=app), many=True).data,
        }

    def get_deliver_app(self, obj):
        app = obj.deliver_app
        return {
            "cc_domain": app.cc_domain,
            "cc_app_id": app.cc_app_id,
            "name": app.name,
            "deliver_units": DeliverUnitResponseSerializer(DeliverUnit.objects.filter(app=app), many=True).data,
        }
