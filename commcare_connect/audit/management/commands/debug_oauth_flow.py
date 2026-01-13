"""
Debug OAuth flow to understand why redirect isn't happening
"""
from allauth.socialaccount import providers
from allauth.socialaccount.models import SocialApp
from django.conf import settings
from django.contrib.sites.models import Site
from django.core.management.base import BaseCommand
from django.test import RequestFactory

from commcare_connect.audit.oauth_views import ConnectOAuth2Adapter


class Command(BaseCommand):
    help = "Debug OAuth flow step by step"

    def handle(self, *args, **options):
        self.stdout.write("\n" + "=" * 80)
        self.stdout.write("DEBUGGING OAUTH FLOW")
        self.stdout.write("=" * 80 + "\n")

        # Step 1: Check if provider is registered
        self.stdout.write(self.style.SUCCESS("STEP 1: Check if Connect provider is registered"))
        try:
            registry = providers.registry
            self.stdout.write(f"  All registered providers: {list(registry.get_list())}")

            connect_provider = registry.by_id("connect")
            self.stdout.write(f"  [OK] Connect provider found: {connect_provider}")
            self.stdout.write(f"       Provider class: {connect_provider.__class__}")
            self.stdout.write(f"       Provider ID: {connect_provider.id}")
            self.stdout.write(f"       Provider name: {connect_provider.name}")
        except Exception as e:
            self.stdout.write(self.style.ERROR(f"  [ERROR] Connect provider not registered: {e}"))
            return

        # Step 2: Check SocialApp in database
        self.stdout.write(self.style.SUCCESS("\nSTEP 2: Check SocialApp in database"))
        try:
            site = Site.objects.get(pk=settings.SITE_ID)
            self.stdout.write(f"  Current site: {site.domain} (ID: {site.pk})")

            social_app = SocialApp.objects.get(provider="connect")
            self.stdout.write(f"  [OK] SocialApp found: {social_app.name}")
            self.stdout.write(f"       Provider: {social_app.provider}")
            self.stdout.write(f"       Client ID: ***{social_app.client_id[-4:]}")
            self.stdout.write(f"       Sites: {[s.domain for s in social_app.sites.all()]}")

            if site in social_app.sites.all():
                self.stdout.write("  [OK] Current site is associated with SocialApp")
            else:
                self.stdout.write(self.style.ERROR("  [ERROR] Current site NOT associated!"))
                return

        except SocialApp.DoesNotExist:
            self.stdout.write(self.style.ERROR("  [ERROR] SocialApp with provider='connect' not found"))
            return

        # Step 3: Check adapter configuration
        self.stdout.write(self.style.SUCCESS("\nSTEP 3: Check OAuth adapter configuration"))
        adapter = ConnectOAuth2Adapter(None)
        self.stdout.write(f"  Adapter class: {adapter.__class__}")
        self.stdout.write(f"  Provider ID: {adapter.provider_id}")
        self.stdout.write(f"  Authorize URL: {adapter.authorize_url}")
        self.stdout.write(f"  Token URL: {adapter.access_token_url}")
        self.stdout.write(f"  Profile URL: {adapter.profile_url}")

        # Step 4: Simulate the OAuth login request
        self.stdout.write(self.style.SUCCESS("\nSTEP 4: Simulate OAuth login request"))
        factory = RequestFactory()
        request = factory.get("/audit/oauth/connect/login/")
        request.user = None  # Anonymous user
        request.session = {}

        self.stdout.write(f"  Request path: {request.path}")
        self.stdout.write(f"  Request method: {request.method}")

        # Step 5: Try to get the app for the provider
        self.stdout.write(self.style.SUCCESS("\nSTEP 5: Check if adapter can retrieve SocialApp"))
        try:
            # This is what allauth does internally
            apps = SocialApp.objects.filter(provider="connect", sites__id=settings.SITE_ID)

            if apps.exists():
                app = apps.first()
                self.stdout.write(f"  [OK] SocialApp can be retrieved: {app.name}")
                self.stdout.write(f"       Client ID: ***{app.client_id[-4:]}")
            else:
                self.stdout.write(self.style.ERROR("  [ERROR] SocialApp cannot be retrieved via query"))
                self.stdout.write(
                    f"       Query: SocialApp.objects.filter(provider='connect', sites__id={settings.SITE_ID})"
                )

        except Exception as e:
            self.stdout.write(self.style.ERROR(f"  [ERROR] Exception retrieving SocialApp: {e}"))

        # Step 6: Check what the view function should do
        self.stdout.write(self.style.SUCCESS("\nSTEP 6: Expected OAuth flow"))
        self.stdout.write("  1. User clicks 'Get Connect OAuth Token' button")
        self.stdout.write("  2. Browser navigates to /audit/oauth/connect/login/")
        self.stdout.write("  3. oauth2_login view function is called")
        self.stdout.write("  4. View retrieves SocialApp from database")
        self.stdout.write("  5. View generates OAuth authorize URL with:")
        self.stdout.write(f"     - Base URL: {adapter.authorize_url}")
        self.stdout.write("     - Client ID: from SocialApp")
        self.stdout.write("     - Redirect URI: http://localhost:8000/audit/oauth/connect/callback/")
        self.stdout.write("     - State: random token for CSRF protection")
        self.stdout.write(f"     - Scope: {connect_provider.get_default_scope()}")
        self.stdout.write("  6. View returns 302 redirect to Connect production")
        self.stdout.write("  7. User sees Connect production login page")

        # Step 7: Check URL configuration
        self.stdout.write(self.style.SUCCESS("\nSTEP 7: Check URL configuration"))
        self.stdout.write("  Audit app URLs:")
        self.stdout.write("    - /audit/oauth/connect/login/ -> oauth_views.oauth2_login")
        self.stdout.write("    - /audit/oauth/connect/callback/ -> oauth_views.oauth2_callback")
        self.stdout.write("\n  Allauth URLs:")
        self.stdout.write("    - /accounts/oauth/connect/login/ -> (auto-generated by allauth)")
        self.stdout.write("    - /accounts/oauth/connect/callback/ -> (auto-generated by allauth)")

        # Summary
        self.stdout.write("\n" + "=" * 80)
        self.stdout.write("DIAGNOSTIC SUMMARY")
        self.stdout.write("=" * 80)
        self.stdout.write("\nIf you're seeing socialaccount/login.html template:")
        self.stdout.write("  - This means the OAuth view couldn't retrieve the SocialApp")
        self.stdout.write("  - OR the adapter isn't properly configured")
        self.stdout.write("  - OR the view function isn't using the adapter")
        self.stdout.write("\nExpected behavior:")
        self.stdout.write("  - You should get a 302 redirect")
        self.stdout.write('  - Django console should show: [date] "GET /audit/oauth/connect/login/ HTTP/1.1" 302')
        self.stdout.write("  - Browser should navigate to: https://connect.dimagi.com/o/authorize/?...")
        self.stdout.write("")
