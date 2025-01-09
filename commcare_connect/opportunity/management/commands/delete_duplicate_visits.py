from django.core.management import BaseCommand
from django.db import transaction
from django.db.models import Count

from commcare_connect.opportunity.models import UserVisit


class Command(BaseCommand):
    help = "Clean up duplicate visits."

    def add_arguments(self, parser, *args, **kwargs):
        parser.add_argument("--opp", type=int, help="Opportunity ID to clean up duplicate visits.")
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="If set, just print the visits that would be deleted without actually deleting them.",
        )

    def handle(self, *args, **options):
        opportunity_id = options.get("opp")
        dry_run = options.get("dry_run")

        duplicates = (
            UserVisit.objects.filter(opportunity_id=opportunity_id)
            .values("opportunity", "entity_id", "deliver_unit", "xform_id")
            .annotate(visit_count=Count("id"))
            .filter(visit_count__gt=1)
        )

        if dry_run:
            self.stdout.write("Running in dry-run mode. No records will be deleted.")
        else:
            self.stdout.write("Attention: Records will be deleted!!")

        with transaction.atomic():
            for duplicate in duplicates:
                visits = UserVisit.objects.filter(
                    opportunity_id=opportunity_id,
                    entity_id=duplicate["entity_id"],
                    deliver_unit=duplicate["deliver_unit"],
                    xform_id=duplicate["xform_id"],
                ).order_by("id")

                visits_to_delete = visits[1:]

                for visit in visits_to_delete:
                    message = (
                        f"Identified duplicate visit: id={visit.id}, "
                        f"xform_id={visit.xform_id}, entity_id={visit.entity_id}, "
                        f"deliver_unit={visit.deliver_unit}, status={visit.status}"
                    )
                    self.stdout.write(message)

                    if not dry_run:
                        visit.delete()

        if not dry_run:
            self.stdout.write(
                self.style.SUCCESS(f"Duplicate visits for opportunity {opportunity_id} deleted successfully.")
            )
        else:
            self.stdout.write(f"Dry-run complete for opportunity {opportunity_id}")
