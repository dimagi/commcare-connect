import csv
import io
from collections import defaultdict

from django.contrib.gis.geos import GEOSException, GEOSGeometry
from django.core.cache import cache
from django.db import transaction
from django.utils.translation import gettext as _

from config import celery_app

from .models import WorkArea, WorkAreaGroup, WorkAreaStatus


def get_import_area_cache_key(opp_id: int):
    return f"work_area_import_lock_{opp_id}"


class WorkAreaCSVImporter:
    REQUIRED_HEADERS = {
        "Work Area Group Name",
        "Area Slug",
        "Ward",
        "Centroid",
        "Boundary",
        "Building Count",
        "Expected Visit Count",
        "Status",
    }

    def __init__(self, opp_id, csv_content):
        self.opp_id = opp_id
        self.csv_content = csv_content
        self.errors = {}
        self.work_areas_to_create = []
        self.seen_slugs = set()
        self.group_map = {}

    def run(self):
        reader = csv.DictReader(io.StringIO(self.csv_content))

        if not self._validate_headers(reader):
            return self._result()

        self.existing_slugs = set(WorkArea.objects.filter(opportunity_id=self.opp_id).values_list("slug", flat=True))
        self.group_map = dict(WorkAreaGroup.objects.filter(opportunity_id=self.opp_id).values_list("name", "id"))

        for line_num, row in enumerate(reader, start=2):
            self._process_row(line_num, row)

        self._bulk_insert()

        return self._result()

    def _result(self):
        if self.errors:
            return {"errors": self.group_errors_by_message(self.errors)}
        return {"created": len(self.work_areas_to_create)}

    def _validate_headers(self, reader):
        headers = set(reader.fieldnames or [])
        missing = self.REQUIRED_HEADERS - headers

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
            self._process_geometry,
            self._process_numbers,
            self._process_status,
            self._process_group,
        ]

        for processor in processors:
            invalid_row |= processor(row, line_num, new_area)

        if not invalid_row:
            self.work_areas_to_create.append(new_area)

    def group_errors_by_message(self, errors):
        """
        Convert {line_num: [msg1, msg2]} into {msg1: [line_nums], msg2: [line_nums]}
        """
        grouped = defaultdict(list)
        for line, msgs in errors.items():
            for msg in msgs:
                grouped[msg].append(line)
        return dict(grouped)

    def _process_slug(self, row, line_num, area):
        invalid = True
        slug = (row.get("Area Slug") or "").strip()

        if not slug:
            self._add_error(line_num, _("Area slug is required."))
        elif slug in self.seen_slugs:
            self._add_error(line_num, _("Duplicate Area slug in file"))
        elif slug in self.existing_slugs:
            self._add_error(line_num, _("Area slug already exists for this opportunity"))
        else:
            self.seen_slugs.add(slug)
            area.slug = slug
            invalid = False

        return invalid

    def _process_geometry(self, row, line_num, area):
        invalid = True
        centroid_raw = row.get("Centroid")
        boundary_raw = row.get("Boundary")

        if not centroid_raw or not boundary_raw:
            self._add_error(line_num, _("Centroid and Boundary are required."))
        else:
            try:
                centroid = GEOSGeometry(centroid_raw, srid=4326)
                boundary = GEOSGeometry(boundary_raw, srid=4326)

                if centroid.geom_type != "Point":
                    self._add_error(line_num, _("Centroid must be a POINT"))
                elif boundary.geom_type != "Polygon":
                    self._add_error(line_num, _("Boundary must be POLYGON"))
                elif not boundary.valid:
                    self._add_error(line_num, _("Invalid Boundary polygon geometry"))
                else:
                    # all checks passed
                    area.centroid = centroid
                    area.boundary = boundary
                    invalid = False

            except (ValueError, GEOSException, TypeError):
                self._add_error(line_num, _("Invalid WKT format for Centroid or Boundary"))

        return invalid

    def _process_numbers(self, row, line_num, area):
        invalid = True
        building_raw = row.get("Building Count")
        visit_raw = row.get("Expected Visit Count")

        try:
            building = int(building_raw) if building_raw else 0
            visit = int(visit_raw) if visit_raw else 0

            if building < 0 or visit < 0:
                self._add_error(line_num, _("Building count and Expected visit cannot be negative"))
            else:
                area.building_count = building
                area.expected_visit_count = visit
                invalid = False

        except ValueError:
            self._add_error(line_num, _("Building count and Expected visit count Must be integers"))

        return invalid

    def _process_status(self, row, line_num, area):
        invalid = True
        raw_status = (row.get("Status") or "").strip()

        if not raw_status:
            area.status = WorkAreaStatus.NOT_STARTED
            invalid = False
        elif raw_status not in WorkAreaStatus.values:
            self._add_error(line_num, _("Invalid status value"))
        else:
            area.status = raw_status
            invalid = False

        return invalid

    def _process_group(self, row, line_num, area):
        invalid = True
        name = (row.get("Work Area Group Name") or "").strip()

        if not name:
            invalid = False  # empty group allowed
        elif name not in self.group_map:
            self._add_error(line_num, _("Group Area name not found"))
        else:
            area.work_area_group_id = self.group_map[name]
            invalid = False

        return invalid

    def _bulk_insert(self):
        if not self.work_areas_to_create or self.errors:
            return

        with transaction.atomic():
            WorkArea.objects.bulk_create(
                self.work_areas_to_create,
                batch_size=500,
            )

    def _add_error(self, line, message):
        if line not in self.errors:
            self.errors[line] = []
        self.errors[line].append(message)


@celery_app.task()
def import_work_areas_task(opp_id, csv_content):
    lock_key = get_import_area_cache_key(opp_id)
    try:
        importer = WorkAreaCSVImporter(opp_id, csv_content)
        return importer.run()
    finally:
        cache.delete(lock_key)
