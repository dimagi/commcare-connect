import csv
import io
import logging

import httpx
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.core.files.storage import default_storage

from commcare_connect.connect_id_client import send_message
from commcare_connect.connect_id_client.models import Message
from commcare_connect.opportunity.models import OpportunityAccess
from config import celery_app

from .clustering import WorkAreaGrouper
from .const import (
    DEFAULT_BUILDING_COUNT,
    WORK_AREA_CSV_GROUP_NAME_HEADER,
    WORK_AREA_CSV_HEADERS,
    WORK_AREA_CSV_IMPLEMENTATION_AREA_HEADER,
)
from .models import OvertureRelease
from .services.implementation_area_import import (
    get_implementation_area_import_cache_key,
    import_uploaded_implementation_areas,
)
from .services.work_area_groups import get_cluster_area_cache_lock_key
from .services.work_area_import import get_import_area_cache_key, import_uploaded_work_areas

logger = logging.getLogger(__name__)

# Overture's catalog, whose "latest" member names the newest release. Kept here beside its only
# reader rather than in const.py, which is for constants more than one module shares.
OVERTURE_CATALOG_URL = "https://stac.overturemaps.org/catalog.json"
OVERTURE_CATALOG_TIMEOUT = 10


@celery_app.task(bind=True)
def import_work_areas_task(self, opp_id, file_name):
    logger.info(f"Starting Work Area import for opportunity: {opp_id}")
    try:
        return import_uploaded_work_areas(opportunity_id=opp_id, csv_source=_read_uploaded_csv(file_name))
    finally:
        cache.delete(get_import_area_cache_key(opp_id))
        default_storage.delete(file_name)


@celery_app.task(bind=True)
def import_implementation_areas_task(self, opp_id, file_name):
    logger.info(f"Starting Implementation Area import for opportunity: {opp_id}")
    try:
        return import_uploaded_implementation_areas(opportunity_id=opp_id, csv_source=_read_uploaded_csv(file_name))
    finally:
        cache.delete(get_implementation_area_import_cache_key(opp_id))
        default_storage.delete(file_name)


def _read_uploaded_csv(file_name: str) -> io.StringIO:
    with default_storage.open(file_name, "rb") as f:
        return io.StringIO(f.read().decode("utf-8-sig"))


class WorkAreaCSVExporter:
    HEADERS = {
        **WORK_AREA_CSV_HEADERS,
        "implementation_area": WORK_AREA_CSV_IMPLEMENTATION_AREA_HEADER,
        "group_name": WORK_AREA_CSV_GROUP_NAME_HEADER,
    }

    FIELD_MAP = {
        "slug": lambda wa: wa.slug,
        "ward": lambda wa: wa.ward,
        "centroid": lambda wa: f"{wa.centroid.x} {wa.centroid.y}",
        "boundary": lambda wa: wa.boundary.wkt,
        "building_count": lambda wa: wa.building_count,
        "visit_count": lambda wa: wa.expected_visit_count,
        "target_population": lambda wa: wa.target_population,
        "lga": lambda wa: (wa.case_properties or {}).get("lga", ""),
        "state": lambda wa: (wa.case_properties or {}).get("state", ""),
        "implementation_area": lambda wa: wa.implementation_area_name,
        "group_name": lambda wa: wa.group_name or "",
    }

    @classmethod
    def get_row(cls, wa):
        return [cls.FIELD_MAP[key](wa) for key in cls.HEADERS]

    @classmethod
    def rows(cls, queryset):
        buffer = io.StringIO()
        writer = csv.writer(buffer)
        headers = cls.HEADERS

        writer.writerow(headers.values())
        yield buffer.getvalue()
        buffer.seek(0)
        buffer.truncate(0)

        for wa in queryset.iterator(chunk_size=2000):
            writer.writerow(cls.get_row(wa))
            yield buffer.getvalue()
            buffer.seek(0)
            buffer.truncate(0)


@celery_app.task()
def send_work_area_assignment_notification(opportunity_access_id: int):
    access = OpportunityAccess.objects.select_related("user", "opportunity").get(pk=opportunity_access_id)
    message = Message(
        usernames=[access.user.username],
        data={
            "action": "ccc_generic_opportunity",
            "title": "New Work Areas Assigned",
            "body": "You have been assigned new work areas. Click here to begin.",
            "opportunity_uuid": str(access.opportunity.opportunity_id),
            "opportunity_status": "delivery",
            "key": "work_area_assignment",
            "session_endpoint_id": "cc_app_home",
        },
    )
    send_message(message)


@celery_app.task()
def cluster_work_areas_task(opp_id, max_buildings=DEFAULT_BUILDING_COUNT):
    lock_key = get_cluster_area_cache_lock_key(opp_id)
    with cache.lock(lock_key, timeout=1200):
        WorkAreaGrouper(opp_id, max_buildings=max_buildings).cluster_work_areas()


@celery_app.task(
    autoretry_for=(httpx.HTTPError,),
    retry_kwargs={"max_retries": 3},
    retry_backoff=True,
    retry_backoff_max=300,
    retry_jitter=True,
)
def update_overture_release():
    """
    Point OvertureRelease at Overture's newest release. Runs daily.

    A catalog that cannot be reached is transient, so it is retried; once the retries are spent the
    recorded release is left alone for the next run, which costs slightly older footprints rather
    than a map that can draw none.
    """
    catalog = fetch_overture_catalog()
    if catalog is None:
        return

    release = catalog.get("latest")
    if release is not None and release == OvertureRelease.current():
        return

    try:
        OvertureRelease.set_current(release)
    except ValidationError:
        logger.error(
            "Overture's catalog reported %r as its latest release, but it does not match the expected release format",
            release,
        )


def fetch_overture_catalog():
    response = httpx.get(OVERTURE_CATALOG_URL, timeout=OVERTURE_CATALOG_TIMEOUT)
    response.raise_for_status()

    try:
        catalog = response.json()
    except ValueError:
        catalog = None

    if not isinstance(catalog, dict):
        logger.error("Overture's catalog at %s did not answer with a JSON object", OVERTURE_CATALOG_URL)
        return None

    return catalog
