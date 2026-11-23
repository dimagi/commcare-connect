from urllib.parse import urlencode

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse

from commcare_connect.solicitation.models import Solicitation, SolicitationApplication, SolicitationApplicationAnswer
from commcare_connect.solicitation.tests.factories import SolicitationApplicationFactory, SolicitationQuestionFactory
from commcare_connect.users.tests.factories import MembershipFactory

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("solicitations_on")]

Status = SolicitationApplication.Status


def list_url(organization, **params):
    url = reverse("my_applications:list", args=[organization.slug])
    return f"{url}?{urlencode(params)}" if params else url


def listed(client, url):
    return [row.record for row in client.get(url).context["table"].page.object_list]


@pytest.fixture
def member_client(client, org_user_member):
    client.force_login(org_user_member)
    return client


def test_lists_only_the_current_organizations_applications(member_client, organization):
    draft = SolicitationApplicationFactory(organization=organization)
    submitted = SolicitationApplicationFactory(organization=organization, status=Status.SUBMITTED)
    SolicitationApplicationFactory(status=Status.SUBMITTED)

    assert set(listed(member_client, list_url(organization))) == {draft, submitted}


def test_switching_organization_changes_the_list(member_client, org_user_member, organization):
    other_org = MembershipFactory(user=org_user_member).organization
    other = SolicitationApplicationFactory(organization=other_org)
    SolicitationApplicationFactory(organization=organization)

    assert listed(member_client, list_url(other_org)) == [other]


def test_not_reachable_for_another_organization(member_client):
    application = SolicitationApplicationFactory()

    assert member_client.get(list_url(application.organization)).status_code == 404


def test_filters_by_type(member_client, organization):
    rfp = SolicitationApplicationFactory(organization=organization, solicitation__type=Solicitation.Type.RFP)
    SolicitationApplicationFactory(organization=organization, solicitation__type=Solicitation.Type.EOI)

    assert listed(member_client, list_url(organization, type=Solicitation.Type.RFP)) == [rfp]


@pytest.mark.parametrize(
    "status, shortlisted, expected",
    [
        (Status.UNDER_REVIEW, True, "Shortlisted"),
        (Status.UNDER_REVIEW, False, "Under Review"),
        # Shortlisting only shows while the application is still being reviewed.
        (Status.REJECTED, True, "Rejected"),
    ],
)
def test_applicant_status_shows_shortlisting_under_review(status, shortlisted, expected):
    application = SolicitationApplicationFactory.build(status=status, shortlisted=shortlisted)

    assert str(application.applicant_status) == expected


def test_withdraw_records_who_and_when(organization):
    application = SolicitationApplicationFactory(organization=organization, status=Status.SUBMITTED)

    application.withdraw("admin@example.com")

    application.refresh_from_db()
    assert application.status == Status.WITHDRAWN
    assert application.withdrawn_date is not None
    assert application.modified_by == "admin@example.com"


@pytest.mark.parametrize("has_applied", [True, False])
def test_sidebar_links_to_applications_once_the_organization_has_applied(member_client, organization, has_applied):
    if has_applied:
        SolicitationApplicationFactory(organization=organization)

    content = member_client.get(reverse("opportunity:list", args=[organization.slug])).content.decode()

    assert (list_url(organization) in content) is has_applied


def detail_url(application):
    return reverse("my_applications:detail", args=[application.organization.slug, application.application_id])


def withdraw_url(application):
    return reverse("my_applications:withdraw", args=[application.organization.slug, application.application_id])


@pytest.fixture
def admin_client(client, org_user_admin):
    client.force_login(org_user_admin)
    return client


def test_detail_shows_status_and_answers(member_client, organization):
    application = SolicitationApplicationFactory(organization=organization, status=Status.SUBMITTED)
    answered = SolicitationQuestionFactory(solicitation=application.solicitation, text="Describe your team")
    SolicitationQuestionFactory(solicitation=application.solicitation, text="Team size")
    SolicitationApplicationAnswer.objects.create(application=application, question=answered, answer="Ten nurses")

    response = member_client.get(detail_url(application))

    content = response.content.decode()
    assert response.status_code == 200
    assert "Submitted" in content
    assert "Ten nurses" in content
    assert "Not answered" in content


@pytest.mark.parametrize(
    "page_url", [lambda application: list_url(application.organization), detail_url], ids=["list", "detail"]
)
@pytest.mark.parametrize(
    "status, badge_class",
    [
        (Status.DRAFT, "badge-amber"),
        (Status.SUBMITTED, "positive-light"),
        (Status.UNDER_REVIEW, "badge-indigo"),
        (Status.AWARDED, "positive-light"),
        (Status.REJECTED, "negative-light"),
        (Status.WITHDRAWN, "negative-light"),
    ],
)
def test_status_badge_is_coloured_by_status(member_client, organization, page_url, status, badge_class):
    application = SolicitationApplicationFactory(organization=organization, status=status)

    content = member_client.get(page_url(application)).content.decode()

    assert f'<span class="badge badge-sm {badge_class}">{application.get_status_display()}</span>' in content


def test_detail_of_another_organization_is_not_found(member_client):
    assert member_client.get(detail_url(SolicitationApplicationFactory())).status_code == 404


def test_application_link_goes_to_the_detail(organization):
    application = SolicitationApplicationFactory(organization=organization)

    assert application.applicant_url == detail_url(application)


@pytest.mark.parametrize("status", [Status.SUBMITTED, Status.UNDER_REVIEW])
def test_admin_withdraws_before_award(admin_client, org_user_admin, organization, status):
    application = SolicitationApplicationFactory(organization=organization, status=status, shortlisted=True)

    response = admin_client.post(withdraw_url(application))

    application.refresh_from_db()
    assert response.url == detail_url(application)
    assert application.status == Status.WITHDRAWN
    assert application.withdrawn_date is not None
    assert application.modified_by == org_user_admin.email


def test_withdraw_is_allowed_after_the_deadline(admin_client, organization):
    application = SolicitationApplicationFactory(
        organization=organization, status=Status.SUBMITTED, solicitation__deadline_passed=True
    )

    admin_client.post(withdraw_url(application))

    application.refresh_from_db()
    assert application.status == Status.WITHDRAWN


@pytest.mark.parametrize("status", [Status.DRAFT, Status.AWARDED, Status.REJECTED, Status.WITHDRAWN])
def test_cannot_withdraw_unsent_or_decided_applications(admin_client, organization, status):
    application = SolicitationApplicationFactory(organization=organization, status=status)

    assert admin_client.post(withdraw_url(application)).status_code == 400
    application.refresh_from_db()
    assert application.status == status


def test_members_cannot_withdraw(member_client, organization):
    application = SolicitationApplicationFactory(organization=organization, status=Status.SUBMITTED)

    assert member_client.post(withdraw_url(application)).status_code == 404
    application.refresh_from_db()
    assert application.status == Status.SUBMITTED


def test_cannot_withdraw_another_organizations_application(admin_client):
    application = SolicitationApplicationFactory(status=Status.SUBMITTED)

    assert admin_client.post(withdraw_url(application)).status_code == 404


@pytest.mark.parametrize(
    "client_fixture, status, shown",
    [
        ("admin_client", Status.SUBMITTED, True),
        ("member_client", Status.SUBMITTED, False),
        ("admin_client", Status.AWARDED, False),
    ],
)
def test_withdraw_button_is_for_admins_of_withdrawable_applications(
    request, organization, client_fixture, status, shown
):
    application = SolicitationApplicationFactory(organization=organization, status=status)
    client = request.getfixturevalue(client_fixture)

    content = client.get(detail_url(application)).content.decode()

    assert (withdraw_url(application) in content) is shown


def answer_file_url(answer):
    application = answer.application
    return reverse(
        "my_applications:answer_file", args=[application.organization.slug, application.application_id, answer.pk]
    )


def file_answer(organization):
    application = SolicitationApplicationFactory(organization=organization, status=Status.SUBMITTED)
    question = SolicitationQuestionFactory(solicitation=application.solicitation, question_type="file")
    return SolicitationApplicationAnswer.objects.create(
        application=application, question=question, file=SimpleUploadedFile("plan.pdf", b"%PDF")
    )


def test_answer_file_downloads_for_members(member_client, organization):
    answer = file_answer(organization)

    detail = member_client.get(detail_url(answer.application)).content.decode()
    response = member_client.get(answer_file_url(answer))

    assert answer_file_url(answer) in detail
    assert b"".join(response.streaming_content) == b"%PDF"
    assert f'filename="{answer.file_name}"' in response["Content-Disposition"]


def test_answer_file_is_not_found_for_other_organizations(member_client):
    answer = file_answer(SolicitationApplicationFactory().organization)

    assert member_client.get(answer_file_url(answer)).status_code == 404
