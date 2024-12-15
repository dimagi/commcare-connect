from django.core.management import BaseCommand

from commcare_connect.opportunity.models import CompletedWorkStatus, Opportunity, OpportunityAccess
from commcare_connect.opportunity.utils.completed_work import update_status


class Command(BaseCommand):
    help = "Run auto-approval logic over opportunity"

    def add_arguments(self, parser):
        parser.add_argument(
            "--opp",
            type=int,
            required=True,
            help="ID of the opportunity to run auto-approval logic on",
        )
        parser.add_argument(
            "--include-over-limit", action="store_true", help="Also run auto-approval logic on over limit works"
        )
        parser.add_argument("--update-payment", action="store_true", help="Update payment accrued")

    def handle(self, *args, opp: int, **options):
        include_over_limit = options.get("include_over_limit", False)
        update_payment = options.get("update_payment", False)
        excluded = [CompletedWorkStatus.rejected]
        if not include_over_limit:
            excluded.append(CompletedWorkStatus.over_limit)
        try:
            opportunity = Opportunity.objects.get(id=opp)
            access_objects = OpportunityAccess.objects.filter(
                opportunity=opportunity, suspended=False, opportunity__auto_approve_payments=True
            )
            for access in access_objects:
                completed_works = access.completedwork_set.exclude(status__in=excluded)
                update_status(completed_works, access, update_payment)

            self.stdout.write(self.style.SUCCESS(f"Successfully processed opportunity with id {opp}"))

        except Opportunity.DoesNotExist:
            self.stdout.write(self.style.ERROR(f"Opportunity with id {opp} does not exist."))
