from datetime import timedelta

import pytest
from django.utils.timezone import now

from commcare_connect.solicitation.models import CLOSES_SOON_DAYS, Solicitation
from commcare_connect.solicitation.tasks import update_solicitation_statuses
from commcare_connect.solicitation.tests.factories import SolicitationFactory, SolicitationInvitationFactory
from commcare_connect.users.tests.factories import MembershipFactory

Status = Solicitation.Status
PAST = timedelta(days=-1)
FUTURE = timedelta(days=1)

pytestmark = pytest.mark.django_db

# (stored status, deadline offset, status once the deadline rule applies)
DEADLINE_CASES = [
    (Status.ACTIVE, FUTURE, Status.ACTIVE),
    (Status.ACTIVE, PAST, Status.CLOSED),
    (Status.DRAFT, PAST, Status.DRAFT),
    (Status.CANCELLED, PAST, Status.CANCELLED),
]


@pytest.mark.parametrize("status, deadline_offset, expected_status", DEADLINE_CASES)
def test_deadline_rule(status, deadline_offset, expected_status):
    solicitation = SolicitationFactory(status=status, application_deadline=now() + deadline_offset)
    solicitation.refresh_from_db()  # the stored status loads as a plain string
    expected_open = expected_status == Status.ACTIVE

    assert solicitation.effective_status == expected_status
    assert solicitation.effective_status.label == expected_status.label
    assert solicitation.is_open is expected_open
    assert Solicitation.objects.open().filter(pk=solicitation.pk).exists() is expected_open


@pytest.mark.parametrize("status, deadline_offset, expected_status", DEADLINE_CASES)
def test_update_solicitation_statuses(status, deadline_offset, expected_status):
    solicitation = SolicitationFactory(status=status, application_deadline=now() + deadline_offset)

    update_solicitation_statuses()
    update_solicitation_statuses()  # safe to re-run

    solicitation.refresh_from_db()
    assert solicitation.status == expected_status


# (go-live offset, deadline offset, status once the date rules apply)
SCHEDULE_CASES = [
    (FUTURE, 2 * FUTURE, Status.SCHEDULED),
    (PAST, FUTURE, Status.ACTIVE),
    (2 * PAST, PAST, Status.CLOSED),
]


def scheduled_solicitation(go_live_offset, deadline_offset):
    return SolicitationFactory(
        status=Status.SCHEDULED, go_live_date=now() + go_live_offset, application_deadline=now() + deadline_offset
    )


@pytest.mark.parametrize("go_live_offset, deadline_offset, expected_status", SCHEDULE_CASES)
def test_go_live_rule(go_live_offset, deadline_offset, expected_status):
    solicitation = scheduled_solicitation(go_live_offset, deadline_offset)
    solicitation.refresh_from_db()  # the stored status loads as a plain string
    expected_open = expected_status == Status.ACTIVE
    expected_scheduled = expected_status == Status.SCHEDULED

    assert solicitation.effective_status == expected_status
    assert solicitation.is_open is expected_open
    assert solicitation.is_scheduled is expected_scheduled
    assert Solicitation.objects.open().filter(pk=solicitation.pk).exists() is expected_open
    assert Solicitation.objects.scheduled().filter(pk=solicitation.pk).exists() is expected_scheduled


@pytest.mark.parametrize("go_live_offset, deadline_offset, expected_status", SCHEDULE_CASES)
def test_update_solicitation_statuses_takes_scheduled_live(
    go_live_offset, deadline_offset, expected_status, mock_send_mail
):
    solicitation = scheduled_solicitation(go_live_offset, deadline_offset)
    invited_org = MembershipFactory().organization
    SolicitationInvitationFactory(solicitation=solicitation, organization=invited_org)

    update_solicitation_statuses()
    update_solicitation_statuses()  # safe to re-run: nothing is emailed twice

    solicitation.refresh_from_db()
    assert solicitation.status == expected_status
    # Invitations held back while it was scheduled are emailed once it goes live, and not if it closed unseen.
    sent_to = [call.kwargs["recipient_list"] for call in mock_send_mail.delay.call_args_list]
    assert sent_to == ([invited_org.get_member_emails()] if expected_status == Status.ACTIVE else [])


@pytest.mark.parametrize(
    "status, deadline_offset, expected",
    [
        (Status.ACTIVE, timedelta(days=CLOSES_SOON_DAYS, minutes=-1), True),
        (Status.ACTIVE, timedelta(days=CLOSES_SOON_DAYS, minutes=1), False),
        (Status.ACTIVE, PAST, False),
        (Status.DRAFT, FUTURE, False),
    ],
)
def test_closes_soon(status, deadline_offset, expected):
    solicitation = SolicitationFactory(status=status, application_deadline=now() + deadline_offset)

    assert solicitation.closes_soon is expected


@pytest.mark.parametrize("deadline_offset, expected", [(timedelta(hours=5), 0), (timedelta(days=3, hours=5), 3)])
def test_days_until_deadline(deadline_offset, expected):
    solicitation = SolicitationFactory(application_deadline=now() + deadline_offset)

    assert solicitation.days_until_deadline == expected
