from django.core.management import BaseCommand

from commcare_connect.opportunity.models import CompletedWorkStatus, Opportunity, OpportunityAccess
from commcare_connect.opportunity.utils.completed_work import update_status


class Command(BaseCommand):
    help = "Run auto-approval logic over opportunity"

    def add_arguments(self, parser):
        parser.add_argument(
            "--opp", type=int, required=True, help="ID of the opportunity to run auto-approval logic on"
        )

    def handle(self, *args, opp: int, **options):
        try:
            opportunity = Opportunity.objects.get(id=opp)
            access_objects = OpportunityAccess.objects.filter(
                opportunity=opportunity, suspended=False, opportunity__auto_approve_payments=True
            )
            for access in access_objects:
                completed_works = access.completedwork_set.exclude(
                    status__in=[CompletedWorkStatus.rejected, CompletedWorkStatus.over_limit]
                )
                update_status(completed_works, access, False)

            self.stdout.write(self.style.SUCCESS(f"Successfully processed opportunity with id {opp}"))

        except Opportunity.DoesNotExist:
            self.stdout.write(self.style.ERROR(f"Opportunity with id {opp} does not exist."))
