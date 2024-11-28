import random
from datetime import datetime, timedelta, timezone

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand

from commcare_connect.conftest import MobileUserFactory
from commcare_connect.opportunity.models import CompletedWorkStatus
from commcare_connect.opportunity.tests.factories import (
    CompletedWorkFactory,
    OpportunityAccessFactory,
    OpportunityFactory,
    UserVisitFactory,
)

User = get_user_model()


class Command(BaseCommand):
    help = "Generates test data for reporting purposes"

    def add_arguments(self, parser):
        parser.add_argument("num_visits", type=int, help="Number of visits to generate")

    def handle(self, *args, **options):
        num_visits = options["num_visits"]
        self.stdout.write(f"Generating {num_visits} test visits...")

        # Create test users
        users = [MobileUserFactory() for _ in range(10)]

        # Create opportunities and opportunity accesses
        opportunities = [OpportunityFactory() for _ in range(5)]
        for user in users:
            for opportunity in random.sample(opportunities, k=random.randint(1, 3)):
                OpportunityAccessFactory(user=user, opportunity=opportunity)

        # Generate completed work and user visits
        start_date = datetime(2024, 1, 1, tzinfo=timezone.utc)
        end_date = datetime.now(timezone.utc)

        for count in range(num_visits):
            print(f"{count + 1}/{num_visits}...")
            user = random.choice(users)
            opportunity_access = random.choice(user.opportunityaccess_set.all())
            completed_work = CompletedWorkFactory(
                opportunity_access=opportunity_access,
                status=CompletedWorkStatus.approved,
            )
            # Generate random, valid lat/lon
            lat = random.uniform(-90, 90)
            lon = random.uniform(-180, 180)
            location = f"{lat:.7f} {lon:.7f} 0.0 3099.99"

            # Generate random date between start_date and end_date
            random_date = start_date + timedelta(days=random.randint(0, (end_date - start_date).days))

            # Generate dynamic timeStart and timeEnd
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
                visit_date=random_date,
                form_json={
                    "metadata": {
                        "location": location,
                        "timeStart": time_start.strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
                        "timeEnd": time_end.strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
                    }
                },
            )

        self.stdout.write(self.style.SUCCESS(f"{num_visits} test visits generated successfully!"))
