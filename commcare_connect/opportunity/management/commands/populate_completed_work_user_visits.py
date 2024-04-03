from itertools import groupby

from django.core.management.base import BaseCommand

from commcare_connect.opportunity.models import CompletedWork, OpportunityAccess, PaymentUnit, UserVisit


class Command(BaseCommand):
    help = "Creates Completed Work for User Visits and populates user visits with reference to completed work"

    def add_arguments(self, parser, *args, **kwargs):
        parser.add_argument("--opp", type=int)

    def handle(self, *args, **options):
        opp_id = options.get("opp")
        filter_kwargs = {"opportunity": opp_id} if opp_id else {}
        access_objects = OpportunityAccess.objects.filter(**filter_kwargs)
        for access in access_objects:
            payment_units = PaymentUnit.objects.filter(opportunity=access.opportunity)
            for payment_unit in payment_units:
                user_visits = UserVisit.objects.filter(
                    opportunity=access.opportunity,
                    user=access.user,
                    deliver_unit__in=payment_unit.deliver_units.all(),
                    is_trial=False,
                )
                for _, visits in groupby(user_visits, key=lambda x: x.entity_id):
                    for visit in visits:
                        completed_work, _ = CompletedWork.objects.get_or_create(
                            opportunity_access=access,
                            payment_unit=payment_unit,
                            entity_id=visit.entity_id,
                            defaults={"entity_name": visit.entity_name},
                        )
                        visit.completed_work = completed_work
                        visit.save()
