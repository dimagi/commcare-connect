import random
from datetime import timedelta
from uuid import uuid4

from django.contrib.gis.geos import Point, Polygon
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils.timezone import now

from commcare_connect.microplanning.models import SRID, WorkArea, WorkAreaStatus
from commcare_connect.opportunity.models import (
    DeliverUnit,
    Opportunity,
    OpportunityAccess,
    PaymentUnit,
    UserVisit,
    VisitValidationStatus,
)
from commcare_connect.users.models import User

# Default region to drop generated work areas into when the opportunity has
# none of its own (lon/lat around Delhi, matching WorkAreaFactory).
DEFAULT_REGION = (77.0, 28.0, 78.0, 29.0)


class Command(BaseCommand):
    help = (
        "Create sample UserVisits (with locations) for an opportunity so they can be shown "
        "via the 'Show Visits' toggle on the microplanning map. Reuses the opportunity's existing "
        "work areas, or creates a few if it has none, and scatters the visits inside their boundaries."
    )

    def add_arguments(self, parser):
        parser.add_argument("opportunity_id", type=str, help="Opportunity UUID (opportunity_id field)")
        parser.add_argument("count", type=int, help="Number of user visits to create")
        parser.add_argument(
            "--work-areas",
            type=int,
            default=4,
            help="Number of work areas to create if the opportunity has none (default: 4)",
        )
        parser.add_argument(
            "--create-users",
            action="store_true",
            default=False,
            help="Create new sample mobile workers for the visits. By default, reuse users that "
            "already have access to the opportunity.",
        )

    @transaction.atomic
    def handle(self, *args, **options):
        count = options["count"]
        opportunity = self._get_opportunity(options["opportunity_id"])

        work_areas = self._get_or_create_work_areas(opportunity, options["work_areas"])
        deliver_unit = self._get_or_create_deliver_unit(opportunity)
        accesses = self._get_accesses(opportunity, work_areas, options["create_users"])

        self.stdout.write(f"Creating {count} visits for '{opportunity.name}' across {len(work_areas)} work area(s)...")
        for i in range(count):
            work_area = work_areas[i % len(work_areas)]
            access = work_area.opportunity_access or random.choice(accesses)
            self._create_visit(opportunity, work_area, access, deliver_unit)

        self.stdout.write(self.style.SUCCESS(f"Created {count} visits. Toggle 'Show Visits' on the map to see them."))

    def _get_opportunity(self, opportunity_id):
        try:
            return Opportunity.objects.get(opportunity_id=opportunity_id)
        except (Opportunity.DoesNotExist, ValueError, ValidationError):
            raise CommandError(f"No opportunity found with opportunity_id={opportunity_id!r}")

    def _get_or_create_work_areas(self, opportunity, num_to_create):
        work_areas = list(WorkArea.objects.filter(opportunity=opportunity))
        if work_areas:
            self.stdout.write(f"Reusing {len(work_areas)} existing work area(s).")
            return work_areas

        self.stdout.write(f"No work areas found - creating {num_to_create} in the default region.")
        return [self._create_work_area(opportunity, i, num_to_create) for i in range(num_to_create)]

    def _create_work_area(self, opportunity, index, total):
        """Create a small, non-overlapping square work area laid out in a grid over DEFAULT_REGION."""
        cols = self._grid_columns(total)
        rows = (total + cols - 1) // cols
        col, row = index % cols, index // cols

        x_min, y_min, x_max, y_max = DEFAULT_REGION
        cell_w = (x_max - x_min) / cols
        cell_h = (y_max - y_min) / rows
        # Inset the box within its cell so adjacent areas don't touch.
        bx_min = x_min + col * cell_w + cell_w * 0.1
        by_min = y_min + row * cell_h + cell_h * 0.1
        bx_max = bx_min + cell_w * 0.8
        by_max = by_min + cell_h * 0.8

        boundary = Polygon(
            ((bx_min, by_min), (bx_max, by_min), (bx_max, by_max), (bx_min, by_max), (bx_min, by_min)),
            srid=SRID,
        )
        return WorkArea.objects.create(
            opportunity=opportunity,
            slug=f"demo-area-{index + 1}",
            ward=f"demo-ward-{index + 1}",
            boundary=boundary,
            centroid=boundary.centroid,
            status=WorkAreaStatus.NOT_VISITED,
            expected_visit_count=10,
            building_count=10,
        )

    @staticmethod
    def _grid_columns(total):
        cols = 1
        while cols * cols < total:
            cols += 1
        return cols

    def _get_or_create_deliver_unit(self, opportunity):
        if opportunity.deliver_app is None:
            raise CommandError("Opportunity has no deliver_app; cannot create a DeliverUnit for visits.")
        deliver_unit = DeliverUnit.objects.filter(app=opportunity.deliver_app).first()
        if deliver_unit:
            return deliver_unit

        payment_unit = PaymentUnit.objects.create(
            opportunity=opportunity,
            name="Demo payment unit",
            description="Auto-created for sample microplanning visits.",
            amount=1,
            max_daily=10,
            max_total=20,
        )
        return DeliverUnit.objects.create(
            app=opportunity.deliver_app,
            slug=f"demo-deliver-unit-{uuid4().hex[:8]}",
            name="Demo deliver unit",
            payment_unit=payment_unit,
        )

    def _get_accesses(self, opportunity, work_areas, create_users):
        if create_users:
            self.stdout.write("Creating sample mobile workers.")
            accesses = [self._create_access(opportunity, i) for i in range(min(3, len(work_areas)) or 1)]
        else:
            accesses = list(OpportunityAccess.objects.filter(opportunity=opportunity))
            if not accesses:
                raise CommandError(
                    "No existing users have access to this opportunity. "
                    "Pass --create-users to create sample mobile workers."
                )
            self.stdout.write(f"Reusing {len(accesses)} existing opportunity access(es).")
        # Make sure every work area has an assignee for nicer map display.
        for i, work_area in enumerate(work_areas):
            if work_area.opportunity_access is None:
                work_area.opportunity_access = accesses[i % len(accesses)]
                work_area.save(update_fields=["opportunity_access"])
        return accesses

    def _create_access(self, opportunity, index):
        user = User.objects.create(username=f"demo-mobile-{uuid4().hex[:12]}", name=f"Demo Worker {index + 1}")
        return OpportunityAccess.objects.create(opportunity=opportunity, user=user)

    def _create_visit(self, opportunity, work_area, access, deliver_unit):
        point = self._random_point_in(work_area.boundary)
        location = f"{point.y:.7f} {point.x:.7f} 0.0 5.0"  # "<lat> <lon> <altitude> <accuracy>"
        visit_date = now() - timedelta(days=random.randint(0, 14), minutes=random.randint(0, 1440))

        UserVisit.objects.create(
            opportunity=opportunity,
            user=access.user,
            opportunity_access=access,
            deliver_unit=deliver_unit,
            work_area=work_area,
            completed_work=None,
            status=VisitValidationStatus.approved,
            visit_date=visit_date,
            location=location,
            form_json={"metadata": {"location": location}},
            xform_id=uuid4().hex,
        )

    @staticmethod
    def _random_point_in(polygon):
        """Sample a uniform random point that falls inside the polygon."""
        x_min, y_min, x_max, y_max = polygon.extent
        for _ in range(20):
            candidate = Point(random.uniform(x_min, x_max), random.uniform(y_min, y_max), srid=polygon.srid)
            if polygon.contains(candidate):
                return candidate
        return polygon.centroid
