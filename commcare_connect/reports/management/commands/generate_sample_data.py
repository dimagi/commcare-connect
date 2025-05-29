import random
from datetime import datetime, timedelta, timezone

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
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
        # List deletion lambdas in order to avoid constraint issues.
        deletions = [
            lambda: Payment.objects.filter(opportunity_access__opportunity__organization_id__in=org_ids).delete(),
            lambda: PaymentInvoice.objects.filter(opportunity__organization_id__in=org_ids).delete(),
            lambda: UserVisit.objects.filter(opportunity__organization_id__in=org_ids).delete(),
            lambda: OpportunityClaim.objects.filter(
                opportunity_access__opportunity__organization_id__in=org_ids
            ).delete(),
            lambda: CompletedModule.objects.filter(opportunity__organization_id__in=org_ids).delete(),
            lambda: Assessment.objects.filter(opportunity__organization_id__in=org_ids).delete(),
            lambda: CompletedWork.objects.filter(opportunity_access__opportunity_id__in=org_ids).delete(),
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

        # Clean up prior sample data scoped to these organizations
        org_ids = [org.id, invited_org.id]
        self.clean_sample_data(org_ids)

        self.stdout.write(f"Generating {num_visits} test visits scoped to organization {org.name}...")

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
        for user in users:
            selected_opps = random.sample(all_opportunities, k=random.randint(1, 3))
            for opp in selected_opps:
                access = OpportunityAccessFactory(user=user, opportunity=opp)
                all_accesses.append(access)

                UserInviteFactory(
                    opportunity=opp,
                    opportunity_access=access,
                    status=random.choice([choice[0] for choice in UserInviteStatus.choices]),
                )

                PaymentUnitFactory(opportunity=opp)

                CompletedModuleFactory(
                    opportunity=opp,
                    user=user,
                    opportunity_access=access,
                    date=datetime.now(timezone.utc) - timedelta(days=random.randint(1, 30)),
                )

                AssessmentFactory(
                    opportunity=opp,
                    user=user,
                    opportunity_access=access,
                    passed=random.choice([True, False]),
                )

                OpportunityClaimFactory(opportunity_access=access)

        start_date = datetime(2024, 1, 1, tzinfo=timezone.utc)
        end_date = djtimezone.now()

        for count in range(num_visits):
            self.stdout.write(f"{count + 1}/{num_visits}...")
            user = random.choice(users)
            access_qs = user.opportunityaccess_set.all()
            if not access_qs:
                continue
            opp_access = random.choice(access_qs)
            completed_work = CompletedWorkFactory(opportunity_access=opp_access)

            lat = random.uniform(-90, 90)
            lon = random.uniform(-180, 180)
            location = f"{lat:.7f} {lon:.7f} 0.0 3099.99"

            delta_days = (end_date - start_date).days
            random_date = start_date + timedelta(days=random.randint(0, delta_days))

            time_start = random_date.replace(
                hour=random.randint(8, 17),
                minute=random.randint(0, 59),
                second=random.randint(0, 59),
                microsecond=random.randint(0, 999999),
            )
            duration = timedelta(minutes=random.randint(5, 60))
            time_end = time_start + duration

            UserVisitFactory(
                completed_work=completed_work,
                opportunity=opp_access.opportunity,
                user=user,
                opportunity_access=opp_access,
                deliver_unit=DeliverUnitFactory(app=opp_access.opportunity.deliver_app),
                visit_date=random_date,
                form_json={
                    "metadata": {
                        "location": location,
                        "timeStart": time_start.strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
                        "timeEnd": time_end.strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
                    }
                },
            )

        # Generate invoices and payments for each opportunity
        self.stdout.write("Generating invoices and payments for each opportunity...")
        for opp in all_opportunities:
            num_invoices = random.randint(1, 6)
            for _ in range(num_invoices):
                invoice = PaymentInvoiceFactory(
                    opportunity=opp,
                    # Creates a date within the past 30 days.
                    date=fake.date_time_between(start_date="-30d", end_date="now", tzinfo=timezone.utc),
                    invoice_number=fake.pystr(),
                )
                # Randomly decide to create a payment for this invoice (about 50% chance)
                if random.choice([True, False]):
                    # If possible, select a random access for the opportunity; otherwise None is passed.
                    accesses = opp.opportunityaccess_set.all()
                    access = random.choice(accesses) if accesses else None
                    PaymentFactory(
                        opportunity_access=access,
                        invoice=invoice,
                        amount=invoice.amount,
                        date_paid=invoice.date,
                    )

        self.stdout.write(
            self.style.SUCCESS(
                f"{num_visits} test visits and associated invoices/payments generated successfully for organization {org.name}!"  # noqa: E501
            )
        )
