from django.core.management.base import BaseCommand

from commcare_connect.opportunity.models import Assessment, CompletedModule, OpportunityAccess, UserVisit


class Command(BaseCommand):
    help = "Populates opportunity_access foriegn key on Assessment, CompletedModules and UserVisit tables."

    def handle(self, *args, **kwargs):
        access_objects = OpportunityAccess.objects.all()

        for access in access_objects:
            UserVisit.objects.filter(user=access.user, opportunity=access.opportunity).update(
                opportunity_access=access
            )
            CompletedModule.objects.filter(user=access.user, opportunity=access.opportunity).update(
                opportunity_access=access
            )
            Assessment.objects.filter(user=access.user, opportunity=access.opportunity).update(
                opportunity_access=access
            )
