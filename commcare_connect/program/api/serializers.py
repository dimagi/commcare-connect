from rest_framework import serializers

from commcare_connect.organization.models import Organization
from commcare_connect.program.models import ManagedOpportunity, Program, ProgramApplication, ProgramApplicationStatus


class ProgramCreateSerializer(serializers.ModelSerializer):
    organization = serializers.SlugRelatedField(slug_field="slug", queryset=Organization.objects.all())

    class Meta:
        model = Program
        fields = [
            "name",
            "description",
            "delivery_type",
            "budget",
            "currency",
            "country",
            "start_date",
            "end_date",
            "organization",
        ]

    def validate(self, data):
        if data.get("end_date") and data.get("start_date") and data["end_date"] <= data["start_date"]:
            raise serializers.ValidationError({"end_date": "End date must be after the start date."})
        return data

    def create(self, validated_data):
        user = self.context["request"].user
        validated_data["created_by"] = user.email
        validated_data["modified_by"] = user.email
        return super().create(validated_data)


class ProgramReadSerializer(serializers.ModelSerializer):
    organization = serializers.SlugRelatedField(slug_field="slug", read_only=True)

    class Meta:
        model = Program
        fields = [
            "id",
            "program_id",
            "name",
            "slug",
            "description",
            "delivery_type",
            "budget",
            "currency",
            "country",
            "start_date",
            "end_date",
            "organization",
            "date_created",
            "date_modified",
        ]


class ManagedOpportunityCreateSerializer(serializers.ModelSerializer):
    organization = serializers.SlugRelatedField(slug_field="slug", queryset=Organization.objects.all())

    class Meta:
        model = ManagedOpportunity
        fields = [
            "name",
            "description",
            "short_description",
            "organization",
            "learn_app",
            "deliver_app",
            "start_date",
            "end_date",
            "total_budget",
            "api_key",
        ]

    def validate_organization(self, org):
        program = self.context["program"]
        accepted = ProgramApplication.objects.filter(
            program=program, organization=org, status=ProgramApplicationStatus.ACCEPTED
        ).exists()
        if not accepted:
            raise serializers.ValidationError("Organization must be an accepted member of the program.")
        return org

    def validate(self, data):
        if data.get("end_date") and data.get("start_date") and data["end_date"] <= data["start_date"]:
            raise serializers.ValidationError({"end_date": "End date must be after the start date."})
        return data

    def create(self, validated_data):
        program = self.context["program"]
        user = self.context["request"].user
        validated_data["program"] = program
        validated_data["currency"] = program.currency
        validated_data["country"] = program.country
        validated_data["delivery_type"] = program.delivery_type
        validated_data["managed"] = True
        validated_data["created_by"] = user.email
        validated_data["modified_by"] = user.email
        return super().create(validated_data)


class ManagedOpportunityReadSerializer(serializers.ModelSerializer):
    organization = serializers.SlugRelatedField(slug_field="slug", read_only=True)

    class Meta:
        model = ManagedOpportunity
        fields = [
            "id",
            "opportunity_id",
            "name",
            "description",
            "short_description",
            "organization",
            "learn_app",
            "deliver_app",
            "start_date",
            "end_date",
            "total_budget",
            "currency",
            "country",
            "delivery_type",
            "active",
            "managed",
            "date_created",
            "date_modified",
        ]


class ProgramApplicationCreateSerializer(serializers.ModelSerializer):
    organization = serializers.SlugRelatedField(slug_field="slug", queryset=Organization.objects.all())

    class Meta:
        model = ProgramApplication
        fields = ["organization"]

    def validate_organization(self, org):
        program = self.context["program"]
        if org == program.organization:
            raise serializers.ValidationError("Cannot invite the program manager's own organization.")
        if ProgramApplication.objects.filter(program=program, organization=org).exists():
            raise serializers.ValidationError("This organization has already been invited to this program.")
        return org

    def create(self, validated_data):
        program = self.context["program"]
        user = self.context["request"].user
        validated_data["program"] = program
        validated_data["status"] = ProgramApplicationStatus.INVITED
        validated_data["created_by"] = user.email
        validated_data["modified_by"] = user.email
        return super().create(validated_data)


class ProgramApplicationReadSerializer(serializers.ModelSerializer):
    organization = serializers.SlugRelatedField(slug_field="slug", read_only=True)

    class Meta:
        model = ProgramApplication
        fields = [
            "id",
            "program_application_id",
            "organization",
            "status",
            "date_created",
            "date_modified",
        ]
