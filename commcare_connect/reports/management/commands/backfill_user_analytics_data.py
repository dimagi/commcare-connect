from django.core.management import BaseCommand

from commcare_connect.reports.tasks import update_user_analytics_data


class Command(BaseCommand):
    help = "Backfills User Analytics Data"

    def handle(self, *args, **options):
        update_user_analytics_data()
