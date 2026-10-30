from http import HTTPStatus
from unittest.mock import patch

import pytest
from django.urls import reverse
from waffle.testutils import override_switch

from commcare_connect.flags.switch_names import SOLICITATIONS
from commcare_connect.program.tests.factories import _get_default_country
from commcare_connect.solicitation.models import Solicitation, SolicitationApplication, SolicitationInvitation
from commcare_connect.solicitation.tests.factories import (
    SolicitationApplicationFactory,
    SolicitationEvaluationCriterionFactory,
    SolicitationFactory,
    SolicitationInvitationFactory,
    SolicitationQuestionFactory,
)
from commcare_connect.solicitation.views import SolicitationListView
from commcare_connect.users.tests.factories import MembershipFactory, OrganizationFactory

Status = SolicitationApplication.Status


@pytest.mark.django_db
def test_queryset_counts(rf, program_manager_org):
    solicitation = SolicitationFactory(organization=program_manager_org)
    for status in Status:
        SolicitationApplicationFactory(solicitation=solicitation, status=status)
    SolicitationInvitationFactory.create_batch(3, solicitation=solicitation)
    empty = SolicitationFactory(organization=program_manager_org)
    SolicitationApplicationFactory(status=Status.SUBMITTED)  # another org's solicitation

    request = rf.get("/")
    request.org = program_manager_org
    view = SolicitationListView()
    view.setup(request)

    counts = {s.pk: (s.response_count, s.invited_count) for s in view.get_queryset()}

    # Every status except draft and withdrawn counts as a response; invitations don't inflate that count.
    assert counts == {solicitation.pk: (len(Status) - 2, 3), empty.pk: (0, 0)}


@pytest.mark.django_db
class TestSolicitationListAccess:
    @pytest.mark.parametrize(
        "switch_active, org_kwargs, role, expected",
        [
            (True, {"program_manager": True}, "admin", HTTPStatus.OK),
            (False, {"program_manager": True}, "admin", HTTPStatus.NOT_FOUND),
            (True, {"program_manager": True}, "member", HTTPStatus.NOT_FOUND),
            (True, {"funder": True}, "admin", HTTPStatus.NOT_FOUND),
            (True, {}, "admin", HTTPStatus.NOT_FOUND),
        ],
    )
    def test_access(self, client, switch_active, org_kwargs, role, expected):
        membership = MembershipFactory(organization=OrganizationFactory(**org_kwargs), role=role)
        client.force_login(membership.user)

        with override_switch(SOLICITATIONS, active=switch_active):
            response = client.get(reverse("solicitation:list", args=(membership.organization.slug,)))

        assert response.status_code == expected


@pytest.mark.django_db
class TestSolicitationNavItem:
    @pytest.mark.parametrize(
        "switch_active, program_manager, role, shown",
        [
            (True, True, "admin", True),
            (False, True, "admin", False),
            (True, True, "member", False),
            (True, False, "admin", False),
        ],
    )
    def test_nav_item(self, client, switch_active, program_manager, role, shown):
        membership = MembershipFactory(organization__program_manager=program_manager, role=role)
        org = membership.organization
        client.force_login(membership.user)

        with override_switch(SOLICITATIONS, active=switch_active):
            response = client.get(reverse("opportunity:list", args=(org.slug,)))

        assert (reverse("solicitation:list", args=(org.slug,)) in response.content.decode()) is shown


STEPS = ("details", "criteria", "questions", "publish")


def step_url(step, solicitation):
    return reverse(f"solicitation:{step}", args=(solicitation.organization.slug, solicitation.solicitation_id))


@pytest.mark.django_db
class TestSolicitationSteps:
    @pytest.fixture
    def draft(self, program_manager_org):
        return SolicitationFactory(organization=program_manager_org, country=_get_default_country())

    def test_create_saves_draft_then_goes_to_criteria(self, pm_client, program_manager_org, draft_data):
        response = pm_client.post(reverse("solicitation:create", args=(program_manager_org.slug,)), draft_data)

        solicitation = Solicitation.objects.get(organization=program_manager_org)
        assert solicitation.status == Solicitation.Status.DRAFT
        assert response.url == step_url("criteria", solicitation)

    def test_criteria_step_saves_rows(self, pm_client, draft, program_manager_org_user_admin):
        data = {
            "criteria-TOTAL_FORMS": 1,
            "criteria-INITIAL_FORMS": 0,
            "criteria-0-label": "Technical expertise",
            "criteria-0-weight": 100,
        }

        response = pm_client.post(step_url("criteria", draft), data)

        assert response.url == step_url("questions", draft)
        criterion = draft.criteria.get()
        assert (criterion.label, criterion.weight, criterion.created_by) == (
            "Technical expertise",
            100,
            program_manager_org_user_admin.email,
        )

    def test_questions_step_links_saved_criterion(self, pm_client, draft):
        criterion = SolicitationEvaluationCriterionFactory(solicitation=draft)
        data = {
            "questions-TOTAL_FORMS": 1,
            "questions-INITIAL_FORMS": 0,
            "questions-0-text": "Describe your team",
            "questions-0-question_type": "text",
            "questions-0-criterion": criterion.pk,
        }

        response = pm_client.post(step_url("questions", draft), data)

        assert response.url == step_url("publish", draft)
        assert draft.questions.get().criterion == criterion

    @pytest.mark.parametrize("public", [True, False])
    def test_publish_redirects_private_solicitations_to_invitations(self, pm_client, draft, public):
        draft.public = public
        draft.save()
        SolicitationEvaluationCriterionFactory(solicitation=draft, weight=100)

        response = pm_client.post(step_url("publish", draft))

        expected = (
            reverse("solicitation:list", args=(draft.organization.slug,)) if public else step_url("invitations", draft)
        )
        assert response.url == expected

    @pytest.mark.parametrize("weight, published", [(100, True), (90, False)])
    def test_publish(self, pm_client, draft, weight, published):
        SolicitationEvaluationCriterionFactory(solicitation=draft, weight=weight)

        response = pm_client.post(step_url("publish", draft))

        draft.refresh_from_db()
        assert (draft.status == Solicitation.Status.ACTIVE) is published
        assert response.status_code == (HTTPStatus.FOUND if published else HTTPStatus.OK)

    @pytest.mark.parametrize("step", ["criteria", "questions"])
    def test_active_solicitation_locks_criteria_and_questions(self, pm_client, program_manager_org, step):
        solicitation = SolicitationFactory(organization=program_manager_org, status=Solicitation.Status.ACTIVE)
        criterion = SolicitationEvaluationCriterionFactory(solicitation=solicitation, weight=100)
        question = SolicitationQuestionFactory(solicitation=solicitation, text="Original")
        data = {
            f"{step}-TOTAL_FORMS": 1,
            f"{step}-INITIAL_FORMS": 1,
            f"{step}-0-id": criterion.pk if step == "criteria" else question.pk,
            f"{step}-0-DELETE": "on",
        }

        response = pm_client.post(step_url(step, solicitation), data)

        assert response.status_code == HTTPStatus.FORBIDDEN
        assert list(solicitation.criteria.all()) == [criterion]
        assert list(solicitation.questions.all()) == [question]

    @pytest.mark.parametrize("step", STEPS)
    @pytest.mark.parametrize("status", [Solicitation.Status.CLOSED, Solicitation.Status.CANCELLED])
    def test_finished_solicitations_are_not_editable(self, pm_client, program_manager_org, status, step):
        solicitation = SolicitationFactory(organization=program_manager_org, status=status)

        assert pm_client.get(step_url(step, solicitation)).status_code == HTTPStatus.NOT_FOUND

    @pytest.mark.parametrize("step", STEPS)
    @pytest.mark.parametrize("method", ["get", "post"])
    def test_other_orgs_solicitation_not_found(self, pm_client, program_manager_org, method, step):
        solicitation = SolicitationFactory(organization__program_manager=True)
        url = reverse(f"solicitation:{step}", args=(program_manager_org.slug, solicitation.solicitation_id))

        assert getattr(pm_client, method)(url).status_code == HTTPStatus.NOT_FOUND

    @pytest.mark.parametrize("step", STEPS)
    @pytest.mark.parametrize("status", [Solicitation.Status.DRAFT, Solicitation.Status.ACTIVE])
    def test_steps_render(self, pm_client, program_manager_org, status, step):
        solicitation = SolicitationFactory(organization=program_manager_org, status=status)
        SolicitationQuestionFactory(solicitation=solicitation)

        assert pm_client.get(step_url(step, solicitation)).status_code == HTTPStatus.OK

    @pytest.mark.parametrize(
        "step, previous_step",
        [("details", None), ("criteria", "details"), ("questions", "criteria"), ("publish", "questions")],
    )
    def test_step_navigation(self, pm_client, draft, program_manager_org, step, previous_step):
        response = pm_client.get(step_url(step, draft))

        assert response.context["path"] == [
            {"title": "Solicitations", "url": reverse("solicitation:list", args=(program_manager_org.slug,))},
            {"title": draft.title},
        ]
        assert response.context.get("previous_step_url") == (previous_step and step_url(previous_step, draft))

    @pytest.mark.parametrize(
        "status, public, title_linked, invited_linked",
        [
            (Solicitation.Status.DRAFT, False, True, False),
            (Solicitation.Status.ACTIVE, False, True, True),
            (Solicitation.Status.ACTIVE, True, True, True),
            (Solicitation.Status.CLOSED, False, False, False),
            (Solicitation.Status.CANCELLED, False, False, False),
        ],
    )
    def test_list_links(self, pm_client, program_manager_org, status, public, title_linked, invited_linked):
        solicitation = SolicitationFactory(organization=program_manager_org, status=status, public=public)

        response = pm_client.get(reverse("solicitation:list", args=(program_manager_org.slug,)))

        content = response.content.decode()
        assert (step_url("details", solicitation) in content) is title_linked
        assert (step_url("invitations", solicitation) in content) is invited_linked

    def test_create_renders(self, pm_client, program_manager_org):
        response = pm_client.get(reverse("solicitation:create", args=(program_manager_org.slug,)))

        assert response.status_code == HTTPStatus.OK
        assert response.context["path"][-1] == {"title": "New solicitation"}


@pytest.fixture
def solicitation(program_manager_org):
    return SolicitationFactory(organization=program_manager_org, status=Solicitation.Status.ACTIVE, public=False)


@pytest.mark.django_db
class TestSolicitationInvitations:
    @pytest.fixture
    def mock_send_mail(self):
        with patch("commcare_connect.solicitation.tasks.send_mail_async") as mock:
            yield mock

    @pytest.mark.parametrize("public", [True, False])
    def test_invite_creates_rows_and_emails_each_org(
        self,
        pm_client,
        solicitation,
        program_manager_org_user_admin,
        mock_send_mail,
        django_capture_on_commit_callbacks,
        public,
    ):
        solicitation.public = public
        solicitation.save()
        invited_orgs = [MembershipFactory().organization for _ in range(2)]

        with django_capture_on_commit_callbacks(execute=True):
            response = pm_client.post(
                step_url("invitations", solicitation), {"organizations": [org.pk for org in invited_orgs]}
            )

        assert response.url == step_url("invitations", solicitation)
        invitations = SolicitationInvitation.objects.filter(solicitation=solicitation)
        assert {(i.organization, i.invited_by) for i in invitations} == {
            (org, program_manager_org_user_admin) for org in invited_orgs
        }
        sent_to = [call.kwargs["recipient_list"] for call in mock_send_mail.delay.call_args_list]
        assert sorted(sent_to) == sorted(org.get_member_emails() for org in invited_orgs)

    @pytest.mark.parametrize("already_invited", [True, False])
    def test_invite_rejects_invited_and_own_org(
        self, pm_client, solicitation, program_manager_org, mock_send_mail, already_invited
    ):
        if already_invited:
            organization = SolicitationInvitationFactory(solicitation=solicitation).organization
        else:
            organization = program_manager_org

        # A valid org alongside the rejected one is not invited either.
        data = {"organizations": [organization.pk, OrganizationFactory().pk]}
        response = pm_client.post(step_url("invitations", solicitation), data)

        assert response.status_code == HTTPStatus.OK
        assert response.context["form"].errors
        assert solicitation.invitations.count() == int(already_invited)
        mock_send_mail.delay.assert_not_called()

    def test_list_shows_invitations_by_org_name(self, pm_client, solicitation):
        invitation = SolicitationInvitationFactory(solicitation=solicitation)
        uninvited = OrganizationFactory()

        response = pm_client.get(step_url("invitations", solicitation))

        [row] = response.context["table"].rows
        assert (row.record, row.get_cell("organization")) == (invitation, invitation.organization.name)
        choices = dict(response.context["form"].fields["organizations"].choices)
        assert choices[uninvited.pk] == uninvited.name

    def test_delete_removes_selected_invitations(self, pm_client, solicitation):
        removed, kept = SolicitationInvitationFactory.create_batch(2, solicitation=solicitation)

        response = pm_client.post(step_url("delete_invitations", solicitation), {"invitation_ids": [removed.pk]})

        assert response.url == step_url("invitations", solicitation)
        assert list(solicitation.invitations.all()) == [kept]

    @pytest.mark.parametrize("other_solicitation", [True, False])
    def test_delete_rejects_invalid_selection(self, pm_client, solicitation, other_solicitation):
        """Nothing selected, or an id from another solicitation, deletes nothing."""
        other = SolicitationInvitationFactory()
        data = {"invitation_ids": [other.pk]} if other_solicitation else {}

        response = pm_client.post(step_url("delete_invitations", solicitation), data)

        assert response.status_code == HTTPStatus.BAD_REQUEST
        assert SolicitationInvitation.objects.filter(pk=other.pk).exists()

    @pytest.mark.parametrize(
        "status, public, own_org",
        [
            (Solicitation.Status.DRAFT, False, True),
            (Solicitation.Status.CLOSED, False, True),
            (Solicitation.Status.CANCELLED, False, True),
            (Solicitation.Status.ACTIVE, False, False),
        ],
    )
    def test_only_own_active_solicitations(self, pm_client, program_manager_org, status, public, own_org):
        organization = program_manager_org if own_org else OrganizationFactory()
        solicitation = SolicitationFactory(organization=organization, status=status, public=public)
        invitation = SolicitationInvitationFactory(solicitation=solicitation)
        # Always the PM's own org slug, so another org's solicitation is reached through this org's URLs.
        url_args = (program_manager_org.slug, solicitation.solicitation_id)
        list_url = reverse("solicitation:invitations", args=url_args)
        delete_url = reverse("solicitation:delete_invitations", args=url_args)

        assert pm_client.get(list_url).status_code == HTTPStatus.NOT_FOUND
        invite_response = pm_client.post(list_url, {"organizations": [OrganizationFactory().pk]})
        assert invite_response.status_code == HTTPStatus.NOT_FOUND
        assert pm_client.post(delete_url, {"invitation_ids": [invitation.pk]}).status_code == HTTPStatus.NOT_FOUND
        assert solicitation.invitations.get() == invitation
