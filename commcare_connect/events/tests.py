from rest_framework.test import APIClient

from commcare_connect.opportunity.models import Opportunity
from commcare_connect.users.models import User

from .models import Event


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
