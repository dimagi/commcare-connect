from django.core.management import BaseCommand

from commcare_connect.opportunity.models import OpportunityAccess
from commcare_connect.opportunity.utils.completed_work import _update_status_set_saved_fields_and_get_payment_accrued


class Command(BaseCommand):
    help = (
        "Populates the saved_* fields on CompletedWork objects "
        "(and also the payment_accrued field on OpportunityAccess)"
    )

    def handle(self, *args, **options):
        for opportunity_access in OpportunityAccess.objects.all():
            completed_works = opportunity_access.completedwork_set.all()
            for completed_work in completed_works:
                _update_status_set_saved_fields_and_get_payment_accrued(
                    completed_work, opportunity_access, compute_payment=True
                )

            self.stdout.write(
                self.style.SUCCESS(f"Successfully processed opportunity access with id {opportunity_access.id}")
            )
