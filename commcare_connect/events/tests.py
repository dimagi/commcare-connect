from rest_framework.test import APIClient

from commcare_connect.opportunity.models import Opportunity
from commcare_connect.users.models import User

from .models import Event
from .tasks import EventQueue, process_events_batch


def test_post_events(mobile_user_with_connect_link: User, api_client: APIClient, opportunity: Opportunity):
    api_client.force_authenticate(mobile_user_with_connect_link)
    response = api_client.post(
        "/api/events/",
        data=[
            {
                "event_type": "invalid_event_name",
                "user": mobile_user_with_connect_link.pk,
                "opportunity": opportunity.pk,
            }
        ],
        format="json",
    )
    assert response.status_code == 400
    assert Event.objects.count() == 0
    response = api_client.post(
        "/api/events/",
        data=[
            {
                "event_type": Event.Type.INVITE_SENT,
                "user": mobile_user_with_connect_link.pk,
                "opportunity": opportunity.pk,
            },
            {
                "event_type": Event.Type.RECORDS_APPROVED,
                "user": mobile_user_with_connect_link.pk,
                "opportunity": opportunity.pk,
            },
        ],
        format="json",
    )
    assert response.status_code == 201
    assert Event.objects.count() == 2


def test_event_queue(mobile_user_with_connect_link: User, opportunity: Opportunity):
    event_queue = EventQueue()
    assert event_queue.pop() == []

    # queue the event
    event = Event(event_type=Event.Type.INVITE_SENT, user=mobile_user_with_connect_link, opportunity=opportunity)
    event.track()
    queued_events = event_queue.pop()
    process_events_batch()
    assert len(queued_events) == 1
    assert Event.objects.count() == 0
    # process the batch
    event.track()
    process_events_batch()
    assert Event.objects.count() == 1
    assert Event.objects.first().user == event.user
