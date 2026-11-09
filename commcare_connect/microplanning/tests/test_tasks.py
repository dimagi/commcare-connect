from unittest import mock

import httpx
import pytest
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage

from commcare_connect.microplanning.const import DEFAULT_BUILDING_COUNT
from commcare_connect.microplanning.models import OvertureRelease, WorkArea
from commcare_connect.microplanning.tasks import (
    OVERTURE_CATALOG_URL,
    cluster_work_areas_task,
    import_work_areas_task,
    send_work_area_assignment_notification,
    update_overture_release,
)
from commcare_connect.opportunity.tests.factories import OpportunityAccessFactory


@pytest.mark.django_db
def test_import_work_areas_task_bom_csv(opportunity):
    CENTROID = "77.1 28.6"
    POLYGON = "POLYGON((77 28, 78 28, 78 29, 77 29, 77 28))"
    headers = "Area Slug,Ward,Centroid,Boundary,Building Count,Expected Visit Count,Target Population,LGA,State\r\n"
    row = f'area-bom,ward,{CENTROID},"{POLYGON}",5,6,100,LGA1,State1\r\n'
    bom_bytes = ("\ufeff" + headers + row).encode("utf-8")

    file_name = "test-bom-upload.csv"
    default_storage.save(file_name, ContentFile(bom_bytes))
    result = import_work_areas_task.apply(args=[opportunity.id, file_name]).get()

    assert result["created"] == 1
    assert WorkArea.objects.filter(slug="area-bom").exists()


@pytest.mark.django_db
def test_send_work_area_assignment_notification(opportunity):
    access = OpportunityAccessFactory(opportunity=opportunity)
    with mock.patch("commcare_connect.microplanning.tasks.send_message") as send_message_mock:
        send_work_area_assignment_notification(access.pk)

    send_message_mock.assert_called_once()
    message = send_message_mock.call_args.args[0]
    assert message.usernames == [access.user.username]
    assert message.data["key"] == "work_area_assignment"
    assert message.data["opportunity_uuid"] == str(opportunity.opportunity_id)
    assert message.data["title"]
    assert message.data["body"]


@mock.patch("commcare_connect.microplanning.tasks.cache")
@mock.patch("commcare_connect.microplanning.tasks.WorkAreaGrouper")
def test_cluster_work_areas_task_forwards_max_buildings(mock_grouper, mock_cache):
    cluster_work_areas_task(opp_id=1, max_buildings=250)

    mock_grouper.assert_called_once_with(1, max_buildings=250)
    mock_grouper.return_value.cluster_work_areas.assert_called_once()


@mock.patch("commcare_connect.microplanning.tasks.cache")
@mock.patch("commcare_connect.microplanning.tasks.WorkAreaGrouper")
def test_cluster_work_areas_task_defaults_max_buildings(mock_grouper, mock_cache):

    cluster_work_areas_task(opp_id=1)

    mock_grouper.assert_called_once_with(1, max_buildings=DEFAULT_BUILDING_COUNT)


@pytest.mark.django_db
def test_update_overture_release_records_the_latest_release(httpx_mock):
    httpx_mock.add_response(url=OVERTURE_CATALOG_URL, json={"latest": "2026-09-17.0"})

    update_overture_release()

    assert OvertureRelease.current() == "2026-09-17.0"


@pytest.mark.parametrize(
    "catalog_answer",
    [
        {"json": {}},
        {"json": {"latest": None}},
        {"json": {"latest": ""}},
        {"json": {"latest": "latest"}},
        {"json": {"latest": "2026-09-17"}},  # a date, but no revision
        {"json": {"latest": "../../etc/passwd"}},
        {"json": {"latest": ["2026-09-17.0"]}},
        {"text": "<html>502 Bad Gateway</html>"},  # answered, but not with JSON
        {"text": "null"},  # JSON, but nothing to read "latest" off
        {"json": []},
        {"json": "2026-09-17.0"},
    ],
)
def test_a_release_we_cannot_use_is_not_recorded(httpx_mock, overture_release, catalog_answer):
    httpx_mock.add_response(url=OVERTURE_CATALOG_URL, **catalog_answer)

    update_overture_release()

    assert OvertureRelease.current() == overture_release


def test_a_catalog_we_cannot_reach_leaves_the_previous_release_standing(httpx_mock, overture_release):
    httpx_mock.add_response(url=OVERTURE_CATALOG_URL, status_code=503)

    with pytest.raises(httpx.HTTPStatusError):
        update_overture_release()

    assert OvertureRelease.current() == overture_release


@pytest.mark.django_db
def test_the_release_is_rewritten_in_place_rather_than_accumulating_rows(httpx_mock):
    for release in ("2026-09-17.0", "2026-10-22.0"):
        httpx_mock.add_response(url=OVERTURE_CATALOG_URL, json={"latest": release})
        update_overture_release()

    assert OvertureRelease.objects.count() == 1
    assert OvertureRelease.current() == "2026-10-22.0"
