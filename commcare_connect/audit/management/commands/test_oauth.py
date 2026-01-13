"""
Test OAuth configuration and verify redirect to Connect production
"""
import httpx
from allauth.socialaccount.models import SocialApp
from django.conf import settings
from django.core.management.base import BaseCommand

from commcare_connect.audit.oauth_views import ConnectOAuth2Adapter


class Command(BaseCommand):
    help = "Test OAuth configuration for Connect production"

    def handle(self, *args, **options):
        self.stdout.write("\n" + "=" * 60)
        self.stdout.write("Testing Connect OAuth Configuration")
        self.stdout.write("=" * 60 + "\n")

        # 1. Check settings
        self.stdout.write(self.style.SUCCESS("1. Checking settings:"))
        self.stdout.write(f"   CONNECT_PRODUCTION_URL: {settings.CONNECT_PRODUCTION_URL}")
        client_id_display = (
            "***" + settings.CONNECT_OAUTH_CLIENT_ID[-4:] if settings.CONNECT_OAUTH_CLIENT_ID else "NOT SET"
        )
        self.stdout.write(f"   CONNECT_OAUTH_CLIENT_ID: {client_id_display}")
        client_secret_display = (
            "***" + settings.CONNECT_OAUTH_CLIENT_SECRET[-4:] if settings.CONNECT_OAUTH_CLIENT_SECRET else "NOT SET"
        )
        self.stdout.write(f"   CONNECT_OAUTH_CLIENT_SECRET: {client_secret_display}")

        # 2. Check adapter configuration
        self.stdout.write(self.style.SUCCESS("\n2. Checking OAuth2 Adapter:"))
        adapter = ConnectOAuth2Adapter(None)
        self.stdout.write(f"   Provider ID: {adapter.provider_id}")
        self.stdout.write(f"   Authorize URL: {adapter.authorize_url}")
        self.stdout.write(f"   Token URL: {adapter.access_token_url}")
        self.stdout.write(f"   Profile URL: {adapter.profile_url}")

        # 3. Check Social Application in database
        self.stdout.write(self.style.SUCCESS("\n3. Checking Social Application in database:"))
        try:
            social_apps = SocialApp.objects.filter(provider="connect")
            if social_apps.exists():
                for app in social_apps:
                    self.stdout.write(f"   [OK] Found Social App: {app.name}")
                    self.stdout.write(f"     - Provider: {app.provider}")
                    self.stdout.write(
                        f"     - Client ID: {'***' + app.client_id[-4:] if app.client_id else 'NOT SET'}"
                    )
                    self.stdout.write(f"     - Secret: {'***' + app.secret[-4:] if app.secret else 'NOT SET'}")
                    self.stdout.write(f"     - Sites: {', '.join(str(site) for site in app.sites.all())}")
            else:
                self.stdout.write(self.style.ERROR("   [ERROR] No Social Application found for provider 'connect'"))
                self.stdout.write("   You need to create one in Django Admin > Social Applications")
        except Exception as e:
            self.stdout.write(self.style.ERROR(f"   Error checking database: {e}"))

        # 4. Test OAuth authorize endpoint
        self.stdout.write(self.style.SUCCESS("\n4. Testing OAuth authorize endpoint:"))
        try:
            # Test if we can reach the authorize endpoint
            test_url = f"{settings.CONNECT_PRODUCTION_URL}/o/authorize/"
            self.stdout.write(f"   Testing: {test_url}")

            response = httpx.get(test_url, follow_redirects=False, timeout=10)
            self.stdout.write(f"   Status: {response.status_code}")

            if response.status_code in [200, 302, 400]:
                self.stdout.write(self.style.SUCCESS("   [OK] OAuth endpoint is reachable"))
                if response.status_code == 400:
                    self.stdout.write("     (400 is expected without proper OAuth params)")
            else:
                self.stdout.write(self.style.WARNING(f"   Unexpected status code: {response.status_code}"))

        except Exception as e:
            self.stdout.write(self.style.ERROR(f"   [ERROR] Failed to reach OAuth endpoint: {e}"))

        # 5. Test token endpoint
        self.stdout.write(self.style.SUCCESS("\n5. Testing OAuth token endpoint:"))
        try:
            token_url = f"{settings.CONNECT_PRODUCTION_URL}/o/token/"
            self.stdout.write(f"   Testing: {token_url}")

            response = httpx.post(token_url, timeout=10)
            self.stdout.write(f"   Status: {response.status_code}")

            if response.status_code in [400, 401]:
                self.stdout.write(self.style.SUCCESS("   [OK] Token endpoint is reachable"))
                self.stdout.write("     (400/401 is expected without valid credentials)")
            else:
                self.stdout.write(self.style.WARNING(f"   Unexpected status code: {response.status_code}"))

        except Exception as e:
            self.stdout.write(self.style.ERROR(f"   [ERROR] Failed to reach token endpoint: {e}"))

        # Summary
        self.stdout.write("\n" + "=" * 60)
        self.stdout.write("Summary:")
        self.stdout.write("=" * 60)

        if settings.CONNECT_PRODUCTION_URL == "https://connect.dimagi.com":
            self.stdout.write(self.style.SUCCESS("[OK] OAuth is configured to target PRODUCTION Connect"))
        else:
            self.stdout.write(self.style.WARNING(f"[WARNING] OAuth is targeting: {settings.CONNECT_PRODUCTION_URL}"))

        self.stdout.write("\nNext steps:")
        self.stdout.write("1. Ensure Social Application exists in Django Admin with provider='connect'")
        self.stdout.write("2. Restart Django server after any changes")
        self.stdout.write("3. Click 'Get Connect OAuth Token' button")
        self.stdout.write("4. You should be redirected to https://connect.dimagi.com/o/authorize/")
        self.stdout.write("")
