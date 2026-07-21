import csv
import io
import logging
from collections import defaultdict

from django.contrib.gis.geos import GEOSException, GEOSGeometry
from django.core.cache import cache
from django.core.files.storage import default_storage
from django.utils.html import strip_tags
from django.utils.translation import gettext as _

from commcare_connect.connect_id_client import send_message
from commcare_connect.connect_id_client.models import Message
from commcare_connect.opportunity.models import OpportunityAccess
from config import celery_app

from .clustering import WorkAreaGrouper
from .const import DEFAULT_BUILDING_COUNT
from .models import SRID, ImplementationArea, WorkArea

logger = logging.getLogger(__name__)


def get_import_area_cache_key(opp_id: int):
    return f"work_area_import_lock_{opp_id}"


def get_implementation_area_import_cache_key(opp_id: int):
    return f"implementation_area_import_lock_{opp_id}"


def get_cluster_area_cache_lock_key(opp_id: int):
    return f"work_area_clustering_cache_lock_key_{opp_id}"


class BaseAreaCSVImporter:
    model = None
    HEADERS = {}

    def __init__(self, opp_id, csv_source):
        self.opp_id = opp_id
        self.csv_source = csv_source
        self.errors = defaultdict(list)
        self.created_count = 0

    def run(self):
        if not self._validate_all_rows(self.csv_source):
            return self._result()
        self._stream_and_insert(self.csv_source)
        self._after_insert()
        return self._result()

    def _result(self):
        if self.errors:
            return {"errors": self.errors}
        return {"created": self.created_count}

    def _validate_all_rows(self, f):
        f.seek(0)
        reader = csv.DictReader(f)
        if not self._validate_headers(reader):
            return False
        self._load_existing()
        for line_num, row in enumerate(reader, start=2):
            self._process_row(line_num, row)
        self._clear_existing()
        return len(self.errors) == 0

    def _stream_and_insert(self, f):
        f.seek(0)
        reader = csv.DictReader(f)
        self._prepare_insert()
        batch = []
        batch_size = 5000
        for row in reader:
            batch.append(self.build_instance(row))
            if len(batch) >= batch_size:
                self.model.objects.bulk_create(batch)
                self.created_count += batch_size
                batch = []
        if batch:
            self.model.objects.bulk_create(batch)
            self.created_count += len(batch)

    def _validate_headers(self, reader):
        headers = set(reader.fieldnames or [])
        missing = set(self.HEADERS.values()) - headers
        if missing:
            self._add_error(1, _("Missing columns: ") + ", ".join(sorted(missing)))
            return False
        return True

    def _process_row(self, line_num, row):
        for processor in self.get_processors():
            processor(row, line_num)

    # --- hooks for subclasses ---
    def _load_existing(self):
        pass

    def _clear_existing(self):
        pass

    def _prepare_insert(self):
        pass

    def _after_insert(self):
        pass

    def get_processors(self):
        raise NotImplementedError

    def build_instance(self, row):
        raise NotImplementedError

    # --- shared geometry parsing/validation ---
    def get_boundary(self, row):
        boundary_wkt = row.get(self.HEADERS.get("boundary"), "").strip()
        return GEOSGeometry(boundary_wkt, srid=SRID)

    def get_centroid(self, row):
        lon, lat = row.get(self.HEADERS.get("centroid")).strip().split()
        wkt = f"POINT({lon} {lat})"
        return GEOSGeometry(wkt, srid=SRID)

    def _validate_centroid(self, row, line_num):
        invalid = True
        if row:
            try:
                self.get_centroid(row)
                invalid = False
            except (ValueError, GEOSException, TypeError, AttributeError):
                pass
        if invalid:
            self._add_error(line_num, _("Centroid must be in 'lon lat' format"))
        return invalid

    def _validate_boundary(self, row, line_num):
        invalid = True
        if row:
            try:
                geom = self.get_boundary(row)
                if geom.geom_type == "Polygon":
                    invalid = False
            except (GEOSException, ValueError, TypeError, AttributeError):
                pass
        if invalid:
            self._add_error(line_num, _("Invalid WKT format for Boundary(Polygon)."))
        return invalid

    def _add_error(self, line, message):
        self.errors[message].append(line)


class WorkAreaCSVImporter(BaseAreaCSVImporter):
    model = WorkArea
    HEADERS = {
        "slug": "Area Slug",
        "ward": "Ward",
        "centroid": "Centroid",
        "boundary": "Boundary",
        "building_count": "Building Count",
        "visit_count": "Expected Visit Count",
        "target_population": "Target Population",
        "lga": "LGA",
        "state": "State",
    }
    OPTIONAL_HEADERS = {"implementation_area": "Implementation Area"}

    def __init__(self, opp_id, csv_source):
        super().__init__(opp_id, csv_source)
        self.seen_slugs = set()
        self.existing_slugs = set()
        self.implementation_area_map = {}

    def _load_existing(self):
        self.existing_slugs.update(WorkArea.objects.filter(opportunity_id=self.opp_id).values_list("slug", flat=True))

    def _clear_existing(self):
        self.existing_slugs.clear()

    def _prepare_insert(self):
        self.implementation_area_map = dict(
            ImplementationArea.objects.filter(opportunity_id=self.opp_id).values_list("name", "id")
        )

    def get_implementation_area_name(self, row):
        header = self.OPTIONAL_HEADERS["implementation_area"]
        return (row.get(header) or "").strip()

    def get_implementation_area_id(self, row):
        return self.implementation_area_map.get(self.get_implementation_area_name(row))

    def get_processors(self):
        return [
            self._validate_slug,
            self._validate_ward,
            self._validate_centroid,
            self._validate_boundary,
            self._validate_numbers,
            self._validate_extra_properties,
        ]

    def build_instance(self, row):
        buildings, visits = self.get_building_and_visit(row)
        extra_props = self.get_extra_properties(row)
        return WorkArea(
            opportunity_id=self.opp_id,
            slug=self.get_slug(row),
            ward=self.get_ward(row),
            centroid=self.get_centroid(row),
            boundary=self.get_boundary(row),
            building_count=buildings,
            expected_visit_count=visits,
            target_population=self.get_target_population(row),
            implementation_area_id=self.get_implementation_area_id(row),
            implementation_area_name=self.get_implementation_area_name(row),
            case_properties={
                "lga": extra_props.get("lga"),
                "state": extra_props.get("state"),
            },
        )

    def get_ward(self, row):
        ward = (row.get(self.HEADERS.get("ward")) or "").strip()
        return strip_tags(ward)

    def get_slug(self, row):
        slug = (row.get(self.HEADERS.get("slug")) or "").strip()
        return strip_tags(slug)

    def _get_int(self, row, key):
        raw = row.get(self.HEADERS.get(key))
        return int(raw) if raw else 0

    def get_building_and_visit(self, row):
        return self._get_int(row, "building_count"), self._get_int(row, "visit_count")

    def get_target_population(self, row):
        return self._get_int(row, "target_population")

    def get_extra_properties(self, row):
        return {
            "lga": row.get(self.HEADERS.get("lga")),
            "state": row.get(self.HEADERS.get("state")),
        }

    def _validate_slug(self, row, line_num):
        invalid = True
        slug = self.get_slug(row)
        if not slug:
            self._add_error(line_num, _("Area slug is required and it should be unique."))
        elif slug in self.seen_slugs:
            self._add_error(line_num, _("Duplicate Area slug in file"))
        elif slug in self.existing_slugs:
            self._add_error(line_num, _("Area slug already exists for this opportunity"))
        else:
            self.seen_slugs.add(slug)
            invalid = False
        return invalid

    def _validate_ward(self, row, line_num):
        invalid = True
        if self.get_ward(row):
            invalid = False
        else:
            self._add_error(line_num, _("Ward is required."))
        return invalid

    def _validate_numbers(self, row, line_num):
        invalid = True
        try:
            building, visit = self.get_building_and_visit(row)
            target_population = self.get_target_population(row)
            if building >= 0 and visit >= 0 and target_population >= 0:
                invalid = False
        except ValueError:
            pass
        if invalid:
            self._add_error(
                line_num, _("Building count, Expected visit count, and Target population must be positive integers")
            )
        return invalid

    def _validate_extra_properties(self, row, line_num):
        extra_properties = self.get_extra_properties(row)
        missing_values = [key for key, value in extra_properties.items() if not value]
        if missing_values:
            self._add_error(line_num, _("Missing values for properties: ") + ", ".join(missing_values))
        return bool(missing_values)


@celery_app.task(bind=True)
def import_work_areas_task(self, opp_id, file_name):
    logger.info(f"Starting Work Area import for opportunity: {opp_id}")
    try:
        if WorkArea.objects.filter(opportunity_id=opp_id).exists():
            return {"errors": {_("Work Areas already exist for this opportunity"): [0]}}

        with default_storage.open(file_name, "rb") as f:
            content = io.StringIO(f.read().decode("utf-8-sig"))
        importer = WorkAreaCSVImporter(opp_id, content)
        result = importer.run()
        return result
    finally:
        cache.delete(get_import_area_cache_key(opp_id))
        default_storage.delete(file_name)


class ImplementationAreaCSVImporter(BaseAreaCSVImporter):
    model = ImplementationArea
    HEADERS = {
        "name": "Implementation Area Name",
        "centroid": "Centroid",
        "boundary": "Boundary",
    }

    def __init__(self, opp_id, csv_source):
        super().__init__(opp_id, csv_source)
        self.seen_names = set()
        self.existing_names = set()

    def _load_existing(self):
        self.existing_names.update(
            ImplementationArea.objects.filter(opportunity_id=self.opp_id).values_list("name", flat=True)
        )

    def _clear_existing(self):
        self.existing_names.clear()

    def get_processors(self):
        return [self._validate_name, self._validate_centroid, self._validate_boundary]

    def get_name(self, row):
        return strip_tags((row.get(self.HEADERS.get("name")) or "").strip())

    def _validate_name(self, row, line_num):
        invalid = True
        name = self.get_name(row)
        if not name:
            self._add_error(line_num, _("Implementation Area Name is required and should be unique."))
        elif name in self.seen_names:
            self._add_error(line_num, _("Duplicate Implementation Area Name in file"))
        elif name in self.existing_names:
            self._add_error(line_num, _("Implementation Area Name already exists for this opportunity"))
        else:
            self.seen_names.add(name)
            invalid = False
        return invalid

    def build_instance(self, row):
        return ImplementationArea(
            opportunity_id=self.opp_id,
            name=self.get_name(row),
            centroid=self.get_centroid(row),
            boundary=self.get_boundary(row),
        )

    def _after_insert(self):
        # Link work areas that were uploaded before their implementation area existed (e.g. after
        # the areas were cleared and re-uploaded): match each unlinked work area by stored name.
        for area in ImplementationArea.objects.filter(opportunity_id=self.opp_id):
            WorkArea.objects.filter(
                opportunity_id=self.opp_id,
                implementation_area__isnull=True,
                implementation_area_name=area.name,
            ).update(implementation_area=area)


@celery_app.task(bind=True)
def import_implementation_areas_task(self, opp_id, file_name):
    logger.info(f"Starting Implementation Area import for opportunity: {opp_id}")
    try:
        if ImplementationArea.objects.filter(opportunity_id=opp_id).exists():
            return {"errors": {_("Implementation Areas already exist for this opportunity"): [0]}}

        with default_storage.open(file_name, "rb") as f:
            content = io.StringIO(f.read().decode("utf-8-sig"))
        importer = ImplementationAreaCSVImporter(opp_id, content)
        return importer.run()
    finally:
        cache.delete(get_implementation_area_import_cache_key(opp_id))
        default_storage.delete(file_name)


class WorkAreaCSVExporter:
    HEADERS = {
        **WorkAreaCSVImporter.HEADERS,
        "implementation_area": "Implementation Area",
        "group_name": "Work Area Group Name",
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
