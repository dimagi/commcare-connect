from unittest.mock import MagicMock, patch

import pytest
from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse

from commcare_connect.flags.flag_names import MICROPLANNING
from commcare_connect.flags.models import Flag


@patch("commcare_connect.microplanning.views.import_work_areas_task.delay")
@pytest.mark.django_db
def test_upload_work_area_double_post_locks(
    mock_delay,
    client,
    org_user_admin,
    opportunity,
):
    flag, _ = Flag.objects.get_or_create(name=MICROPLANNING)
    flag.opportunities.add(opportunity)
    flag.save()
    client.force_login(org_user_admin)
    cache.clear()

    mock_task = MagicMock()
    mock_task.id = "task-123"
    mock_delay.return_value = mock_task

    csv_file = SimpleUploadedFile(
        "test.csv",
        b"Area Slug,Ward,Centroid,Boundary,Building Count,Expected Visit Count\n",
        content_type="text/csv",
    )

    url = reverse(
        "microplanning:upload_work_areas",
        kwargs={
            "org_slug": opportunity.organization.slug,
            "opp_id": opportunity.opportunity_id,
        },
    )
    response1 = client.post(url, {"csv_file": csv_file})
    assert response1.status_code == 302
    messages = list(response1.wsgi_request._messages)

    assert "task_id=task-123" in response1.url
    assert mock_delay.call_count == 1
    assert "Work Area upload has been started." in str(messages[0])

    # should not allow a second upload while the first one is "in progress"
    response2 = client.post(url, {"csv_file": csv_file})
    assert response2.status_code == 302
    messages = list(response2.wsgi_request._messages)
    assert "An import for this opportunity is already in progress." in str(messages[1])
    assert "task_id" not in response2.url
    assert mock_delay.call_count == 1  # Should still be 1, not called again
