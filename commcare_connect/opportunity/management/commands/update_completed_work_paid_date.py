from django.core.management import BaseCommand
from django.db import transaction

from commcare_connect.opportunity.models import OpportunityAccess
from commcare_connect.opportunity.utils.completed_work import update_work_payment_date


class Command(BaseCommand):
    help = "Updates paid dates from payments for all opportunity accesses"

    def handle(self, *args, **kwargs):
        try:
            with transaction.atomic():
                accesses = OpportunityAccess.objects.all()
                self.stdout.write("Starting to process to update the paid date...")

                for access in accesses:
                    update_work_payment_date(access)

                self.stdout.write("Process completed")

        except Exception as e:
            self.stdout.write(self.style.ERROR(f"An error occurred: {str(e)}"))
