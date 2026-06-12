"""
Management command: seed_work_areas_for_opportunity

Generates a realistic grid of contiguous WorkArea polygons for testing
the clustering algorithm. WorkAreas are arranged in a grid pattern so
every cell shares edges with its neighbors, satisfying the Rook contiguity
requirement.

Usage:
    python manage.py seed_work_areas_for_opportunity --opportunity-id 1
    python manage.py seed_work_areas_for_opportunity --opportunity-id 1 --rows 20 --cols 20
    python manage.py seed_work_areas_for_opportunity --opportunity-id 1 --origin-lat -26.2041 --origin-lon 28.0473
    python manage.py seed_work_areas_for_opportunity --opportunity-id 1 --cell-size 0.01 --clear
"""

import math
import random
from uuid import uuid4

from django.contrib.gis.geos import Point, Polygon
from django.core.management.base import BaseCommand, CommandError

from commcare_connect.microplanning.models import Opportunity, WorkArea

# Default origin: Johannesburg CBD (adjust to your target region)
DEFAULT_ORIGIN_LAT = -26.2041
DEFAULT_ORIGIN_LON = 28.0473

DEFAULT_CELL_METRES = 50  # each cell is 50m x 50m on the ground

METRES_PER_DEGREE_LAT = 111_320  # constant everywhere

SRID = 4326  # Match your model's SRID


def metres_to_degrees(metres, origin_lat):
    """
    Convert a ground distance in metres to (deg_lat, deg_lon) at a given latitude.
    Latitude degrees are constant; longitude degrees shrink toward the poles.
    """
    cell_h = metres / METRES_PER_DEGREE_LAT
    cell_w = metres / (METRES_PER_DEGREE_LAT * math.cos(math.radians(origin_lat)))
    return cell_w, cell_h


def make_polygon(origin_lon, origin_lat, cell_w, cell_h):
    """Build a rectangular Polygon for a grid cell. Origin is the SW corner."""
    lon0, lat0 = origin_lon, origin_lat
    lon1, lat1 = origin_lon + cell_w, origin_lat + cell_h
    return Polygon(
        (
            (lon0, lat0),  # SW
            (lon1, lat0),  # SE
            (lon1, lat1),  # NE
            (lon0, lat1),  # NW
            (lon0, lat0),  # close
        ),
        srid=SRID,
    )


def make_centroid(origin_lon, origin_lat, cell_w, cell_h):
    """Return the centroid Point for a grid cell."""
    return Point(
        origin_lon + cell_w / 2,
        origin_lat + cell_h / 2,
        srid=SRID,
    )


def ward_name(row, col, ward_size=5):
    """
    Assign a ward slug based on grid position.
    Every (ward_size x ward_size) block of cells belongs to the same ward.
    """
    ward_row = row // ward_size
    ward_col = col // ward_size
    return f"ward-{ward_row}-{ward_col}"


def building_count_for_cell(row, col, rows, cols, seed=42):
    """
    Generate a realistic building_count per WorkArea using:
      - A spatial gradient (denser toward the centre, like a real city)
      - Random noise
    Result is clamped to 1-150 so groups of 200-300 require 2-5 WorkAreas.
    """
    rng = random.Random(seed + row * 1000 + col)

    centre_row, centre_col = rows / 2, cols / 2
    dist = math.sqrt((row - centre_row) ** 2 + (col - centre_col) ** 2)
    max_dist = math.sqrt(centre_row**2 + centre_col**2)
    normalised_dist = dist / max_dist if max_dist else 0

    base = int(120 * (1 - normalised_dist) + 10)
    noise = rng.randint(-20, 20)
    return max(1, min(150, base + noise))


class Command(BaseCommand):
    help = (
        "Populate the database with synthetic WorkArea polygons arranged in a "
        "contiguous grid for testing the clustering algorithm."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--opportunity-id",
            type=int,
            required=True,
            help="ID of the existing Opportunity to assign to all generated WorkAreas.",
        )
        parser.add_argument(
            "--rows",
            type=int,
            default=15,
            help="Number of grid rows (default: 15)",
        )
        parser.add_argument(
            "--cols",
            type=int,
            default=15,
            help="Number of grid columns (default: 15)",
        )
        parser.add_argument(
            "--origin-lat",
            type=float,
            default=DEFAULT_ORIGIN_LAT,
            help=f"Latitude of the SW corner of the grid (default: {DEFAULT_ORIGIN_LAT})",
        )
        parser.add_argument(
            "--origin-lon",
            type=float,
            default=DEFAULT_ORIGIN_LON,
            help=f"Longitude of the SW corner of the grid (default: {DEFAULT_ORIGIN_LON})",
        )
        parser.add_argument(
            "--cell-metres",
            type=float,
            default=DEFAULT_CELL_METRES,
            help=f"Side length of each square cell in metres (default: {DEFAULT_CELL_METRES}m)",
        )
        parser.add_argument(
            "--ward-size",
            type=int,
            default=5,
            help="Cells per ward side — a (ward_size x ward_size) block = 1 ward (default: 5)",
        )
        parser.add_argument(
            "--seed",
            type=int,
            default=42,
            help="Random seed for reproducible building counts (default: 42)",
        )
        parser.add_argument(
            "--clear",
            action="store_true",
            default=False,
            help="Delete all existing WorkArea records for this Opportunity before populating",
        )

    def handle(self, *args, **options):
        opportunity_id = options["opportunity_id"]
        rows = options["rows"]
        cols = options["cols"]
        origin_lat = options["origin_lat"]
        origin_lon = options["origin_lon"]
        cell_metres = options["cell_metres"]
        ward_size = options["ward_size"]
        seed = options["seed"]
        clear = options["clear"]

        if rows <= 0 or cols <= 0:
            raise CommandError("--rows and --cols must be positive integers.")

        if cell_metres <= 0:
            raise CommandError("--cell-metres must be a positive number.")

        # Validate Opportunity exists upfront
        try:
            opportunity = Opportunity.objects.get(pk=opportunity_id)
        except Opportunity.DoesNotExist:
            raise CommandError(
                f"Opportunity with ID {opportunity_id} does not exist. "
                "Please create one first or pass a valid --opportunity-id."
            )

        self.stdout.write(f"Using Opportunity: [{opportunity.pk}] {opportunity}")

        # Derive degree dimensions from metres at the origin latitude
        cell_w, cell_h = metres_to_degrees(cell_metres, origin_lat)

        self.stdout.write(
            f"Cell size: {cell_metres}m x {cell_metres}m "
            f"({cell_w:.8f}deg lon x {cell_h:.8f}deg lat at lat={origin_lat})"
        )

        if clear:
            deleted, _ = WorkArea.objects.filter(opportunity=opportunity).delete()
            self.stdout.write(self.style.WARNING(f"Deleted {deleted} existing WorkArea(s) for this Opportunity."))

        total = rows * cols
        self.stdout.write(f"Creating {total} WorkAreas ({rows} rows x {cols} cols)...")

        work_areas = []

        for row in range(rows):
            for col in range(cols):
                cell_origin_lon = origin_lon + col * cell_w
                cell_origin_lat = origin_lat + row * cell_h

                boundary = make_polygon(cell_origin_lon, cell_origin_lat, cell_w, cell_h)
                centroid = make_centroid(cell_origin_lon, cell_origin_lat, cell_w, cell_h)
                ward = ward_name(row, col, ward_size)
                buildings = building_count_for_cell(row, col, rows, cols, seed)

                work_areas.append(
                    WorkArea(
                        centroid=centroid,
                        boundary=boundary,
                        ward=ward,
                        building_count=buildings,
                        slug=uuid4(),
                        opportunity=opportunity,
                    )
                )

        WorkArea.objects.bulk_create(work_areas)

        total_buildings = sum(wa.building_count for wa in work_areas)
        expected_groups = round(total_buildings / 250)  # midpoint of 200-300 cap
        grid_width_m = cols * cell_metres
        grid_height_m = rows * cell_metres

        self.stdout.write(self.style.SUCCESS(f"Created {total} WorkAreas"))
        self.stdout.write(f"  Total buildings : {total_buildings}")
        self.stdout.write(f"  Expected groups : ~{expected_groups} (at 250 buildings/group)")
        self.stdout.write(f"  Grid origin     : ({origin_lat}, {origin_lon})")
        self.stdout.write(f"  Grid dimensions : {grid_width_m:.0f}m x {grid_height_m:.0f}m")
        self.stdout.write(f"  Wards created   : {math.ceil(rows / ward_size) * math.ceil(cols / ward_size)}")
