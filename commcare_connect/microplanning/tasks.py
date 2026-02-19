import csv
import io
import logging

from django.contrib.gis.geos import GEOSException, GEOSGeometry
from django.core.cache import cache
from django.core.files.storage import default_storage
from django.utils.html import strip_tags
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

    def __init__(self, opp_id, csv_source):
        self.opp_id = opp_id
        self.csv_source = csv_source
        self.errors = {}
        self.seen_slugs = set()
        self.created_count = 0

    def _validate_all_rows(self, f):
        f.seek(0)
        reader = csv.DictReader(f)
        if not self._validate_headers(reader):
            return False

        self.existing_slugs = set(WorkArea.objects.filter(opportunity_id=self.opp_id).values_list("slug", flat=True))
        for line_num, row in enumerate(reader, start=2):
            self._process_row(line_num, row)

        self.existing_slugs = None
        return len(self.errors) == 0

    def _stream_and_insert(self, f):
        f.seek(0)
        reader = csv.DictReader(f)
        batch = []
        batch_size = 5000

        for row in reader:
            buildings, visits = self.get_building_and_visit(row)
            batch.append(
                WorkArea(
                    opportunity_id=self.opp_id,
                    slug=self.get_slug(row),
                    ward=self.get_ward(row),
                    centroid=self.get_centroid(row),
                    boundary=self.get_boundary(row),
                    building_count=buildings,
                    expected_visit_count=visits,
                )
            )

            if len(batch) >= batch_size:
                WorkArea.objects.bulk_create(batch)
                self.created_count += batch_size
                batch = []

        if batch:
            WorkArea.objects.bulk_create(batch)
            self.created_count += len(batch)

    def run(self):
        # Make sure csv_source is seekable
        if isinstance(self.csv_source, str):
            f = io.StringIO(self.csv_source)
        else:
            f = self.csv_source

        # --- First pass: validation only ---
        if not self._validate_all_rows(f):
            return self._result()  # abort if any row is invalid

        # --- Second pass: streaming batch insert ---
        self._stream_and_insert(f)

        return self._result()

    def _result(self):
        if self.errors:
            return {"errors": self.errors}
        return {"created": self.created_count}

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
        processors = [
            self._validate_slug,
            self._validate_ward,
            self._validate_centroid,
            self._validate_boundary,
            self._validate_numbers,
        ]
        for processor in processors:
            processor(
                row,
                line_num,
            )

    def get_boundary(self, row):
        boundary_wkt = row.get(self.HEADERS.get("boundary"), "").strip()
        return GEOSGeometry(boundary_wkt, srid=4326)

    def get_centroid(self, row):
        lon, lat = row.get(self.HEADERS.get("centroid")).strip().split()
        wkt = f"POINT({lon} {lat})"
        return GEOSGeometry(wkt, srid=4326)

    def get_ward(self, row):
        ward = (row.get(self.HEADERS.get("ward")) or "").strip()
        return strip_tags(ward)

    def get_slug(self, row):
        slug = (row.get(self.HEADERS.get("slug")) or "").strip()
        return strip_tags(slug)

    def get_building_and_visit(self, row):
        building_raw = row.get(self.HEADERS.get("building_count"))
        visit_raw = row.get(self.HEADERS.get("visit_count"))
        building = int(building_raw) if building_raw else 0
        visit = int(visit_raw) if visit_raw else 0
        return building, visit

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
            except (GEOSException, ValueError, TypeError):
                pass
        if invalid:
            self._add_error(line_num, _("Invalid WKT format for Boundary(Polygon)."))

        return invalid

    def _validate_numbers(self, row, line_num):
        invalid = True
        try:
            building, visit = self.get_building_and_visit(row)
            if building >= 0 and visit >= 0:
                invalid = False
        except ValueError:
            pass

        if invalid:
            self._add_error(line_num, _("Building count and Expected visit count must be postive integers"))
        return invalid

    def _add_error(self, line, message):
        if message not in self.errors:
            self.errors[message] = []
        self.errors[message].append(line)


@celery_app.task(bind=True)
def import_work_areas_task(self, opp_id, file_name):
    logger.info(f"Starting Work Area import for opportunity: {opp_id}")
    try:
        if WorkArea.objects.filter(opportunity_id=opp_id).exists():
            return {"errors": {_("Work Areas already exist for this opportunity"): [0]}}

        with default_storage.open(file_name, "r") as f:
            importer = WorkAreaCSVImporter(opp_id, f)
            result = importer.run()
        return result
    finally:
        cache.delete(get_import_area_cache_key(opp_id))
        default_storage.delete(file_name)
