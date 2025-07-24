import random
from datetime import datetime, timedelta, timezone

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone as djtimezone
from faker import Faker

from commcare_connect.opportunity.models import (
    Assessment,
    CompletedModule,
    CompletedWork,
    Opportunity,
    OpportunityAccess,
    OpportunityClaim,
    Payment,
    PaymentInvoice,
    PaymentUnit,
    UserInvite,
    UserInviteStatus,
    UserVisit,
)
from commcare_connect.opportunity.tests.factories import (
    AssessmentFactory,
    CompletedModuleFactory,
    CompletedWorkFactory,
    DeliverUnitFactory,
    DeliveryTypeFactory,
    OpportunityAccessFactory,
    OpportunityClaimFactory,
    OpportunityFactory,
    PaymentFactory,
    PaymentInvoiceFactory,
    PaymentUnitFactory,
    UserInviteFactory,
    UserVisitFactory,
)
from commcare_connect.organization.models import Organization
from commcare_connect.program.models import ManagedOpportunity, Program, ProgramApplication
from commcare_connect.program.tests.factories import (
    ManagedOpportunityFactory,
    ProgramApplicationFactory,
    ProgramFactory,
)
from commcare_connect.users.tests.factories import MobileUserFactory

User = get_user_model()
fake = Faker()


class Command(BaseCommand):
    help = "Generates fake data for testing purposes"

    def add_arguments(self, parser):
        parser.add_argument("num_visits", type=int, help="Number of visits to generate")
        parser.add_argument("org_slug", type=str, help="Primary organization slug to scope the data to")
        parser.add_argument(
            "--invited_org_slug",
            type=str,
            default=None,
            help="Slug for the invited organization (if not provided, one will be created)",
        )
        parser.add_argument(
            "--managed_opportunities",
            type=int,
            default=3,
            help="Number of managed opportunities to create",
        )

    def clean_sample_data(self, org_ids):
        self.stdout.write("Cleaning up previous sample data...")
        deletions = [
            lambda: Payment.objects.filter(opportunity_access__opportunity__organization_id__in=org_ids).delete(),
            lambda: PaymentInvoice.objects.filter(opportunity__organization_id__in=org_ids).delete(),
            lambda: UserVisit.objects.filter(opportunity__organization_id__in=org_ids).delete(),
            lambda: OpportunityClaim.objects.filter(
                opportunity_access__opportunity__organization_id__in=org_ids
            ).delete(),
            lambda: CompletedModule.objects.filter(opportunity__organization_id__in=org_ids).delete(),
            lambda: Assessment.objects.filter(opportunity__organization_id__in=org_ids).delete(),
            lambda: CompletedWork.objects.filter(
                opportunity_access__opportunity__organization_id__in=org_ids
            ).delete(),
            lambda: PaymentUnit.objects.filter(opportunity__organization_id__in=org_ids).delete(),
            lambda: UserInvite.objects.filter(opportunity__organization_id__in=org_ids).delete(),
            lambda: OpportunityAccess.objects.filter(opportunity__organization_id__in=org_ids).delete(),
            lambda: Opportunity.objects.filter(organization_id__in=org_ids).delete(),
            lambda: ManagedOpportunity.objects.filter(organization_id__in=org_ids).delete(),
            lambda: ProgramApplication.objects.filter(program__organization_id__in=org_ids).delete(),
            lambda: Program.objects.filter(organization_id__in=org_ids).delete(),
        ]

        for delete in deletions:
            delete()
        self.stdout.write("Cleanup complete.")

    def handle(self, *args, **options):
        num_visits = options["num_visits"]
        org_slug = options["org_slug"]
        invited_org_slug = options["invited_org_slug"]
        num_managed_opps = options["managed_opportunities"]
        BATCH_SIZE = 10000

        org, created = Organization.objects.get_or_create(slug=org_slug, defaults={"name": org_slug, "slug": org_slug})
        if created:
            self.stdout.write(f"Created primary organization: {org.name}")
        else:
            self.stdout.write(f"Using existing primary organization: {org.name}")

        if invited_org_slug:
            invited_org, invited_created = Organization.objects.get_or_create(
                slug=invited_org_slug,
                defaults={"name": invited_org_slug, "slug": invited_org_slug},
            )
        else:
            invited_org = Organization.objects.create(
                name=f"{org_slug} Invited Org",
                slug=f"{fake.slug()}-{random.randint(1000, 9999)}",
            )
            invited_created = True
        if invited_created:
            self.stdout.write(f"Created invited organization: {invited_org.name}")
        else:
            self.stdout.write(f"Using existing invited organization: {invited_org.name}")

        org_ids = [org.id, invited_org.id]
        self.clean_sample_data(org_ids)

        self.stdout.write("Generating prerequisite data (users, opportunities, etc.)...")

        users = [MobileUserFactory() for _ in range(10)]
        opportunities = [OpportunityFactory(organization=org) for _ in range(5)]

        managed_opportunities = []
        for _ in range(num_managed_opps):
            program = ProgramFactory(organization=org, delivery_type=DeliveryTypeFactory())
            ProgramApplicationFactory(program=program, organization=invited_org)
            managed_opp = ManagedOpportunityFactory(
                organization=invited_org, program=program, org_pay_per_visit=random.randint(500, 1000)
            )
            managed_opportunities.append(managed_opp)

        all_opportunities = opportunities + managed_opportunities
        all_accesses = []
        payment_units = {}
        for user in users:
            selected_opps = random.sample(all_opportunities, k=random.randint(1, 3))
            for opp in selected_opps:
                access = OpportunityAccessFactory(user=user, opportunity=opp)
                all_accesses.append(access)
                UserInviteFactory(opportunity=opp, opportunity_access=access, status=UserInviteStatus.accepted)

                # Create and cache PaymentUnit per opportunity
                if opp.id not in payment_units:
                    payment_units[opp.id] = PaymentUnitFactory(opportunity=opp)

                CompletedModuleFactory(opportunity=opp, user=user, opportunity_access=access)
                AssessmentFactory(opportunity=opp, user=user, opportunity_access=access)
                OpportunityClaimFactory(opportunity_access=access)

        self.stdout.write(f"Generating {num_visits} test visits for organization {org.name}...")

        if not all_accesses:
            self.stdout.write(self.style.WARNING("No OpportunityAccess records exist. Cannot generate visits."))
            return

        self.stdout.write("Pre-caching Deliver Units for all opportunities...")
        deliver_units_cache = {}
        for opp in all_opportunities:
            if opp.deliver_app and opp.deliver_app.id not in deliver_units_cache:
                deliver_units_cache[opp.deliver_app.id] = DeliverUnitFactory(app=opp.deliver_app)

        start_date = datetime(2024, 7, 1, tzinfo=timezone.utc)
        end_date = djtimezone.now()
        works_to_create = []

        for count in range(num_visits):
            opp_access = random.choice(all_accesses)
            user = opp_access.user
            opportunity = opp_access.opportunity

            # Generate random data
            lat, lon = random.uniform(-90, 90), random.uniform(-180, 180)
            location = f"{lat:.7f} {lon:.7f} 0.0 3099.99"
            delta_days = (end_date - start_date).days
            random_date = start_date + timedelta(days=random.randint(0, delta_days))
            time_start = random_date.replace(hour=random.randint(8, 17), minute=random.randint(0, 59))
            time_end = time_start + timedelta(minutes=random.randint(5, 60))

            deliver_unit = deliver_units_cache.get(opportunity.deliver_app_id)
            payment_unit = payment_units.get(opportunity.id)

            work = CompletedWorkFactory.build(
                opportunity_access=opp_access,
                payment_unit=payment_unit,
            )
            visit = UserVisitFactory.build(
                completed_work=None,
                opportunity=opportunity,
                user=user,
                opportunity_access=opp_access,
                deliver_unit=deliver_unit,
                visit_date=random_date,
                form_json={
                    "metadata": {
                        "location": location,
                        "timeStart": time_start.strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
                        "timeEnd": time_end.strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
                    }
                },
            )
            # Temporarily attach the visit to the work object to link them after creation
            work._transient_visit = visit
            works_to_create.append(work)

            if len(works_to_create) >= BATCH_SIZE:
                self.stdout.write(f"Processing batch of {len(works_to_create)} visits...")
                self._bulk_create_visits(works_to_create)
                works_to_create = []

        if works_to_create:
            self.stdout.write(f"Processing final batch of {len(works_to_create)} visits...")
            self._bulk_create_visits(works_to_create)

        self.stdout.write("Generating invoices and payments...")
        for opp in all_opportunities:
            num_invoices = random.randint(1, 6)
            for _ in range(num_invoices):
                invoice = PaymentInvoiceFactory(
                    opportunity=opp,
                    date=fake.date_time_between(start_date="-30d", end_date="now", tzinfo=timezone.utc),
                    invoice_number=fake.pystr(),
                )
                if random.choice([True, False]):
                    accesses = opp.opportunityaccess_set.all()
                    access = random.choice(list(accesses)) if accesses else None
                    PaymentFactory(
                        opportunity_access=access,
                        invoice=invoice,
                        amount=invoice.amount,
                        date_paid=invoice.date,
                    )

        self.stdout.write(self.style.SUCCESS(f"Successfully generated {num_visits} test visits."))

    @transaction.atomic
    def _bulk_create_visits(self, works_to_create):
        created_works = CompletedWork.objects.bulk_create(works_to_create)

        visits_for_creation = []
        for i, work_with_pk in enumerate(created_works):
            original_work_object = works_to_create[i]
            visit_object = original_work_object._transient_visit
            visit_object.completed_work = work_with_pk
            visits_for_creation.append(visit_object)

        UserVisit.objects.bulk_create(visits_for_creation)
