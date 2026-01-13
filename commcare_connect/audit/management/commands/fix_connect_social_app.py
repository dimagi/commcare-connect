"""
Fix Connect Social App site association
"""
from allauth.socialaccount.models import SocialApp
from django.conf import settings
from django.contrib.sites.models import Site
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Fix Connect Social App site association for local development"

    def handle(self, *args, **options):
        self.stdout.write("\nFixing Connect Social App site association...")

        # Get or create the default site
        site, created = Site.objects.get_or_create(
            pk=settings.SITE_ID, defaults={"domain": "localhost:8000", "name": "Local Development"}
        )

        if created:
            self.stdout.write(self.style.SUCCESS(f"  Created site: {site.domain}"))
        else:
            self.stdout.write(f"  Found existing site: {site.domain} (ID: {site.pk})")

        # Get Connect Social App
        try:
            connect_app = SocialApp.objects.get(provider="connect")
            self.stdout.write(f"  Found Connect Social App: {connect_app.name}")

            # Add site if not already associated
            if site not in connect_app.sites.all():
                connect_app.sites.add(site)
                self.stdout.write(self.style.SUCCESS(f"  ✓ Associated {site.domain} with Connect Social App"))
            else:
                self.stdout.write("  Site already associated")

            # Show all associated sites
            self.stdout.write("\n  Associated sites:")
            for s in connect_app.sites.all():
                self.stdout.write(f"    - {s.domain} (ID: {s.pk})")

            self.stdout.write(self.style.SUCCESS("\n✓ Connect OAuth should now work!"))
            self.stdout.write("  Refresh your browser and try 'Get Connect OAuth Token' again.\n")

        except SocialApp.DoesNotExist:
            self.stdout.write(self.style.ERROR("  ✗ Connect Social App not found!"))
            self.stdout.write("  You need to create it in Django Admin > Social Applications")
