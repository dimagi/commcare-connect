from datetime import timedelta

import pytest
from django.utils.timezone import now

from commcare_connect.solicitation.models import Solicitation
from commcare_connect.solicitation.tasks import update_solicitation_statuses
from commcare_connect.solicitation.tests.factories import SolicitationFactory

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
