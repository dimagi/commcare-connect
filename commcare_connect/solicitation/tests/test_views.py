import re
from http import HTTPStatus
from unittest.mock import patch

import pytest
from django.urls import reverse
from waffle.testutils import override_switch

from commcare_connect.flags.switch_names import SOLICITATIONS
from commcare_connect.program.tests.factories import _get_default_country
from commcare_connect.solicitation.models import (
    Solicitation,
    SolicitationApplication,
    SolicitationInvitation,
    SolicitationReviewer,
)
from commcare_connect.solicitation.tables import SolicitationTable
from commcare_connect.solicitation.tasks import send_reviewer_assignment_emails
from commcare_connect.solicitation.tests.factories import (
    SolicitationApplicationFactory,
    SolicitationEvaluationCriterionFactory,
    SolicitationFactory,
    SolicitationInvitationFactory,
    SolicitationQuestionFactory,
    SolicitationReviewerFactory,
)
from commcare_connect.solicitation.views.management import SolicitationListView
from commcare_connect.users.tests.factories import MembershipFactory, OrganizationFactory, UserFactory

Status = SolicitationApplication.Status


@pytest.mark.django_db
def test_queryset_counts(rf, program_manager_org):
    solicitation = SolicitationFactory(organization=program_manager_org)
    for status in Status:
        SolicitationApplicationFactory(solicitation=solicitation, status=status)
    SolicitationInvitationFactory.create_batch(3, solicitation=solicitation)
    SolicitationReviewerFactory.create_batch(2, solicitation=solicitation)
    empty = SolicitationFactory(organization=program_manager_org)
    SolicitationApplicationFactory(status=Status.SUBMITTED)  # another org's solicitation

    request = rf.get("/")
    request.org = program_manager_org
    view = SolicitationListView()
    view.setup(request)

    counts = {s.pk: (s.response_count, s.invited_count, s.reviewer_count) for s in view.get_queryset()}

    # Every status except draft and withdrawn counts as a response; the counts don't inflate each other.
    assert counts == {solicitation.pk: (len(Status) - 2, 3, 2), empty.pk: (0, 0, 0)}


@pytest.mark.django_db
@pytest.mark.parametrize(
    "status, deadline_passed, items",
    [
        (Solicitation.Status.DRAFT, False, ["Manage Review Team", "Cancel"]),
        (Solicitation.Status.ACTIVE, False, ["Manage Review Team", "Close", "Cancel"]),
        # Past its deadline but not yet closed by update_solicitation_statuses.
        (Solicitation.Status.ACTIVE, True, ["Manage Review Team"]),
        (Solicitation.Status.CLOSED, False, ["Manage Review Team"]),
        (Solicitation.Status.CANCELLED, False, []),
    ],
)
def test_row_menu(rf, program_manager_org, status, deadline_passed, items):
    request = rf.get("/")
    request.org = program_manager_org
    solicitation = SolicitationFactory(
        organization=program_manager_org, status=status, deadline_passed=deadline_passed
    )
    table = SolicitationTable([solicitation])
    table.request = request

    links = re.findall(
        r'<a href="([^"]+)"\s*(hx-boost="false")?[^>]*>\s*(.+?)\s*</a>', table.render_actions(solicitation)
    )

    assert [title for _url, _boost, title in links] == items
    # Only the reviewers page navigates; close and cancel load their form into the list page's modal.
    assert [(url, boost) for url, boost, title in links if boost] == (
        [(step_url("reviewers", solicitation), 'hx-boost="false"')] if items else []
    )


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

    @pytest.mark.parametrize("step", ["criteria", "questions"])
    @pytest.mark.parametrize("has_rows", [False, True])
    def test_formset_step_adds_blank_row_only_when_empty(self, pm_client, draft, step, has_rows):
        if has_rows:
            SolicitationQuestionFactory(
                solicitation=draft, criterion=SolicitationEvaluationCriterionFactory(solicitation=draft)
            )

        formset = pm_client.get(step_url(step, draft)).context["form"]

        assert (len(formset.initial_forms), len(formset.extra_forms)) == ((1, 0) if has_rows else (0, 1))

    def test_criteria_step_skips_untouched_blank_row(self, pm_client, draft):
        data = {
            "criteria-TOTAL_FORMS": 1,
            "criteria-INITIAL_FORMS": 0,
            "criteria-0-label": "",
            "criteria-0-weight": "",
        }

        response = pm_client.post(step_url("criteria", draft), data)

        assert response.url == step_url("questions", draft)
        assert not draft.criteria.exists()

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
    @pytest.mark.parametrize(
        "status, deadline_passed",
        [
            (Solicitation.Status.CLOSED, False),
            (Solicitation.Status.CANCELLED, False),
            # Past its deadline but not yet closed by update_solicitation_statuses.
            (Solicitation.Status.ACTIVE, True),
        ],
    )
    @pytest.mark.parametrize("method", ["get", "post"])
    def test_finished_solicitations_are_not_editable(
        self, pm_client, program_manager_org, status, deadline_passed, method, step
    ):
        solicitation = SolicitationFactory(
            organization=program_manager_org, status=status, deadline_passed=deadline_passed
        )

        assert getattr(pm_client, method)(step_url(step, solicitation)).status_code == HTTPStatus.NOT_FOUND

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
        "status, public, deadline_passed, is_editable, is_open, reviewers_linked",
        [
            (Solicitation.Status.DRAFT, False, False, True, False, True),
            (Solicitation.Status.ACTIVE, False, False, True, True, True),
            (Solicitation.Status.ACTIVE, True, False, True, True, True),
            # Past its deadline but not yet closed by update_solicitation_statuses.
            (Solicitation.Status.ACTIVE, False, True, False, False, True),
            (Solicitation.Status.CLOSED, False, False, False, False, True),
            (Solicitation.Status.CANCELLED, False, False, False, False, False),
        ],
    )
    def test_list_links(
        self, pm_client, program_manager_org, status, public, deadline_passed, is_editable, is_open, reviewers_linked
    ):
        solicitation = SolicitationFactory(
            organization=program_manager_org,
            status=status,
            public=public,
            deadline_passed=deadline_passed,
        )

        response = pm_client.get(reverse("solicitation:list", args=(program_manager_org.slug,)))

        content = response.content.decode()
        assert (step_url("details", solicitation) in content) is is_editable
        assert (step_url("cancel", solicitation) in content) is is_editable
        assert (step_url("invitations", solicitation) in content) is is_open
        assert (step_url("close", solicitation) in content) is is_open
        assert (step_url("reviewers", solicitation) in content) is reviewers_linked

    def test_create_renders(self, pm_client, program_manager_org):
        response = pm_client.get(reverse("solicitation:create", args=(program_manager_org.slug,)))

        assert response.status_code == HTTPStatus.OK
        assert response.context["path"][-1] == {"title": "New solicitation"}


PARTICIPANT_FACTORIES = {"invitations": SolicitationInvitationFactory, "reviewers": SolicitationReviewerFactory}


@pytest.fixture
def solicitation(program_manager_org):
    return SolicitationFactory(organization=program_manager_org, status=Solicitation.Status.ACTIVE, public=False)


ACTIONS = {"close": Solicitation.Status.CLOSED, "cancel": Solicitation.Status.CANCELLED}


@pytest.mark.django_db
@pytest.mark.parametrize("action", ACTIONS)
class TestSolicitationClosure:
    @pytest.mark.parametrize(
        "status, deadline_passed, allowed_actions",
        [
            # A draft was never open to applications, so it can only be cancelled.
            (Solicitation.Status.DRAFT, False, {"cancel"}),
            (Solicitation.Status.ACTIVE, False, {"close", "cancel"}),
            # Past its deadline but not yet closed by update_solicitation_statuses.
            (Solicitation.Status.ACTIVE, True, set()),
            (Solicitation.Status.CLOSED, False, set()),
            (Solicitation.Status.CANCELLED, False, set()),
        ],
    )
    def test_transitions(
        self,
        pm_client,
        program_manager_org,
        program_manager_org_user_admin,
        action,
        status,
        deadline_passed,
        allowed_actions,
    ):
        solicitation = SolicitationFactory(
            organization=program_manager_org, status=status, deadline_passed=deadline_passed
        )

        response = pm_client.post(
            step_url(action, solicitation), {"closure_reason": "Funding withdrawn"}, HTTP_HX_REQUEST="true"
        )

        solicitation.refresh_from_db()
        if action in allowed_actions:
            assert response.headers["HX-Redirect"] == reverse("solicitation:list", args=(program_manager_org.slug,))
            assert (solicitation.status, solicitation.closure_reason, solicitation.modified_by) == (
                ACTIONS[action],
                "Funding withdrawn",
                program_manager_org_user_admin.email,
            )
            assert pm_client.get(step_url("details", solicitation)).status_code == HTTPStatus.NOT_FOUND
        else:
            assert response.status_code == HTTPStatus.NOT_FOUND
            assert (solicitation.status, solicitation.closure_reason) == (status, "")

    def test_form_only_renders_in_the_modal(self, pm_client, solicitation, action):
        url = step_url(action, solicitation)

        response = pm_client.get(url, HTTP_HX_REQUEST="true")
        assert response.status_code == HTTPStatus.OK
        # The row menu's link is boosted, which would otherwise put this URL in the address bar.
        assert response.headers["HX-Push-Url"] == "false"
        # A direct visit goes to the list page that hosts the modal.
        assert pm_client.get(url).url == reverse("solicitation:list", args=(solicitation.organization.slug,))

    @pytest.mark.parametrize(
        "switch_active, role, own_org",
        [(False, "admin", True), (True, "member", True), (True, "admin", False)],
    )
    def test_only_pm_admins_of_the_owning_org(self, client, program_manager_org, action, switch_active, role, own_org):
        organization = program_manager_org if own_org else OrganizationFactory(program_manager=True)
        solicitation = SolicitationFactory(organization=organization, status=Solicitation.Status.ACTIVE)
        client.force_login(MembershipFactory(organization=program_manager_org, role=role).user)
        # Always the PM's own org slug, so another org's solicitation is reached through this org's URLs.
        url = reverse(f"solicitation:{action}", args=(program_manager_org.slug, solicitation.solicitation_id))

        with override_switch(SOLICITATIONS, active=switch_active):
            response = client.post(url, {"closure_reason": "Funding withdrawn"})

        assert response.status_code == HTTPStatus.NOT_FOUND
        solicitation.refresh_from_db()
        assert solicitation.status == Solicitation.Status.ACTIVE

    def test_list_shows_the_reason(self, pm_client, program_manager_org, action):
        SolicitationFactory(
            organization=program_manager_org, status=ACTIONS[action], closure_reason="Funding withdrawn"
        )

        response = pm_client.get(reverse("solicitation:list", args=(program_manager_org.slug,)))

        assert "Funding withdrawn" in response.content.decode()


@pytest.fixture
def mock_send_mail():
    with patch("commcare_connect.solicitation.tasks.send_mail_async") as mock:
        yield mock


@pytest.fixture
def member(program_manager_org):
    return MembershipFactory(organization=program_manager_org, role="viewer").user


def sent_to(mock_send_mail):
    return sorted(call.kwargs["recipient_list"] for call in mock_send_mail.delay.call_args_list)


def assert_add_rejected(response, participants, expected_count, mock_send_mail):
    """The form came back with errors, and nothing was added or emailed."""
    assert response.status_code == HTTPStatus.OK
    assert response.context["form"].errors
    assert participants.count() == expected_count
    mock_send_mail.delay.assert_not_called()


@pytest.mark.django_db
class TestSolicitationInvitations:
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
        assert sent_to(mock_send_mail) == sorted(org.get_member_emails() for org in invited_orgs)

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

        assert_add_rejected(response, solicitation.invitations, int(already_invited), mock_send_mail)

    def test_list_shows_invitations_by_org_name(self, pm_client, solicitation):
        invitation = SolicitationInvitationFactory(solicitation=solicitation)
        uninvited = OrganizationFactory()

        response = pm_client.get(step_url("invitations", solicitation))

        [row] = response.context["table"].rows
        assert (row.record, row.get_cell("organization")) == (invitation, invitation.organization.name)
        choices = dict(response.context["form"].fields["organizations"].choices)
        assert choices[uninvited.pk] == uninvited.name


@pytest.mark.django_db
class TestSolicitationReviewers:
    @pytest.mark.parametrize("role", SolicitationReviewer.Role.values)
    def test_add_assigns_role_and_emails_each_person(
        self,
        pm_client,
        solicitation,
        program_manager_org_user_admin,
        member,
        mock_send_mail,
        django_capture_on_commit_callbacks,
        role,
    ):
        # The PM is a member too, so can take a role on their own solicitation.
        users = [member, program_manager_org_user_admin]

        with django_capture_on_commit_callbacks(execute=True):
            response = pm_client.post(
                step_url("reviewers", solicitation), {"users": [user.pk for user in users], "role": role}
            )

        assert response.url == step_url("reviewers", solicitation)
        assert {(a.user, a.role, a.invited_by) for a in solicitation.reviewer_assignments.all()} == {
            (user, role, program_manager_org_user_admin) for user in users
        }
        assert sent_to(mock_send_mail) == sorted([user.email] for user in users)

    @pytest.mark.parametrize("already_assigned", [True, False])
    def test_add_rejects_assigned_members_and_non_members(
        self, pm_client, solicitation, program_manager_org, member, mock_send_mail, already_assigned
    ):
        if already_assigned:
            SolicitationReviewerFactory(solicitation=solicitation, user=member)
            rejected = member
        else:
            rejected = MembershipFactory().user  # belongs to another organization

        # A valid member alongside the rejected one is not added either.
        valid = MembershipFactory(organization=program_manager_org).user
        data = {"users": [rejected.pk, valid.pk], "role": SolicitationReviewer.Role.REVIEWER}
        response = pm_client.post(step_url("reviewers", solicitation), data)

        assert_add_rejected(response, solicitation.reviewer_assignments, int(already_assigned), mock_send_mail)

    def test_list_shows_assignments_and_offers_other_members(self, pm_client, solicitation, member):
        assignment = SolicitationReviewerFactory(
            solicitation=solicitation, user=member, role=SolicitationReviewer.Role.OBSERVER
        )
        # Assigned on another solicitation only, so still offered here.
        unassigned = SolicitationReviewerFactory(
            user=MembershipFactory(organization=solicitation.organization).user
        ).user

        response = pm_client.get(step_url("reviewers", solicitation))

        [row] = response.context["table"].rows
        assert (row.record, row.get_cell("name"), row.get_cell("role")) == (assignment, member.name, "Observer")
        choices = dict(response.context["form"].fields["users"].choices)
        assert member.pk not in choices
        assert choices[unassigned.pk] == f"{unassigned.name} ({unassigned.email})"

    @pytest.mark.parametrize(
        "status, linked", [(Solicitation.Status.ACTIVE, True), (Solicitation.Status.CLOSED, False)]
    )
    def test_solicitation_crumb_links_to_details_only_while_editable(
        self, pm_client, program_manager_org, status, linked
    ):
        solicitation = SolicitationFactory(organization=program_manager_org, status=status)

        response = pm_client.get(step_url("reviewers", solicitation))

        assert response.context["path"][1].get("url") == (step_url("details", solicitation) if linked else None)

    @pytest.mark.parametrize(
        "role, email, subject",
        [
            (SolicitationReviewer.Role.REVIEWER, "reviewer@example.com", "Reviewer assignment: Malaria outreach"),
            (SolicitationReviewer.Role.OBSERVER, "observer@example.com", "Observer assignment: Malaria outreach"),
            # Nobody to write to.
            (SolicitationReviewer.Role.REVIEWER, None, None),
            (SolicitationReviewer.Role.REVIEWER, "", None),
        ],
    )
    def test_assignment_email(self, mock_send_mail, role, email, subject):
        assignment = SolicitationReviewerFactory(
            solicitation__title="Malaria outreach", user=UserFactory(email=email), role=role
        )

        send_reviewer_assignment_emails([assignment.pk])

        subjects = [call.kwargs["subject"] for call in mock_send_mail.delay.call_args_list]
        assert subjects == ([subject] if subject else [])
        assert sent_to(mock_send_mail) == ([[email]] if subject else [])


@pytest.mark.django_db
class TestSolicitationParticipants:
    """Behaviour the invitations and reviewers pages share."""

    @pytest.mark.parametrize("page", PARTICIPANT_FACTORIES)
    def test_delete_removes_selected_participants(self, pm_client, solicitation, page):
        removed, kept = PARTICIPANT_FACTORIES[page].create_batch(2, solicitation=solicitation)

        response = pm_client.post(step_url(f"delete_{page}", solicitation), {"ids": [removed.pk]})

        assert response.url == step_url(page, solicitation)
        assert list(type(kept).objects.filter(solicitation=solicitation)) == [kept]

    @pytest.mark.parametrize("page", PARTICIPANT_FACTORIES)
    @pytest.mark.parametrize("other_solicitation", [True, False])
    def test_delete_rejects_invalid_selection(self, pm_client, solicitation, page, other_solicitation):
        """Nothing selected, or an id from another solicitation, deletes nothing."""
        other = PARTICIPANT_FACTORIES[page]()
        data = {"ids": [other.pk]} if other_solicitation else {}

        response = pm_client.post(step_url(f"delete_{page}", solicitation), data)

        assert response.status_code == HTTPStatus.BAD_REQUEST
        assert type(other).objects.filter(pk=other.pk).exists()

    @pytest.mark.parametrize(
        "page, status, own_org, deadline_passed, found",
        [
            # Organizations are invited only while the solicitation is open.
            ("invitations", Solicitation.Status.DRAFT, True, False, False),
            ("invitations", Solicitation.Status.CLOSED, True, False, False),
            ("invitations", Solicitation.Status.CANCELLED, True, False, False),
            ("invitations", Solicitation.Status.ACTIVE, False, False, False),
            # Past its deadline but not yet closed by update_solicitation_statuses.
            ("invitations", Solicitation.Status.ACTIVE, True, True, False),
            # Reviewers are managed until the solicitation is cancelled.
            ("reviewers", Solicitation.Status.DRAFT, True, False, True),
            ("reviewers", Solicitation.Status.ACTIVE, True, True, True),
            ("reviewers", Solicitation.Status.CLOSED, True, False, True),
            ("reviewers", Solicitation.Status.CANCELLED, True, False, False),
            ("reviewers", Solicitation.Status.ACTIVE, False, False, False),
        ],
    )
    def test_only_own_manageable_solicitations(
        self, pm_client, program_manager_org, page, status, own_org, deadline_passed, found
    ):
        organization = program_manager_org if own_org else OrganizationFactory()
        solicitation = SolicitationFactory(organization=organization, status=status, deadline_passed=deadline_passed)
        participant = PARTICIPANT_FACTORIES[page](solicitation=solicitation)
        # Always the PM's own org slug, so another org's solicitation is reached through this org's URLs.
        url_args = (program_manager_org.slug, solicitation.solicitation_id)
        list_url = reverse(f"solicitation:{page}", args=url_args)
        delete_url = reverse(f"solicitation:delete_{page}", args=url_args)

        # An empty add re-renders the page with its errors.
        page_status = HTTPStatus.OK if found else HTTPStatus.NOT_FOUND
        assert pm_client.get(list_url).status_code == page_status
        assert pm_client.post(list_url, {}).status_code == page_status
        delete_status = HTTPStatus.FOUND if found else HTTPStatus.NOT_FOUND
        assert pm_client.post(delete_url, {"ids": [participant.pk]}).status_code == delete_status
        assert type(participant).objects.filter(pk=participant.pk).exists() is not found
