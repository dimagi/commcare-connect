from django.core.management import BaseCommand

from commcare_connect.reports.tasks import sync_user_analytics_data


class Command(BaseCommand):
    help = "Backfills User Analytics Data"

    def handle(self, *args, **options):
        sync_user_analytics_data()
