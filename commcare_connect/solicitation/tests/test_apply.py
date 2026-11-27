import re
from unittest.mock import patch
from urllib.parse import urlencode

import pytest
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from django.utils.timezone import now

from commcare_connect.organization.models import Organization
from commcare_connect.solicitation.forms import ApplicationAnswersForm, validate_answer_file_size
from commcare_connect.solicitation.models import Solicitation, SolicitationApplication, SolicitationQuestion
from commcare_connect.solicitation.services import ApplicationNotEditable, save_application
from commcare_connect.solicitation.tasks import send_application_submitted_email
from commcare_connect.solicitation.tests.factories import (
    SolicitationApplicationFactory,
    SolicitationFactory,
    SolicitationInvitationFactory,
    SolicitationQuestionFactory,
)
from commcare_connect.users.helpers import CURRENT_ORG_SESSION_KEY
from commcare_connect.users.tests.factories import MembershipFactory, OrganizationFactory

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("solicitations_on")]

QuestionType = SolicitationQuestion.QuestionType


@pytest.fixture(autouse=True)
def submitted_email():
    # Tests don't run a Celery broker; the email itself is tested by calling the task directly.
    with patch("commcare_connect.solicitation.services.send_application_submitted_email") as task:
        yield task


@pytest.fixture
def solicitation():
    return SolicitationFactory(status=Solicitation.Status.ACTIVE, public=True, title="Malaria outreach")


@pytest.fixture
def admin_org(user):
    return MembershipFactory(user=user, role="admin").organization


@pytest.fixture
def applicant(client, user, admin_org):
    client.force_login(user)
    return client


def apply_url(solicitation, organization=None):
    url = reverse("marketplace:apply", args=[solicitation.solicitation_id])
    return f"{url}?{urlencode({'organization': organization.slug})}" if organization else url


def question(solicitation, question_type=QuestionType.TEXT, **kwargs):
    return SolicitationQuestionFactory(solicitation=solicitation, question_type=question_type, **kwargs)


def field(q):
    return f"question_{q.pk}"


def post_application(client, *, solicitation, organization, data):
    """Posts the Apply form as ``organization``, checks it redirected, and returns the saved application."""
    response = client.post(apply_url(solicitation, organization), data)
    assert response.status_code == 302
    return SolicitationApplication.objects.get(solicitation=solicitation, organization=organization)


def test_logged_out_visitor_is_sent_to_log_in(client, solicitation):
    response = client.get(apply_url(solicitation))

    assert response.status_code == 302
    assert response.url.startswith(reverse("account_login"))
    assert "next=" in response.url


def test_detail_page_links_to_apply(client, solicitation):
    content = client.get(reverse("marketplace:detail", args=[solicitation.solicitation_id])).content.decode()

    assert apply_url(solicitation) in content


@pytest.mark.parametrize(
    "role, posting_org, expected", [("admin", False, True), ("member", False, False), ("admin", True, False)]
)
def test_only_admins_of_other_orgs_can_apply_as_them(client, user, solicitation, role, posting_org, expected):
    organization = solicitation.organization if posting_org else OrganizationFactory()
    MembershipFactory(user=user, organization=organization, role=role)
    client.force_login(user)

    organizations = client.get(apply_url(solicitation)).context["organizations"]

    assert (organization in organizations) is expected


@pytest.mark.parametrize(
    "kwargs",
    [
        {"status": Solicitation.Status.DRAFT, "public": True},
        {"status": Solicitation.Status.ACTIVE, "public": False},
    ],
)
def test_draft_or_uninvited_private_solicitation_is_not_found(applicant, kwargs):
    solicitation = SolicitationFactory(**kwargs)

    assert applicant.get(apply_url(solicitation)).status_code == 404


@pytest.mark.parametrize(
    "kwargs",
    [
        {"status": Solicitation.Status.ACTIVE, "deadline_passed": True},
        {"status": Solicitation.Status.CLOSED},
        {"status": Solicitation.Status.CANCELLED},
    ],
)
def test_closed_solicitation_says_it_closed(applicant, kwargs):
    solicitation = SolicitationFactory(public=True, **kwargs)

    response = applicant.get(apply_url(solicitation))

    assert response.status_code == 200
    assert response.context["closed"] is True
    assert "form" not in response.context


def test_submitting_after_the_deadline_says_it_closed(applicant, solicitation, admin_org):
    text = question(solicitation, required=False)
    application = post_application(
        applicant, solicitation=solicitation, organization=admin_org, data={field(text): "Draft", "save": ""}
    )
    Solicitation.objects.filter(pk=solicitation.pk).update(application_deadline=now())

    response = applicant.post(apply_url(solicitation, admin_org), {field(text): "Changed", "submit": ""})

    assert response.status_code == 200
    assert response.context["closed"] is True
    assert response.context["application"] == application
    application.refresh_from_db()
    assert application.status == SolicitationApplication.Status.DRAFT
    assert application.answers.get(question=text).answer == "Draft"


def test_only_organization_is_picked_for_you(applicant, solicitation, admin_org):
    response = applicant.get(apply_url(solicitation))

    assert response.context["organization"] == admin_org
    assert "form" in response.context


def test_several_organizations_ask_which_to_apply_as(applicant, user, solicitation):
    MembershipFactory(user=user, role="admin")

    response = applicant.get(apply_url(solicitation))

    assert response.context["organization"] is None
    assert "form" not in response.context
    # The login page's layout, without the app's menus.
    assert "account/select_organization.html" in [template.name for template in response.templates]


def test_chosen_organization_cannot_be_switched_on_the_page(applicant, user, solicitation, admin_org):
    MembershipFactory(user=user, role="admin")

    response = applicant.get(apply_url(solicitation, organization=admin_org))

    assert "form" in response.context
    assert 'name="organization"' not in response.content.decode()
    assert applicant.session[CURRENT_ORG_SESSION_KEY] == admin_org.slug


def test_questions_are_numbered_under_a_progress_bar(applicant, solicitation):
    question(solicitation)
    question(solicitation)

    content = applicant.get(apply_url(solicitation)).content.decode()

    assert "Question 1" in content
    assert "Question 2" in content
    assert content.count("data-question") == 2
    assert 'x-data="applicationProgress()"' in content


def test_save_draft_keeps_answers_without_requiring_them(applicant, user, solicitation, admin_org):
    answered = question(solicitation, required=True)
    unanswered = question(solicitation, required=True)

    application = post_application(
        applicant, solicitation=solicitation, organization=admin_org, data={field(answered): "Our plan", "save": ""}
    )

    assert application.status == SolicitationApplication.Status.DRAFT
    assert application.submitted_date is None
    assert application.submitter_email == user.email
    assert {answer.question_id: answer.answer for answer in application.answers.all()} == {answered.pk: "Our plan"}
    assert not application.answers.filter(question=unanswered).exists()


def test_submit_requires_required_answers(applicant, solicitation, admin_org, submitted_email):
    required = question(solicitation, required=True)
    question(solicitation, required=False)

    response = applicant.post(apply_url(solicitation, admin_org), {"submit": ""})

    assert response.status_code == 200
    assert list(response.context["form"].errors) == [field(required)]
    # Not even a draft: the organization hasn't saved anything yet.
    assert not SolicitationApplication.objects.exists()
    submitted_email.delay_on_commit.assert_not_called()


def test_too_large_file_is_rejected_without_creating_an_application(applicant, solicitation, admin_org):
    upload = question(solicitation, QuestionType.FILE, required=False)

    with patch("commcare_connect.solicitation.forms.MAX_ANSWER_FILE_SIZE_MB", 0):
        response = applicant.post(
            apply_url(solicitation, admin_org), {field(upload): SimpleUploadedFile("plan.pdf", b"%PDF"), "save": ""}
        )

    assert list(response.context["form"].errors) == [field(upload)]
    assert not SolicitationApplication.objects.exists()


@pytest.mark.parametrize("size_mb, valid", [(25, True), (25.01, False)])
def test_answer_files_are_capped_at_25_mb(size_mb, valid):
    file = SimpleUploadedFile("plan.pdf", b"")
    file.size = int(size_mb * 1024 * 1024)

    if valid:
        validate_answer_file_size(file)
    else:
        with pytest.raises(ValidationError):
            validate_answer_file_size(file)


def test_submit_requires_a_file_when_none_was_uploaded(applicant, solicitation, admin_org):
    upload = question(solicitation, QuestionType.FILE, required=True)

    response = applicant.post(apply_url(solicitation, admin_org), {"submit": ""})

    assert list(response.context["form"].errors) == [field(upload)]


def test_submit_saves_each_answer_type_and_emails_the_submitter(
    applicant, user, solicitation, admin_org, submitted_email
):
    text = question(solicitation)
    number = question(solicitation, QuestionType.NUMBER)
    choice = question(solicitation, QuestionType.CHOICE, choice_options=["North", "South"])
    date = question(solicitation, QuestionType.DATE)
    upload = question(solicitation, QuestionType.FILE)
    data = {
        field(text): "Our plan",
        field(number): "12.5",
        field(choice): "South",
        field(date): "2027-01-15",
        field(upload): SimpleUploadedFile("plan.pdf", b"%PDF"),
        "submit": "",
    }

    response = applicant.post(apply_url(solicitation, admin_org), data)

    application = SolicitationApplication.objects.get(solicitation=solicitation, organization=admin_org)
    assert response.url == application.applicant_url
    assert application.status == SolicitationApplication.Status.SUBMITTED
    assert application.submitted_date is not None
    assert application.submitted_by == user
    answers = {answer.question_id: answer for answer in application.answers.all()}
    assert [answers[q.pk].answer for q in (text, number, choice, date)] == ["Our plan", "12.5", "South", "2027-01-15"]
    assert answers[upload.pk].file.name.endswith(".pdf")
    submitted_email.delay_on_commit.assert_called_once_with(application.pk)


def test_confirmation_email_goes_to_the_submitter_with_a_full_link(mock_send_mail):
    application = SolicitationApplicationFactory(status=SolicitationApplication.Status.SUBMITTED, submitted_date=now())

    send_application_submitted_email(application.pk)

    email = mock_send_mail.delay.call_args.kwargs
    assert mock_send_mail.delay.call_count == 1
    assert email["recipient_list"] == [application.submitter_email]
    assert re.search(rf"https?://[^/\s]+{re.escape(application.applicant_url)}", email["message"])


def test_saved_file_is_kept_when_submitting_without_a_new_one(applicant, solicitation, admin_org):
    upload = question(solicitation, QuestionType.FILE, required=True)
    applicant.post(
        apply_url(solicitation, admin_org), {field(upload): SimpleUploadedFile("plan.pdf", b"%PDF"), "save": ""}
    )

    application = post_application(applicant, solicitation=solicitation, organization=admin_org, data={"submit": ""})

    assert application.status == SolicitationApplication.Status.SUBMITTED
    assert application.answers.get(question=upload).file


def test_replacing_a_file_deletes_the_old_one_once_committed(
    applicant, solicitation, admin_org, django_capture_on_commit_callbacks
):
    upload = question(solicitation, QuestionType.FILE, required=False)
    application = post_application(
        applicant,
        solicitation=solicitation,
        organization=admin_org,
        data={field(upload): SimpleUploadedFile("old.pdf", b"%PDF"), "save": ""},
    )
    old_file = application.answers.get(question=upload).file
    storage, old_name = old_file.storage, old_file.name

    with django_capture_on_commit_callbacks(execute=True):
        post_application(
            applicant,
            solicitation=solicitation,
            organization=admin_org,
            data={field(upload): SimpleUploadedFile("new.pdf", b"%PDF"), "save": ""},
        )

    assert not storage.exists(old_name)
    assert storage.exists(application.answers.get(question=upload).file.name)


def test_draft_answers_come_back_on_the_form(applicant, solicitation, admin_org):
    text = question(solicitation)
    applicant.post(apply_url(solicitation, admin_org), {field(text): "Our plan", "save": ""})

    form = applicant.get(apply_url(solicitation, admin_org)).context["form"]

    assert form[field(text)].value() == "Our plan"


def test_clearing_an_answer_removes_it(applicant, solicitation, admin_org):
    text = question(solicitation, required=False)
    applicant.post(apply_url(solicitation, admin_org), {field(text): "Our plan", "save": ""})

    applicant.post(apply_url(solicitation, admin_org), {field(text): "", "save": ""})

    assert not SolicitationApplication.objects.get(organization=admin_org).answers.exists()


@pytest.mark.parametrize(
    "status", [SolicitationApplication.Status.SUBMITTED, SolicitationApplication.Status.WITHDRAWN]
)
def test_sent_application_cannot_be_changed(applicant, solicitation, admin_org, status):
    SolicitationApplicationFactory(solicitation=solicitation, organization=admin_org, status=status)

    page = applicant.get(apply_url(solicitation, admin_org))
    post = applicant.post(apply_url(solicitation, admin_org), {"submit": ""})

    assert "form" not in page.context
    # Shown as it now stands, as after a second click on Submit, rather than an error page.
    assert post.url == apply_url(solicitation, admin_org)


def test_submit_sent_by_another_request_shows_the_application(applicant, solicitation, admin_org):
    with patch("commcare_connect.solicitation.views.apply.save_application", side_effect=ApplicationNotEditable):
        response = applicant.post(apply_url(solicitation, admin_org), {"submit": ""})

    assert response.url == apply_url(solicitation, admin_org)


@pytest.mark.parametrize(
    "status, heading",
    [(Solicitation.Status.CLOSED, "This solicitation has closed"), (Solicitation.Status.CANCELLED, "was cancelled")],
)
def test_sent_application_is_shown_after_the_solicitation_closes(applicant, admin_org, status, heading):
    solicitation = SolicitationFactory(public=True, status=status)
    SolicitationApplicationFactory(
        solicitation=solicitation,
        organization=admin_org,
        status=SolicitationApplication.Status.SUBMITTED,
        submitted_date=now(),
    )

    content = applicant.get(apply_url(solicitation, admin_org)).content.decode()

    assert "submitted its application" in content
    assert heading not in content


def test_cancelled_solicitation_says_it_was_cancelled(applicant):
    solicitation = SolicitationFactory(public=True, status=Solicitation.Status.CANCELLED)

    content = applicant.get(apply_url(solicitation)).content.decode()

    assert "This solicitation was cancelled" in content
    assert "stopped taking applications" not in content


def test_private_solicitation_does_not_offer_to_create_an_organization(client, user):
    # A member, not an admin, of an invited organization can see the solicitation but not apply.
    solicitation = SolicitationFactory(status=Solicitation.Status.ACTIVE, public=False)
    invited = MembershipFactory(user=user, role="member").organization
    SolicitationInvitationFactory(solicitation=solicitation, organization=invited)
    client.force_login(user)

    response = client.get(apply_url(solicitation))

    content = response.content.decode()
    assert response.status_code == 200
    assert "Only invited organizations can apply" in content
    assert response.context["create_org_url"] not in content


def test_invited_organization_applies_to_a_private_solicitation(applicant, admin_org, submitted_email):
    solicitation = SolicitationFactory(status=Solicitation.Status.ACTIVE, public=False)
    SolicitationInvitationFactory(solicitation=solicitation, organization=admin_org)

    application = post_application(applicant, solicitation=solicitation, organization=admin_org, data={"submit": ""})

    assert application.status == SolicitationApplication.Status.SUBMITTED


def test_private_solicitation_takes_only_invited_organizations(user, admin_org):
    MembershipFactory(user=user, role="admin")
    solicitation = SolicitationFactory(status=Solicitation.Status.ACTIVE, public=False)
    SolicitationInvitationFactory(solicitation=solicitation, organization=admin_org)

    assert list(solicitation.applicant_organizations(user)) == [admin_org]


def test_one_application_per_organization(applicant, solicitation, admin_org, submitted_email):
    applicant.post(apply_url(solicitation, admin_org), {"save": ""})
    applicant.post(apply_url(solicitation, admin_org), {"submit": ""})
    second_submit = applicant.post(apply_url(solicitation, admin_org), {"submit": ""})

    assert second_submit.url == apply_url(solicitation, admin_org)
    assert SolicitationApplication.objects.filter(solicitation=solicitation, organization=admin_org).count() == 1
    assert submitted_email.delay_on_commit.call_count == 1


def test_cannot_post_as_an_organization_the_user_cannot_apply_as(client, user, solicitation):
    organization = MembershipFactory(user=user, role="member").organization
    client.force_login(user)

    response = client.post(apply_url(solicitation, organization), {"submit": ""})

    assert response.status_code == 400
    assert not SolicitationApplication.objects.exists()


def test_link_to_an_organization_the_user_no_longer_administers_asks_which_to_apply_as(
    applicant, user, solicitation, admin_org
):
    former = MembershipFactory(user=user, role="member").organization

    response = applicant.get(apply_url(solicitation, former))

    assert response.context["organization"] is None
    assert response.context["organizations"] == [admin_org]
    assert "form" not in response.context


def test_new_user_creates_an_unverified_org_and_can_submit(client, user, solicitation):
    client.force_login(user)
    response = client.get(apply_url(solicitation))
    assert response.context["organizations"] == []

    created = client.post(response.context["create_org_url"], {"name": "Fresh Applicants"})
    organization = Organization.objects.get(name="Fresh Applicants")
    assert created.url == apply_url(solicitation)
    assert organization.verified is False

    application = post_application(client, solicitation=solicitation, organization=organization, data={"submit": ""})

    assert application.status == SolicitationApplication.Status.SUBMITTED


class TestSaveApplication:
    def answers(self, application, data):
        form = ApplicationAnswersForm(data, application=application, require_answers=False)
        assert form.is_valid(), form.errors
        return form

    def test_creates_the_application_with_its_answers(self, user, solicitation, admin_org, submitted_email):
        text = question(solicitation)
        unsaved = SolicitationApplication(solicitation=solicitation, organization=admin_org)

        application = save_application(
            solicitation=solicitation,
            organization=admin_org,
            answers=self.answers(unsaved, {field(text): "Our plan"}),
            user=user,
            submit=True,
        )

        assert application.status == SolicitationApplication.Status.SUBMITTED
        assert application.submitted_by == user
        assert application.answers.get(question=text).answer == "Our plan"
        submitted_email.delay_on_commit.assert_called_once_with(application.pk)

    def test_sent_application_is_not_changed(self, user, solicitation, admin_org):
        sent = SolicitationApplicationFactory(
            solicitation=solicitation, organization=admin_org, status=SolicitationApplication.Status.SUBMITTED
        )
        text = question(solicitation)

        with pytest.raises(ApplicationNotEditable):
            save_application(
                solicitation=solicitation,
                organization=admin_org,
                answers=self.answers(sent, {field(text): "Changed"}),
                user=user,
                submit=False,
            )

        assert not sent.answers.exists()
