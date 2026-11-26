from decimal import Decimal
from http import HTTPStatus

import pytest
from django.urls import reverse
from waffle.testutils import override_switch

from commcare_connect.flags.switch_names import SOLICITATIONS
from commcare_connect.solicitation.models import SolicitationApplication, SolicitationApplicationReview
from commcare_connect.solicitation.tables import SolicitationApplicationTable
from commcare_connect.solicitation.tests.factories import (
    SolicitationApplicationFactory,
    SolicitationApplicationReviewFactory,
    SolicitationFactory,
    SolicitationReviewerFactory,
)

Status = SolicitationApplication.Status
Recommendation = SolicitationApplicationReview.Recommendation


@pytest.fixture
def solicitation(program_manager_org):
    return SolicitationFactory(organization=program_manager_org)


@pytest.mark.django_db
class TestApplicationQuerySet:
    def test_with_review_summary(self, solicitation):
        application = SolicitationApplicationFactory(solicitation=solicitation, status=Status.UNDER_REVIEW)
        reviewer = SolicitationReviewerFactory(solicitation=solicitation).user
        SolicitationApplicationReviewFactory(
            application=application,
            reviewer=reviewer,
            overall_score=80,
            recommendation=Recommendation.APPROVE,
            submitted=True,
        )
        # By someone no longer assigned: still in the score and recommendations, not in progress.
        SolicitationApplicationReviewFactory(
            application=application,
            overall_score=60,
            recommendation=Recommendation.REJECT,
            submitted=True,
        )
        # A draft review counts nowhere.
        SolicitationApplicationReviewFactory(
            application=application, overall_score=10, recommendation=Recommendation.REJECT
        )

        # Observers don't count towards progress.
        SolicitationReviewerFactory(solicitation=solicitation, role="observer")

        [row] = solicitation.applications.with_review_summary()

        assert (row.average_score, row.reviewed_count, row.reviewer_count) == (70, 1, 1)
        assert (row.approve_count, row.reject_count, row.needs_revision_count) == (1, 1, 0)

    def test_responses_exclude_drafts_and_withdrawn(self, solicitation):
        applications = {
            status: SolicitationApplicationFactory(solicitation=solicitation, status=status) for status in Status
        }

        expected = {app for status, app in applications.items() if status not in (Status.DRAFT, Status.WITHDRAWN)}
        assert set(solicitation.applications.responses()) == expected

    def test_review_progress_uses_each_applications_own_solicitation(self, solicitation):
        reviewed = SolicitationApplicationFactory(solicitation=solicitation)
        reviewer = SolicitationReviewerFactory(solicitation=solicitation).user
        SolicitationApplicationReviewFactory(application=reviewed, reviewer=reviewer, submitted=True)
        other = SolicitationApplicationFactory(
            solicitation=SolicitationFactory(organization=solicitation.organization)
        )
        SolicitationReviewerFactory.create_batch(2, solicitation=other.solicitation)
        # Reviews other.solicitation's application, but isn't one of its reviewers.
        SolicitationApplicationReviewFactory(application=other, reviewer=reviewer, submitted=True)

        rows = SolicitationApplication.objects.filter(pk__in=[reviewed.pk, other.pk]).with_review_summary()

        progress = {row.pk: (row.reviewed_count, row.reviewer_count) for row in rows}
        assert progress == {reviewed.pk: (1, 1), other.pk: (0, 2)}


def received_url(organization):
    return reverse("received_applications:list", args=(organization.slug,))


@pytest.mark.django_db
class TestReceivedApplicationsView:
    def test_lists_responses_to_the_organizations_solicitations(self, pm_client, program_manager_org, solicitation):
        submitted = SolicitationApplicationFactory(solicitation=solicitation, status=Status.SUBMITTED)
        other_solicitation = SolicitationFactory(organization=program_manager_org)
        under_review = SolicitationApplicationFactory(solicitation=other_solicitation, status=Status.UNDER_REVIEW)
        SolicitationApplicationFactory(solicitation=solicitation, status=Status.DRAFT)
        SolicitationApplicationFactory(status=Status.SUBMITTED)

        response = pm_client.get(received_url(program_manager_org))

        assert {row.record for row in response.context["table"].rows} == {submitted, under_review}

    def test_solicitation_filter_offers_only_the_organizations_solicitations(
        self, pm_client, program_manager_org, solicitation
    ):
        SolicitationApplicationFactory(solicitation=solicitation, status=Status.SUBMITTED)
        other_solicitation = SolicitationFactory(organization=program_manager_org)
        SolicitationApplicationFactory(solicitation=other_solicitation, status=Status.SUBMITTED)
        SolicitationFactory()

        response = pm_client.get(f"{received_url(program_manager_org)}?solicitation={solicitation.solicitation_id}")

        assert [row.record.solicitation for row in response.context["table"].rows] == [solicitation]
        choices = response.context["filter"].form.fields["solicitation"].queryset
        assert set(choices) == {solicitation, other_solicitation}

    def test_solicitation_filter_refuses_another_organizations_solicitation(self, pm_client, program_manager_org):
        other = SolicitationApplicationFactory(status=Status.SUBMITTED).solicitation

        response = pm_client.get(f"{received_url(program_manager_org)}?solicitation={other.solicitation_id}")

        assert list(response.context["table"].rows) == []

    def test_responses_link_preselects_the_solicitation(self, pm_client, solicitation):
        response = pm_client.get(reverse("solicitation:list", args=(solicitation.organization.slug,)))

        expected = f'href="{received_url(solicitation.organization)}?solicitation={solicitation.solicitation_id}"'
        assert expected in response.content.decode()

    @pytest.mark.parametrize("user_fixture", ["program_manager_org_user_member", "org_user_admin"])
    def test_not_found_for_non_pm_admins(self, client, request, user_fixture):
        user = request.getfixturevalue(user_fixture)
        client.force_login(user)
        organization = user.memberships.get().organization

        with override_switch(SOLICITATIONS, active=True):
            assert client.get(received_url(organization)).status_code == HTTPStatus.NOT_FOUND

    @pytest.mark.parametrize(
        "query, expected_shortlisted",
        [("", [True, False]), ("?shortlisted=on", [True])],
    )
    def test_shortlisted_toggle_shows_only_shortlisted(self, pm_client, solicitation, query, expected_shortlisted):
        for shortlisted in (True, False):
            SolicitationApplicationFactory(
                solicitation=solicitation, status=Status.UNDER_REVIEW, shortlisted=shortlisted
            )

        response = pm_client.get(received_url(solicitation.organization) + query)

        rows = response.context["table"].rows
        assert sorted(row.record.shortlisted for row in rows) == sorted(expected_shortlisted)

    @pytest.mark.parametrize(
        "query, expected_statuses",
        [("", {Status.SUBMITTED, Status.UNDER_REVIEW}), ("?status=under_review", {Status.UNDER_REVIEW})],
    )
    def test_status_filter(self, pm_client, solicitation, query, expected_statuses):
        for status in (Status.SUBMITTED, Status.UNDER_REVIEW):
            SolicitationApplicationFactory(solicitation=solicitation, status=status)

        response = pm_client.get(received_url(solicitation.organization) + query)

        assert {row.record.status for row in response.context["table"].rows} == expected_statuses

    def test_status_filter_offers_only_response_statuses(self, pm_client, solicitation):
        response = pm_client.get(received_url(solicitation.organization))

        choices = {value for value, _ in response.context["filter"].form.fields["status"].choices if value}
        assert choices == {Status.SUBMITTED, Status.UNDER_REVIEW, Status.AWARDED, Status.REJECTED}

    @pytest.mark.parametrize("query, applied", [("", False), ("?shortlisted=on", True), ("?status=awarded", True)])
    def test_clear_filters_is_enabled_only_when_filters_apply(self, pm_client, solicitation, query, applied):
        response = pm_client.get(received_url(solicitation.organization) + query)

        assert response.context["filters_applied"] is applied
        button = response.content.decode().split("Clear filters")[0].rsplit("<button", 1)[1]
        assert ("disabled" not in button) is applied


@pytest.mark.django_db
class TestSolicitationApplicationTable:
    def cell(self, application, column):
        rows = SolicitationApplication.objects.filter(pk=application.pk).with_review_summary()
        return SolicitationApplicationTable(rows).rows[0].get_cell(column)

    def test_recommendations(self):
        application = SolicitationApplicationFactory(status=Status.UNDER_REVIEW)
        for recommendation in (Recommendation.APPROVE, Recommendation.APPROVE, Recommendation.NEEDS_REVISION):
            SolicitationApplicationReviewFactory(
                application=application, recommendation=recommendation, submitted=True
            )

        cell = self.cell(application=application, column="recommendations")

        assert "Approve: 2" in cell
        assert "Needs Revision: 1" in cell
        assert "Reject" not in cell

    def test_average_score_rounds_half_up(self):
        application = SolicitationApplicationFactory(status=Status.UNDER_REVIEW)
        for score in (70, 75):
            SolicitationApplicationReviewFactory(application=application, overall_score=score, submitted=True)

        assert self.cell(application=application, column="average_score") == Decimal(73)

    @pytest.mark.parametrize("is_descending, expected", [(False, [60, 80, None]), (True, [80, 60, None])])
    def test_average_score_sorts_unscored_last(self, is_descending, expected):
        for score in (80, None, 60):
            application = SolicitationApplicationFactory(status=Status.UNDER_REVIEW)
            if score is not None:
                SolicitationApplicationReviewFactory(application=application, overall_score=score, submitted=True)
        table = SolicitationApplicationTable(
            SolicitationApplication.objects.with_review_summary(),
            order_by="-average_score" if is_descending else "average_score",
        )

        assert [row.record.average_score for row in table.rows] == expected

    @pytest.mark.parametrize("column", ["solicitation", "organization", "submitted_date", "status", "shortlisted"])
    @pytest.mark.parametrize("prefix", ["", "-"])
    def test_sorting_keeps_ties_in_primary_key_order(self, column, prefix):
        table = SolicitationApplicationTable(
            SolicitationApplication.objects.with_review_summary(), order_by=f"{prefix}{column}"
        )

        assert table.data.data.query.order_by[-1] == f"{prefix}pk"
