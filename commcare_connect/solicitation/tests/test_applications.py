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
from commcare_connect.users.tests.factories import MembershipFactory, OrganizationFactory

Status = SolicitationApplication.Status
Recommendation = SolicitationApplicationReview.Recommendation


@pytest.fixture
def solicitation(program_manager_org):
    return SolicitationFactory(organization=program_manager_org)


def applications_url(solicitation, org_slug=None):
    return reverse(
        "solicitation:applications", args=(org_slug or solicitation.organization.slug, solicitation.solicitation_id)
    )


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


@pytest.mark.django_db
class TestSolicitationApplicationsView:
    @pytest.mark.parametrize(
        "query, expected_shortlisted",
        [("", [True, False]), ("?shortlisted=on", [True])],
    )
    def test_shortlisted_toggle_shows_only_shortlisted(self, pm_client, solicitation, query, expected_shortlisted):
        for shortlisted in (True, False):
            SolicitationApplicationFactory(
                solicitation=solicitation, status=Status.UNDER_REVIEW, shortlisted=shortlisted
            )

        response = pm_client.get(applications_url(solicitation) + query)

        rows = response.context["table"].rows
        assert sorted(row.record.shortlisted for row in rows) == sorted(expected_shortlisted)

    @pytest.mark.parametrize("query, applied", [("", False), ("?shortlisted=on", True)])
    def test_clear_filters_is_enabled_only_when_filters_apply(self, pm_client, solicitation, query, applied):
        response = pm_client.get(applications_url(solicitation) + query)

        assert response.context["filters_applied"] is applied
        button = response.content.decode().split("Clear filters")[0].rsplit("<button", 1)[1]
        assert ("disabled" not in button) is applied

    def test_other_orgs_solicitation_not_found(self, pm_client, program_manager_org):
        other = SolicitationFactory(organization=OrganizationFactory(program_manager=True))

        response = pm_client.get(applications_url(other, org_slug=program_manager_org.slug))

        assert response.status_code == HTTPStatus.NOT_FOUND

    @pytest.mark.parametrize(
        "org_kwargs, role, expected",
        [
            ({"program_manager": True}, "admin", HTTPStatus.OK),
            ({"program_manager": True}, "member", HTTPStatus.NOT_FOUND),
            ({}, "admin", HTTPStatus.NOT_FOUND),
        ],
    )
    def test_pm_only(self, client, org_kwargs, role, expected):
        membership = MembershipFactory(organization=OrganizationFactory(**org_kwargs), role=role)
        solicitation = SolicitationFactory(organization=membership.organization)
        client.force_login(membership.user)

        with override_switch(SOLICITATIONS, active=True):
            response = client.get(applications_url(solicitation))

        assert response.status_code == expected

    def test_closed_solicitation_breadcrumb_has_no_details_link(self, pm_client, program_manager_org):
        closed = SolicitationFactory(organization=program_manager_org, status="closed")

        response = pm_client.get(applications_url(closed))

        assert response.context["path"][1] == {"title": closed.title, "url": None}
        assert f"<span>{closed.title}</span>" in response.content.decode()


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

        response = pm_client.get(f"{received_url(program_manager_org)}?solicitation={solicitation.pk}")

        assert [row.record.solicitation for row in response.context["table"].rows] == [solicitation]
        choices = response.context["filter"].form.fields["solicitation"].queryset
        assert set(choices) == {solicitation, other_solicitation}

    @pytest.mark.parametrize("user_fixture", ["program_manager_org_user_member", "org_user_admin"])
    def test_not_found_for_non_pm_admins(self, client, request, user_fixture):
        user = request.getfixturevalue(user_fixture)
        client.force_login(user)
        organization = user.memberships.get().organization

        with override_switch(SOLICITATIONS, active=True):
            assert client.get(received_url(organization)).status_code == HTTPStatus.NOT_FOUND


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
