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

        [row] = solicitation.applications.with_review_summary(reviewer_ids=[reviewer.pk])

        assert (row.average_score, row.reviewed_count) == (70, 1)
        assert (row.approve_count, row.reject_count, row.needs_revision_count) == (1, 1, 0)

    def test_responses_exclude_drafts_and_withdrawn(self, solicitation):
        applications = {
            status: SolicitationApplicationFactory(solicitation=solicitation, status=status) for status in Status
        }

        expected = {app for status, app in applications.items() if status not in (Status.DRAFT, Status.WITHDRAWN)}
        assert set(solicitation.applications.responses()) == expected

    def test_with_review_summary_without_reviewers(self, solicitation):
        SolicitationApplicationFactory(solicitation=solicitation)

        [row] = solicitation.applications.with_review_summary(reviewer_ids=[])

        assert (row.average_score, row.reviewed_count, row.approve_count) == (None, 0, 0)


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

    def test_observers_are_not_counted_as_reviewers(self, pm_client, solicitation):
        SolicitationReviewerFactory(solicitation=solicitation)
        SolicitationReviewerFactory(solicitation=solicitation, role="observer")

        response = pm_client.get(applications_url(solicitation))

        assert response.context["table"].reviewer_count == 1

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

    def test_list_links_responses_to_applications(self, pm_client, solicitation):
        response = pm_client.get(reverse("solicitation:list", args=(solicitation.organization.slug,)))

        assert applications_url(solicitation) in response.content.decode()

    def test_closed_solicitation_breadcrumb_has_no_details_link(self, pm_client, program_manager_org):
        closed = SolicitationFactory(organization=program_manager_org, status="closed")

        response = pm_client.get(applications_url(closed))

        assert response.context["path"][1] == {"title": closed.title, "url": None}


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
        assert applications_url(solicitation) in response.content.decode()

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

    def test_status_shows_as_a_badge_without_the_applicants_shortlisting(
        self, pm_client, program_manager_org, solicitation
    ):
        SolicitationApplicationFactory(solicitation=solicitation, status=Status.UNDER_REVIEW, shortlisted=True)

        content = pm_client.get(received_url(program_manager_org)).content.decode()

        assert '<span class="badge badge-sm badge-indigo">Under Review</span>' in content

    def test_sidebar_links_pm_admins_to_received_applications(self, pm_client, program_manager_org):
        response = pm_client.get(reverse("opportunity:list", args=(program_manager_org.slug,)))

        assert received_url(program_manager_org) in response.content.decode()


@pytest.mark.django_db
class TestSolicitationApplicationTable:
    def cell(self, application, column, reviewer_count=0):
        rows = SolicitationApplication.objects.filter(pk=application.pk).with_review_summary(reviewer_ids=[])
        return SolicitationApplicationTable(rows, reviewer_count=reviewer_count).rows[0].get_cell(column)

    @pytest.mark.parametrize("reviewer_count, expected", [(2, "0/2"), (0, "No reviewers")])
    def test_review_progress(self, reviewer_count, expected):
        application = SolicitationApplicationFactory(status=Status.UNDER_REVIEW)

        assert expected in self.cell(application, "review_progress", reviewer_count=reviewer_count)

    def test_recommendations(self):
        application = SolicitationApplicationFactory(status=Status.UNDER_REVIEW)
        for recommendation in (Recommendation.APPROVE, Recommendation.APPROVE, Recommendation.NEEDS_REVISION):
            SolicitationApplicationReviewFactory(
                application=application, recommendation=recommendation, submitted=True
            )

        cell = self.cell(application, "recommendations")

        assert "Approve: 2" in cell
        assert "Needs Revision: 1" in cell
        assert "Reject" not in cell

    def test_average_score_rounds_half_up(self):
        application = SolicitationApplicationFactory(status=Status.UNDER_REVIEW)
        for score in (70, 75):
            SolicitationApplicationReviewFactory(application=application, overall_score=score, submitted=True)

        assert self.cell(application, "average_score") == Decimal(73)
