import csv
import io
import logging

from django.contrib.gis.geos import GEOSException, GEOSGeometry
from django.core.cache import cache
from django.db import transaction
from django.utils.translation import gettext as _

from config import celery_app

from .models import WorkArea

logger = logging.getLogger(__name__)


def get_import_area_cache_key(opp_id: int):
    return f"work_area_import_lock_{opp_id}"


class WorkAreaCSVImporter:
    HEADERS = {
        "slug": "Area Slug",
        "ward": "Ward",
        "centroid": "Centroid",
        "boundary": "Boundary",
        "building_count": "Building Count",
        "visit_count": "Expected Visit Count",
    }

    def __init__(self, opp_id, csv_content):
        self.opp_id = opp_id
        self.csv_content = csv_content
        self.errors = {}
        self.work_areas_to_create = []
        self.seen_slugs = set()

    def run(self):
        reader = csv.DictReader(io.StringIO(self.csv_content))
        if not self._validate_headers(reader):
            return self._result()
        self.existing_slugs = set(WorkArea.objects.filter(opportunity_id=self.opp_id).values_list("slug", flat=True))
        for line_num, row in enumerate(reader, start=2):
            self._process_row(line_num, row)

        self._bulk_insert()
        return self._result()

    def _result(self):
        if self.errors:
            return {"errors": self.errors}
        return {"created": len(self.work_areas_to_create)}

    def _validate_headers(self, reader):
        headers = set(reader.fieldnames or [])
        missing = set(self.HEADERS.values()) - headers
        if missing:
            self._add_error(
                1,
                f"Missing columns: {', '.join(sorted(missing))}",
            )
            return False
        return True

    def _process_row(self, line_num, row):
        new_area = WorkArea(opportunity_id=self.opp_id)
        invalid_row = False
        processors = [
            self._process_slug,
            self._process_ward,
            self._parse_centroid,
            self._parse_boundary,
            self._process_numbers,
        ]
        for processor in processors:
            invalid_row |= processor(row, line_num, new_area)
        if not invalid_row:
            self.work_areas_to_create.append(new_area)

    def _process_slug(self, row, line_num, area):
        invalid = True
        slug = (row.get(self.HEADERS.get("slug")) or "").strip()
        if not slug:
            self._add_error(line_num, _("Area slug is required and it should be unique."))
        elif slug in self.seen_slugs:
            self._add_error(line_num, _("Duplicate Area slug in file"))
        elif slug in self.existing_slugs:
            self._add_error(line_num, _("Area slug already exists for this opportunity"))
        else:
            self.seen_slugs.add(slug)
            area.slug = slug
            invalid = False

        return invalid

    def _process_ward(self, row, line_num, area):
        invalid = True
        ward = (row.get(self.HEADERS.get("ward")) or "").strip()
        if ward:
            area.ward = ward
            invalid = False
        else:
            self._add_error(line_num, _("Ward is required."))
        return invalid

    def _parse_centroid(self, row, line_num, area):
        invalid = True
        if row:
            try:
                lon, lat = row.get(self.HEADERS.get("centroid")).strip().split()
                wkt = f"POINT({lon} {lat})"
                point = GEOSGeometry(wkt, srid=4326)
                area.centroid = point
                invalid = False
            except (ValueError, GEOSException, TypeError, AttributeError):
                pass

        if invalid:
            self._add_error(line_num, _("Centroid must be in 'lon lat' format"))
        return invalid

    def _parse_boundary(self, row, line_num, area):
        invalid = True
        if row:
            try:
                geom = GEOSGeometry(row.get(self.HEADERS.get("boundary")), srid=4326)
                if geom.geom_type == "Polygon":
                    invalid = False
                    area.boundary = geom
            except (GEOSException, ValueError, TypeError):
                pass
        if invalid:
            self._add_error(line_num, _("Invalid WKT format for Boundary(Polygon)."))

        return invalid

    def _process_numbers(self, row, line_num, area):
        invalid = True
        building_raw = row.get(self.HEADERS.get("building_count"))
        visit_raw = row.get(self.HEADERS.get("visit_count"))
        try:
            building = int(building_raw) if building_raw else 0
            visit = int(visit_raw) if visit_raw else 0

            if building >= 0 and visit >= 0:
                area.building_count = building
                area.expected_visit_count = visit
                invalid = False
        except ValueError:
            pass

        if invalid:
            self._add_error(line_num, _("Building count and Expected visit count must be postive integers"))

        return invalid

    def _bulk_insert(self):
        if not self.work_areas_to_create or self.errors:
            return

        with transaction.atomic():
            WorkArea.objects.bulk_create(
                self.work_areas_to_create,
                batch_size=1000,
            )

    def _add_error(self, line, message):
        if message not in self.errors:
            self.errors[message] = []
        self.errors[message].append(line)


@celery_app.task()
def import_work_areas_task(opp_id, csv_content):
    logger.info(f"Importing work areas for the opportunity: {opp_id}")
    if WorkArea.objects.filter(opportunity_id=opp_id).exists():
        return {"errors": {[_("Work Areas already exist for this opportunity."), [0]]}}

    try:
        importer = WorkAreaCSVImporter(opp_id, csv_content)
        return importer.run()
    finally:
        cache.delete(get_import_area_cache_key(opp_id))
