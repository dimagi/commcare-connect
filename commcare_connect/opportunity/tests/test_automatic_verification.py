from http import HTTPStatus

import pytest
from django.urls import reverse

from commcare_connect.opportunity.decorators import require_manual_visit_verification
from commcare_connect.opportunity.filters import DeliverFilterSet
from commcare_connect.opportunity.forms import (
    DeliverUnitFlagsForm,
    OpportunityVerificationFlagsConfigForm,
    VisitExportForm,
)
from commcare_connect.opportunity.models import (
    DeliverUnitFlagRules,
    OpportunityAccess,
    OpportunityVerificationFlags,
    VisitReviewStatus,
    VisitValidationStatus,
)
from commcare_connect.opportunity.tests.factories import DeliverUnitFactory, OpportunityFactory, UserVisitFactory
from commcare_connect.program.tests.factories import ProgramFactory
from commcare_connect.users.tests.factories import OrganizationFactory


@pytest.mark.django_db
class TestRequireManualVisitVerificationDecorator:
    def _request_with_opp(self, automatic_visit_verification):
        opp = OpportunityFactory(automatic_visit_verification=automatic_visit_verification)
        request = type("R", (), {})()
        request.opportunity = opp
        return request

    def test_passes_through_when_flag_off(self):
        called = {}

        @require_manual_visit_verification
        def view(request):
            called["yes"] = True
            return "ok"

        result = view(self._request_with_opp(False))
        assert called == {"yes": True}
        assert result == "ok"

    def test_blocks_when_flag_on(self):
        @require_manual_visit_verification
        def view(request):
            raise AssertionError("inner view should not run")

        response = view(self._request_with_opp(True))
        assert response.status_code == HTTPStatus.FORBIDDEN
        assert response["HX-Trigger"] == "reload_table"


@pytest.mark.django_db
class TestOpportunityVerificationFlagsConfigForm:
    @pytest.mark.parametrize(
        "automatic_visit_verification, fields_shown",
        [
            pytest.param(True, False, id="hidden_when_on"),
            pytest.param(False, True, id="kept_when_off"),
        ],
    )
    def test_flag_fields_follow_automatic_verification(self, automatic_visit_verification, fields_shown):
        opp = OpportunityFactory(automatic_visit_verification=automatic_visit_verification)
        form = OpportunityVerificationFlagsConfigForm(opportunity=opp)
        for field in ("duplicate", "gps", "location"):
            assert (field in form.fields) is fields_shown

    def test_save_forces_falsy_when_flag_on(self):
        opp = OpportunityFactory(automatic_visit_verification=True)
        instance = OpportunityVerificationFlags(opportunity=opp, duplicate=True, gps=True, location=42)
        form = OpportunityVerificationFlagsConfigForm(
            data={"form_submission_start": "", "form_submission_end": ""},
            instance=instance,
            opportunity=opp,
        )
        assert form.is_valid(), form.errors
        saved = form.save()
        assert saved.duplicate is False
        assert saved.gps is False
        assert saved.location == 0


@pytest.mark.django_db
class TestDeliverUnitFlagsForm:
    def test_hides_check_attachments_when_flag_on(self):
        opp = OpportunityFactory(automatic_visit_verification=True)
        form = DeliverUnitFlagsForm(opportunity=opp)
        assert "check_attachments" not in form.fields

    def test_save_forces_check_attachments_false(self):
        opp = OpportunityFactory(automatic_visit_verification=True)
        deliver_unit = DeliverUnitFactory(app=opp.deliver_app, payment_unit=None)
        instance = DeliverUnitFlagRules(opportunity=opp, deliver_unit=deliver_unit, check_attachments=True)
        form = DeliverUnitFlagsForm(
            data={"deliver_unit": deliver_unit.pk, "duration": 5},
            instance=instance,
            opportunity=opp,
        )
        assert form.is_valid(), form.errors
        saved = form.save()
        assert saved.check_attachments is False


@pytest.mark.django_db
class TestDeliverFilterSet:
    def test_drops_review_pending_and_has_duplicates_when_flag_on(self):
        opp = OpportunityFactory(automatic_visit_verification=True)
        fs = DeliverFilterSet({}, queryset=OpportunityAccess.objects.none(), opportunity=opp)
        assert "review_pending" not in fs.filters
        assert "has_duplicates" not in fs.filters
        # Other filters remain
        assert "last_active" in fs.filters
        assert "has_flags" in fs.filters

    def test_keeps_all_filters_when_flag_off(self):
        opp = OpportunityFactory(automatic_visit_verification=False)
        fs = DeliverFilterSet({}, queryset=OpportunityAccess.objects.none(), opportunity=opp)
        for name in ("review_pending", "has_duplicates", "last_active", "has_flags", "has_overlimit"):
            assert name in fs.filters


@pytest.mark.django_db
class TestVisitExportForm:
    @pytest.mark.parametrize(
        "automatic_visit_verification, pending_offered",
        [
            pytest.param(True, False, id="stripped_when_on"),
            pytest.param(False, True, id="kept_when_off"),
        ],
    )
    def test_pending_choice_follows_automatic_verification(
        self, organization, automatic_visit_verification, pending_offered
    ):
        opp = OpportunityFactory(automatic_visit_verification=automatic_visit_verification, organization=organization)
        form = VisitExportForm(opportunity=opp, org_slug=organization.slug)
        choices = dict(form.fields["status"].choices)
        assert (VisitValidationStatus.pending.value in choices) is pending_offered

    def test_review_export_unaffected(self, organization):
        opp = OpportunityFactory(automatic_visit_verification=True, organization=organization)
        form = VisitExportForm(opportunity=opp, org_slug=organization.slug, review_export=True)
        # review export uses VisitReviewStatus choices, not VisitValidationStatus
        choices = dict(form.fields["status"].choices)
        assert VisitReviewStatus.pending.value in choices


@pytest.mark.django_db
class TestProgramHomePendingReview:
    def test_pm_home_excludes_auto_verified(self, client, program_manager_org, program_manager_org_user_admin):
        program = ProgramFactory(organization=program_manager_org)
        nm_org = OrganizationFactory()
        manual_opp = OpportunityFactory(organization=nm_org, program=program, automatic_visit_verification=False)
        auto_opp = OpportunityFactory(organization=nm_org, program=program, automatic_visit_verification=True)
        for opp in (manual_opp, auto_opp):
            UserVisitFactory(
                opportunity=opp,
                status=VisitValidationStatus.approved,
                review_status=VisitReviewStatus.pending,
                review_created_on="2026-01-01T00:00:00Z",
            )

        client.force_login(program_manager_org_user_admin)
        opp_names = self._pending_review_opp_names(client=client, org_slug=program_manager_org.slug)
        assert manual_opp.name in opp_names
        assert auto_opp.name not in opp_names

    def test_nm_home_excludes_auto_verified(self, client, organization, program_manager_org, org_user_admin):
        program = ProgramFactory(organization=program_manager_org)
        manual_opp = OpportunityFactory(organization=organization, program=program, automatic_visit_verification=False)
        auto_opp = OpportunityFactory(organization=organization, program=program, automatic_visit_verification=True)
        for opp in (manual_opp, auto_opp):
            UserVisitFactory(opportunity=opp, status=VisitValidationStatus.pending)

        client.force_login(org_user_admin)
        opp_names = self._pending_review_opp_names(client=client, org_slug=organization.slug)
        assert manual_opp.name in opp_names
        assert auto_opp.name not in opp_names

    def _pending_review_opp_names(self, client, org_slug):
        """Names of the opportunities listed on the program home's Pending Review card."""
        response = client.get(reverse("program:home", args=(org_slug,)))
        assert response.status_code == HTTPStatus.OK
        pending_review_card = next(
            (a for a in response.context["recent_activities"] if a["title"] == "Pending Review"), None
        )
        assert pending_review_card is not None
        return {row["opportunity__name"] for row in pending_review_card["rows"]}


@pytest.mark.django_db
class TestAutomaticVerificationBackendGuards:
    @pytest.mark.parametrize(
        "url_name,post_data",
        [
            ("opportunity:approve_visits", {"visit_ids[]": []}),
            ("opportunity:reject_visits", {"visit_ids[]": [], "reason": "x"}),
            ("opportunity:user_visit_review", {"review_status": "agree"}),
            ("opportunity:visit_import", {}),
            ("opportunity:review_visit_import", {}),
            ("opportunity:review_visit_export", {}),
        ],
    )
    def test_endpoint_returns_403(self, client, organization, org_user_member, url_name, post_data):
        opp = OpportunityFactory(organization=organization, automatic_visit_verification=True)
        client.force_login(org_user_member)
        url = reverse(url_name, args=(organization.slug, opp.opportunity_id))
        response = client.post(url, data=post_data)
        assert response.status_code == HTTPStatus.FORBIDDEN
        assert response["HX-Trigger"] == "reload_table"


@pytest.mark.django_db
class TestReviewVisitImportRequirePost:
    def test_get_is_rejected(self, client, organization, org_user_member):
        opp = OpportunityFactory(organization=organization, automatic_visit_verification=False)
        client.force_login(org_user_member)
        url = reverse("opportunity:review_visit_import", args=(organization.slug, opp.opportunity_id))
        response = client.get(url)
        assert response.status_code == HTTPStatus.METHOD_NOT_ALLOWED

    def test_non_member_is_rejected(self, client, organization, user):
        opp = OpportunityFactory(organization=organization, automatic_visit_verification=False)
        client.force_login(user)  # authenticated but not a member of `organization`
        url = reverse("opportunity:review_visit_import", args=(organization.slug, opp.opportunity_id))
        response = client.post(url)
        assert response.status_code == HTTPStatus.NOT_FOUND


@pytest.mark.django_db
class TestVisitVerificationFilterStatusClamping:
    @pytest.mark.parametrize(
        "visit_statuses, filter_status, expected_count",
        [
            # A bookmarked ?filter_status=pending must not surface pending visits when auto-verify is on:
            # it is clamped to "all", so every visit is returned.
            pytest.param(
                [VisitValidationStatus.pending, VisitValidationStatus.approved],
                "pending",
                2,
                id="stale_pending_clamped_to_all",
            ),
            pytest.param(
                [VisitValidationStatus.approved, VisitValidationStatus.rejected],
                "approved",
                1,
                id="allowed_status_passes_through",
            ),
        ],
    )
    def test_filter_status_in_auto_verify_mode(
        self, client, organization, org_user_member, mobile_user, visit_statuses, filter_status, expected_count
    ):
        opp = mobile_user.opportunityaccess_set.first().opportunity
        opp.automatic_visit_verification = True
        opp.save()

        access = mobile_user.opportunityaccess_set.first()
        for status in visit_statuses:
            UserVisitFactory(opportunity=opp, user=mobile_user, opportunity_access=access, status=status)

        client.force_login(org_user_member)
        url = reverse("opportunity:user_visit_verification_table", args=(organization.slug, opp.opportunity_id))
        response = client.get(f"{url}?filter_status={filter_status}&user={mobile_user.user_id}")
        assert response.status_code == HTTPStatus.OK
        assert response.context["table"].rows.data.data.count() == expected_count
