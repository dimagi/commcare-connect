"""Seed work areas for local microplanning map work.

Complements ``generate_sample_data``, which builds opportunities and visits but no work areas.
Either seeds a self-contained demo opportunity, or adds work areas to one that already exists
(``--opp-id``).

The clusters are placed far apart on purpose: the map's filters and search auto-zoom to what is
selected, and that behaviour is indistinguishable from "zoom to everything" when every work area
sits in the same small box.
"""

import random
import uuid
from datetime import date, timedelta
from typing import NamedTuple

from allauth.account.models import EmailAddress
from django.conf import settings
from django.contrib.gis.geos import Point, Polygon
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.http import Http404
from django.utils import timezone
from oauth2_provider.models import get_application_model

from commcare_connect.commcarehq.models import HQServer
from commcare_connect.flags.flag_names import MICROPLANNING
from commcare_connect.flags.models import Flag
from commcare_connect.microplanning.const import (
    NO_CHILDREN_WORK_AREA_UNIT_SLUG,
    SERVICE_DELIVERY_UNIT_SLUG,
)
from commcare_connect.microplanning.models import SRID, ImplementationArea, WorkArea, WorkAreaGroup, WorkAreaStatus
from commcare_connect.opportunity.models import (
    CommCareApp,
    CompletedWork,
    Country,
    Currency,
    DeliverUnit,
    DeliveryType,
    Opportunity,
    OpportunityAccess,
    PaymentUnit,
    UserVisit,
    VisitValidationStatus,
)
from commcare_connect.organization.models import Organization, UserOrganizationMembership
from commcare_connect.program.models import Program
from commcare_connect.users.models import User
from commcare_connect.utils.db import get_object_by_uuid_or_int

# Organization.save() derives the slug from the name, so a slug cannot be chosen here.
DEMO_ORG_NAME = "Microplanning Demo Org"
DEMO_OPP_NAME = "Microplanning Demo"
DEMO_ADMIN_USERNAME = "mpadmin"
DEMO_ADMIN_PASSWORD = "mpadmin"

CLUSTER_NAMES = ["North", "Central", "South", "East", "West", "Northeast", "Northwest", "Southeast"]
CLUSTER_ORIGIN = (4.0, 5.0)  # lon, lat — southwest corner of the first cluster
CLUSTER_SPACING = 3.5  # degrees between cluster origins; wide enough that zooming is visible
CLUSTERS_PER_ROW = 3
CELL_SIZE = 0.04  # degrees per work area side

ASSIGNED_STATUSES = [WorkAreaStatus.NOT_VISITED, WorkAreaStatus.VISITED, WorkAreaStatus.EXPECTED_VISIT_REACHED]
UNASSIGNED_STATUSES = [
    WorkAreaStatus.UNASSIGNED,
    WorkAreaStatus.UNASSIGNED,
    WorkAreaStatus.UNASSIGNED,
    WorkAreaStatus.EXCLUDED,
    WorkAreaStatus.INACCESSIBLE,
]
VISITED_STATUSES = (WorkAreaStatus.VISITED, WorkAreaStatus.EXPECTED_VISIT_REACHED)
# Every coverage metric counts approved visits by deliver unit slug, so the seeded deliver units
# have to use the slugs from microplanning.const or the map reports zeros and warns that the
# required deliver units are missing.
PAYMENT_UNITS = (
    ("Service Delivery", SERVICE_DELIVERY_UNIT_SLUG),
    ("No Children in Work Area", NO_CHILDREN_WORK_AREA_UNIT_SLUG),
)


class Cluster(NamedTuple):
    """One geographic cluster of work areas, and the rows they hang off."""

    name: str
    origin: tuple  # (lon, lat) of the southwest corner
    implementation_area: ImplementationArea
    groups: list
    assignee: OpportunityAccess | None


class Command(BaseCommand):
    help = "Generates work areas, groups and implementation areas for the microplanning maps"

    def add_arguments(self, parser):
        parser.add_argument(
            "--opp-id",
            type=str,
            default=None,
            help="Seed work areas onto this existing opportunity (pk or uuid) instead of creating a demo one",
        )
        parser.add_argument(
            "--org-slug",
            type=str,
            default=None,
            help="Create the demo opportunity in this existing workspace instead of a new one",
        )
        parser.add_argument("--clusters", type=int, default=3, help="Number of geographic clusters (default 3)")
        parser.add_argument(
            "--grid", type=int, default=4, help="Work areas per cluster side, so grid^2 per cluster (default 4)"
        )
        parser.add_argument("--groups-per-cluster", type=int, default=2, help="Work area groups per cluster")
        parser.add_argument("--workers", type=int, default=3, help="Mobile workers to create and assign areas to")
        parser.add_argument(
            "--no-admin",
            action="store_true",
            help=f"Skip creating the {DEMO_ADMIN_USERNAME} superuser",
        )
        parser.add_argument(
            "--keep-existing",
            action="store_true",
            help=(
                "Add to the opportunity's existing work areas instead of replacing them. Intended for "
                "an opportunity whose areas came from elsewhere; re-running the same clusters this way "
                "is refused, since their generated names already exist."
            ),
        )
        parser.add_argument("--seed", type=int, default=42, help="Random seed, for reproducible geometry")
        parser.add_argument(
            "--force",
            action="store_true",
            help="Required to run with DEBUG off — this command creates a superuser with a known password",
        )

    def handle(self, *args, **options):
        if not settings.DEBUG and not options["force"]:
            raise CommandError(
                "Refusing to run with DEBUG off: this command creates test data and a superuser with a "
                "well-known password. Pass --force if that is really what you want."
            )
        if options["clusters"] < 1 or options["grid"] < 1 or options["groups_per_cluster"] < 1:
            raise CommandError("--clusters, --grid and --groups-per-cluster must all be at least 1")

        random.seed(options["seed"])

        with transaction.atomic():
            opportunity, owns_workspace = self.resolve_opportunity(options)
            if opportunity.deliver_app_id is None:
                raise CommandError(
                    f"Opportunity {opportunity.name!r} has no deliver app, so no deliver units can be seeded."
                )
            assignment_mode = self.ensure_program_manager(opportunity.organization, owns_workspace)
            if not options["no_admin"]:
                self.ensure_admin(opportunity.organization)
            self.ensure_flag(opportunity)
            if options["keep_existing"]:
                self.check_names_available(opportunity, options)
            else:
                self.clear_areas(opportunity)

            payment_units = self.ensure_payment_units(opportunity)
            accesses = self.ensure_workers(opportunity, options["workers"])
            work_areas = self.create_areas(opportunity, accesses, options)
            visit_count = self.create_visits(opportunity, work_areas, payment_units)

        self.report(opportunity, work_areas, visit_count, assignment_mode)

    def resolve_opportunity(self, options):
        """The caller's existing opportunity, or the demo one (created on first run).

        Returns the opportunity and whether its workspace is one this command owns — a caller's
        own workspace is only ever read, never reconfigured.
        """
        if options["opp_id"]:
            try:
                opportunity = get_object_by_uuid_or_int(
                    Opportunity.objects.all(), options["opp_id"], uuid_field="opportunity_id"
                )
            except Http404:
                raise CommandError(f"No opportunity matches --opp-id {options['opp_id']!r}") from None
            self.stdout.write(f"Using existing opportunity: {opportunity.name}")
            return opportunity, False
        return self.ensure_demo_opportunity(options["org_slug"]), not options["org_slug"]

    def ensure_demo_opportunity(self, org_slug):
        org = self.ensure_demo_org(org_slug)
        hq_server = self.ensure_hq_server()
        opportunity = org.opportunities.filter(name=DEMO_OPP_NAME).first()
        if opportunity:
            self.backfill_hq_server(opportunity, hq_server)
            self.stdout.write(f"Using existing demo opportunity in {org.slug}")
            return opportunity

        currency = Currency.objects.filter(code="USD").first()
        country = Country.objects.filter(code="USA").first()
        if not (currency and country):
            raise CommandError("Currency/Country reference data is missing — run migrate first.")

        delivery_type, _ = DeliveryType.objects.get_or_create(
            slug="microplanning-demo",
            defaults={"name": "Microplanning Demo", "description": "Seeded delivery type"},
        )
        today = date.today()
        program = Program.objects.filter(
            organization=org, name=f"{DEMO_OPP_NAME} Program"
        ).first() or Program.objects.create(
            organization=org,
            name=f"{DEMO_OPP_NAME} Program",
            description="Seeded program",
            delivery_type=delivery_type,
            budget=100000,
            currency=currency,
            country=country,
            start_date=today - timedelta(days=180),
            end_date=today + timedelta(days=180),
        )
        opportunity = Opportunity.objects.create(
            organization=org,
            program=program,
            name=DEMO_OPP_NAME,
            description="Seeded opportunity for microplanning map work",
            short_description="Microplanning demo",
            active=True,
            is_test=True,
            learn_app=self.ensure_app(org, "Learn", hq_server),
            deliver_app=self.ensure_app(org, "Deliver", hq_server),
            hq_server=hq_server,
            delivery_type=delivery_type,
            currency=currency,
            country=country,
            total_budget=100000,
            start_date=today - timedelta(days=120),
            end_date=today + timedelta(days=120),
        )
        self.stdout.write(f"Created demo opportunity in {org.slug}")
        return opportunity

    def ensure_demo_org(self, org_slug):
        """Reuse whichever workspace already holds the demo opportunity, else make one.

        Anchoring on the opportunity rather than the workspace is deliberate:
        Organization.save() replaces any slug passed in with slugify_uniquely(name), so the slug
        is not a usable lookup key and matching on name alone breaks once a run has left a
        duplicate behind.
        """
        if org_slug:
            org = Organization.objects.filter(slug=org_slug).first()
            if not org:
                raise CommandError(f"No workspace with slug {org_slug!r}")
        else:
            existing = Opportunity.objects.filter(name=DEMO_OPP_NAME).select_related("organization").first()
            org = existing.organization if existing else Organization.objects.create(name=DEMO_ORG_NAME)
        return org

    def ensure_program_manager(self, org, owns_workspace):
        """Assignment Mode needs an admin membership in a *program manager* workspace.

        Only set on the workspace this command created. program_manager changes what the whole
        app shows for a workspace, well beyond microplanning, so flipping it on one the caller
        pointed us at would be a side effect nobody asked for — that case is reported instead.

        Returns whether Assignment Mode will actually work.
        """
        if org.program_manager:
            return True
        if not owns_workspace:
            self.stdout.write(
                self.style.WARNING(
                    f"{org.slug} is not a program manager workspace, so Assignment Mode will render the "
                    "ordinary progress map. Set program_manager on it yourself to use Assignment Mode."
                )
            )
            return False
        org.program_manager = True
        org.save(update_fields=["program_manager"])
        self.stdout.write(f"Marked {org.slug} as a program manager workspace")
        return True

    def ensure_hq_server(self):
        """Reuse any HQServer; build one, with the OAuth application it requires, if there is none."""
        hq_server = HQServer.objects.order_by("id").first()
        if hq_server:
            return hq_server
        application_model = get_application_model()
        application = application_model.objects.create(
            name="Microplanning Demo OAuth App",
            client_type=application_model.CLIENT_CONFIDENTIAL,
            authorization_grant_type=application_model.GRANT_CLIENT_CREDENTIALS,
        )
        return HQServer.objects.create(
            name="Microplanning Demo HQ",
            url="https://hq.microplanning.example",
            oauth_application=application,
        )

    def ensure_app(self, org, kind, hq_server):
        """A CommCareApp shell.

        hq_server is required in practice even though the column is nullable: CommCareApp.url
        dereferences it unconditionally and the opportunity detail page renders that url.
        """
        app, _ = CommCareApp.objects.get_or_create(
            organization=org,
            name=f"{DEMO_OPP_NAME} {kind} App",
            defaults={
                "cc_domain": "microplanning-demo",
                "cc_app_id": f"microplanning-demo-{kind.lower()}",
                "description": f"Seeded {kind.lower()} app",
                "hq_server": hq_server,
            },
        )
        return app

    def backfill_hq_server(self, opportunity, hq_server):
        """Fill in hq_server on rows an earlier run created without one."""
        for app in (opportunity.learn_app, opportunity.deliver_app):
            if app and app.hq_server_id is None:
                app.hq_server = hq_server
                app.save(update_fields=["hq_server"])
        if opportunity.hq_server_id is None:
            opportunity.hq_server = hq_server
            opportunity.save(update_fields=["hq_server"])

    def ensure_admin(self, org):
        admin, created = User.objects.get_or_create(
            username=DEMO_ADMIN_USERNAME,
            defaults={"email": f"{DEMO_ADMIN_USERNAME}@example.com", "name": "Microplanning Admin"},
        )
        admin.is_staff = True
        admin.is_superuser = True
        admin.set_password(DEMO_ADMIN_PASSWORD)
        admin.save()
        # ACCOUNT_EMAIL_VERIFICATION is "mandatory", so an unverified address cannot use the
        # normal login form.
        EmailAddress.objects.update_or_create(
            user=admin, email=admin.email, defaults={"verified": True, "primary": True}
        )
        UserOrganizationMembership.objects.update_or_create(
            organization=org, user=admin, defaults={"role": UserOrganizationMembership.Role.ADMIN}
        )
        self.stdout.write(f"Superuser {admin.username} ({'created' if created else 'updated'})")

    def ensure_flag(self, opportunity):
        flag, _ = Flag.objects.get_or_create(name=MICROPLANNING)
        flag.opportunities.add(opportunity)
        self.stdout.write(f"Flag {MICROPLANNING} enabled for this opportunity")

    def check_names_available(self, opportunity, options):
        """Fail early when --keep-existing would recreate rows the opportunity already has.

        Every generated name is deterministic, so a second run with the same options keeps the
        previous run's rows and then tries to recreate them. Three per-opportunity unique
        constraints sit in the way, and all three are checked: an opportunity whose areas came
        from elsewhere can collide on the work area slug alone, without either name matching.
        """
        clusters = [cluster_name(index) for index in range(options["clusters"])]
        taken = [
            *self.taken_values(
                ImplementationArea,
                opportunity,
                "name",
                [implementation_area_name(cluster) for cluster in clusters],
            ),
            *self.taken_values(
                WorkAreaGroup,
                opportunity,
                "name",
                [group_name(cluster, index) for cluster in clusters for index in range(options["groups_per_cluster"])],
            ),
            *self.taken_values(
                WorkArea,
                opportunity,
                "slug",
                [work_area_slug(cluster, index) for cluster in clusters for index in range(options["grid"] ** 2)],
            ),
        ]
        if taken:
            shown = ", ".join(sorted(taken)[:3])
            more = ", …" if len(taken) > 3 else ""
            raise CommandError(
                f"--keep-existing would recreate rows this opportunity already has ({shown}{more}). "
                "Drop --keep-existing to delete every work area on this opportunity, imported ones "
                "included, and seed from scratch — or seed a different opportunity."
            )

    def taken_values(self, model, opportunity, field, values):
        """Which of `values` this opportunity already uses for `field`."""
        return list(
            model.objects.filter(opportunity=opportunity, **{f"{field}__in": values}).values_list(field, flat=True)
        )

    def clear_areas(self, opportunity):
        """Make the command re-runnable.

        Only rows hanging off a work area are cleared: visits recorded against no work area belong
        to whatever else has been seeded on this opportunity, and deleting those would be a
        surprise on an --opp-id run.

        Order matters. Visits go first, since WorkArea is PROTECTed by UserVisit.work_area, and the
        CompletedWork rows keyed on the outgoing work area ids go with them — otherwise every run
        strands another generation of them against ids that no longer exist.
        """
        work_areas = WorkArea.objects.filter(opportunity=opportunity)
        work_area_ids = [str(pk) for pk in work_areas.values_list("id", flat=True)]
        UserVisit.objects.filter(opportunity=opportunity, work_area__isnull=False).delete()
        CompletedWork.objects.filter(opportunity_access__opportunity=opportunity, entity_id__in=work_area_ids).delete()
        _, deleted_by_model = work_areas.delete()
        WorkAreaGroup.objects.filter(opportunity=opportunity).delete()
        ImplementationArea.objects.filter(opportunity=opportunity).delete()
        deleted = deleted_by_model.get(WorkArea._meta.label, 0)
        if deleted:
            self.stdout.write(f"Cleared {deleted} existing work area rows")

    def ensure_payment_units(self, opportunity):
        """Two payment units, each with one deliver unit — drives the Payment Unit map filter.

        The two pair up: each seeded visit records a deliver unit against a completed work keyed on
        a payment unit, and a completed work whose payment unit does not own the deliver unit its
        visits carry never completes. The slugs are fixed by the coverage metrics, so an --opp-id
        opportunity may already own a deliver unit using one of them — that deliver unit's own
        payment unit is then the one to pair it with, rather than a second one seeded alongside it.
        """
        units = []
        for index, (name, slug) in enumerate(PAYMENT_UNITS):
            deliver_unit = DeliverUnit.objects.filter(app=opportunity.deliver_app, slug=slug).first()
            payment_unit = self.existing_payment_unit(deliver_unit, opportunity) or self.seeded_payment_unit(
                opportunity, name, index
            )
            if deliver_unit is None:
                deliver_unit = DeliverUnit.objects.create(
                    app=opportunity.deliver_app, slug=slug, name=f"{name} DU", payment_unit=payment_unit
                )
            elif deliver_unit.payment_unit_id != payment_unit.id:
                deliver_unit.payment_unit = payment_unit
                deliver_unit.save(update_fields=["payment_unit"])
            units.append((payment_unit, deliver_unit))
        return units

    def existing_payment_unit(self, deliver_unit, opportunity):
        """The payment unit `deliver_unit` already belongs to, if it is one this opportunity owns.

        A deliver unit hanging off another opportunity's payment unit means the deliver app is
        shared, and pairing seeded visits across opportunities that way would be worse than
        repointing it.
        """
        if deliver_unit is None or deliver_unit.payment_unit_id is None:
            return None
        payment_unit = deliver_unit.payment_unit
        return payment_unit if payment_unit.opportunity_id == opportunity.id else None

    def seeded_payment_unit(self, opportunity, name, index):
        payment_unit, _ = PaymentUnit.objects.get_or_create(
            opportunity=opportunity,
            name=name,
            defaults={
                "description": f"Seeded {name}",
                "amount": 5 + index,
                "org_amount": 2,
                "max_daily": 10,
                "max_total": 100,
            },
        )
        return payment_unit

    def ensure_workers(self, opportunity, count):
        accesses = []
        for i in range(count):
            user, _ = User.objects.get_or_create(username=f"mp_worker_{i}", defaults={"name": f"MP Worker {i}"})
            access, _ = OpportunityAccess.objects.get_or_create(
                opportunity=opportunity, user=user, defaults={"accepted": True}
            )
            if not access.accepted or access.suspended:
                access.accepted = True
                access.suspended = False
                access.save()
            accesses.append(access)
        self.stdout.write(f"Workers: {', '.join(a.user.username for a in accesses)}")
        return accesses

    def create_areas(self, opportunity, accesses, options):
        """One implementation area and N groups of grid^2 work areas per cluster."""
        work_areas = []
        for index in range(options["clusters"]):
            cluster = self.build_cluster(opportunity, index, accesses, options)
            work_areas += self.create_cluster_areas(opportunity, cluster, options["grid"])
            self.stdout.write(
                f"Cluster {cluster.name}: {options['grid'] ** 2} work areas in {len(cluster.groups)} groups"
            )
        return work_areas

    def build_cluster(self, opportunity, index, accesses, options):
        name = cluster_name(index)
        origin = cluster_origin(index)
        return Cluster(
            name=name,
            origin=origin,
            implementation_area=self.create_implementation_area(opportunity, name, origin, options["grid"]),
            groups=self.create_groups(opportunity, name, options["groups_per_cluster"]),
            # One cluster per worker, so the Assignee filter and "Show only unassigned" both
            # have something to show. Clusters beyond the worker count stay unassigned.
            assignee=accesses[index] if index < len(accesses) else None,
        )

    def create_implementation_area(self, opportunity, name, origin, grid):
        lon0, lat0 = origin
        span = grid * CELL_SIZE
        return ImplementationArea.objects.create(
            opportunity=opportunity,
            name=implementation_area_name(name),
            centroid=Point(lon0 + span / 2, lat0 + span / 2, srid=SRID),
            boundary=box(lon0 - CELL_SIZE, lat0 - CELL_SIZE, lon0 + span + CELL_SIZE, lat0 + span + CELL_SIZE),
        )

    def create_groups(self, opportunity, cluster, count):
        return [
            WorkAreaGroup.objects.create(
                opportunity=opportunity,
                name=group_name(cluster, i),
                ward=f"{cluster.lower()}-ward-{i + 1}",
            )
            for i in range(count)
        ]

    def create_cluster_areas(self, opportunity, cluster, grid):
        lon0, lat0 = cluster.origin
        pending = []

        for cell_index in range(grid * grid):
            row, col = divmod(cell_index, grid)
            x1, y1 = lon0 + col * CELL_SIZE, lat0 + row * CELL_SIZE
            group = cluster.groups[cell_index * len(cluster.groups) // (grid * grid)]
            # Only the cluster's first group is assigned, so every cluster holds a mix.
            access = cluster.assignee if group is cluster.groups[0] else None
            statuses = ASSIGNED_STATUSES if access else UNASSIGNED_STATUSES

            pending.append(
                WorkArea(
                    opportunity=opportunity,
                    work_area_group=group,
                    opportunity_access=access,
                    implementation_area=cluster.implementation_area,
                    implementation_area_name=cluster.implementation_area.name,
                    slug=work_area_slug(cluster.name, cell_index),
                    ward=group.ward,
                    centroid=Point(x1 + CELL_SIZE / 2, y1 + CELL_SIZE / 2, srid=SRID),
                    boundary=box(x1, y1, x1 + CELL_SIZE, y1 + CELL_SIZE),
                    building_count=random.randint(80, 300),
                    # Small on purpose: create_visits() has to be able to reach this target for
                    # the areas it marks EXPECTED_VISIT_REACHED.
                    expected_visit_count=random.randint(2, 5),
                    target_population=random.randint(200, 900),
                    status=statuses[cell_index % len(statuses)],
                    case_id=str(uuid.uuid4()),
                    case_properties={"lga": f"{cluster.name} LGA"},
                )
            )

        # bulk_create returns the rows with their pks set, and each one already holds the
        # OpportunityAccess object create_visits() needs, so no re-read is required.
        created = WorkArea.objects.bulk_create(pending)
        for group in cluster.groups:
            group.update_centroid()
        return created

    def create_visits(self, opportunity, work_areas, payment_units):
        """Visits on the visited areas only, spread over 90 days.

        The count and the deliver unit both follow the area's status, because the map derives its
        coverage tiles from approved visits rather than from the status column: an area labelled
        EXPECTED_VISIT_REACHED with fewer service-delivery visits than its target reads as not
        delivered everywhere except on its own chip.
        """
        visited = [wa for wa in work_areas if wa.opportunity_access_id and wa.status in VISITED_STATUSES]
        now = timezone.now()
        visits = []

        for index, work_area in enumerate(visited):
            payment_unit, deliver_unit, visit_count = self.visit_plan(work_area, payment_units, index)
            completed_work, _ = CompletedWork.objects.get_or_create(
                opportunity_access_id=work_area.opportunity_access_id,
                payment_unit=payment_unit,
                entity_id=str(work_area.id),
                defaults={"entity_name": work_area.slug},
            )
            for n in range(visit_count):
                lon, lat = jittered_point(work_area)
                visits.append(
                    UserVisit(
                        opportunity=opportunity,
                        user_id=work_area.opportunity_access.user_id,
                        opportunity_access_id=work_area.opportunity_access_id,
                        deliver_unit=deliver_unit,
                        completed_work=completed_work,
                        work_area=work_area,
                        entity_id=completed_work.entity_id,
                        entity_name=completed_work.entity_name,
                        status=VisitValidationStatus.approved,
                        visit_date=now - timedelta(days=random.randint(0, 90), hours=n),
                        # The visit tile view parses this as "<lat> <lon> <alt> <accuracy>".
                        location=f"{lat} {lon} 0 5",
                        form_json={"seeded": True},
                        xform_id=str(uuid.uuid4()),
                    )
                )

        UserVisit.objects.bulk_create(visits)
        self.stdout.write(f"Visits: {len(visits)} across {len(visited)} work areas")
        return len(visits)

    def visit_plan(self, work_area, payment_units, index):
        """The payment unit, deliver unit and visit count for one visited work area."""
        service_delivery, no_children = payment_units
        if work_area.status == WorkAreaStatus.EXPECTED_VISIT_REACHED:
            # Enough approved service-delivery visits to actually hit the target.
            return (*service_delivery, work_area.expected_visit_count)
        if index % 2:
            # "Visited, no children found" — one visit on the other deliver unit, so that tile
            # is populated too.
            return (*no_children, 1)
        # Visited but short of the target.
        return (*service_delivery, max(1, work_area.expected_visit_count - 1))

    def report(self, opportunity, work_areas, visit_count, assignment_mode):
        base = f"/a/{opportunity.organization.slug}/microplanning/{opportunity.opportunity_id}/"
        self.stdout.write(self.style.SUCCESS(f"\n{len(work_areas)} work areas, {visit_count} visits"))
        self.stdout.write(f"Progress Map:   {base}")
        if assignment_mode:
            self.stdout.write(f"Assignment Mode: {base}?assignment_mode=1")


def cluster_name(index):
    """A single word: the name is lower-cased straight into the work area slug and the ward, both
    SlugFields, and bulk_create does not validate them."""
    return CLUSTER_NAMES[index] if index < len(CLUSTER_NAMES) else f"Region{index + 1}"


def implementation_area_name(cluster):
    return f"{cluster} Implementation Area"


def group_name(cluster, index):
    return f"{cluster} Group {index + 1}"


def work_area_slug(cluster, index):
    return f"{cluster.lower()}-wa-{index + 1:02d}"


def cluster_origin(index):
    """Southwest corner of a cluster, laid out on a widely spaced grid."""
    lon0, lat0 = CLUSTER_ORIGIN
    return (
        lon0 + (index % CLUSTERS_PER_ROW) * CLUSTER_SPACING,
        lat0 + (index // CLUSTERS_PER_ROW) * CLUSTER_SPACING,
    )


def box(x1, y1, x2, y2):
    """An axis-aligned rectangle. from_bbox() drops the srid, which GeoDjango needs set."""
    polygon = Polygon.from_bbox((x1, y1, x2, y2))
    polygon.srid = SRID
    return polygon


def jittered_point(work_area):
    x1, y1, x2, y2 = work_area.boundary.extent
    return round(random.uniform(x1, x2), 6), round(random.uniform(y1, y2), 6)
