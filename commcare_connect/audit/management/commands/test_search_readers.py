"""
Test searching for readers using OAuth API
"""
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand

from commcare_connect.audit.management.extractors.connect_api_facade import ConnectAPIFacade

User = get_user_model()


class Command(BaseCommand):
    help = "Test searching for 'readers' opportunities using OAuth API"

    def add_arguments(self, parser):
        parser.add_argument(
            "--email", type=str, default="jjackson-dev@dimagi.com", help="Email to use for OAuth token"
        )
        parser.add_argument("--query", type=str, default="readers", help="Search query")

    def handle(self, *args, **options):
        email = options["email"]
        query = options["query"]

        self.stdout.write("\n" + "=" * 80)
        self.stdout.write(f"Testing opportunity search for: '{query}'")
        self.stdout.write("=" * 80 + "\n")

        # Get user
        try:
            user = User.objects.get(email=email)
            self.stdout.write(f"[OK] Found user: {user.email} (ID: {user.id})")
        except User.DoesNotExist:
            self.stdout.write(self.style.ERROR(f"[ERROR] User not found: {email}"))
            return

        # Check if user has OAuth token
        try:
            from commcare_connect.audit.helpers import get_connect_oauth_token

            token = get_connect_oauth_token(user)
            if token:
                self.stdout.write(f"[OK] User has OAuth token: ***{token[-8:]}")
            else:
                self.stdout.write(self.style.ERROR("[ERROR] User does not have OAuth token"))
                self.stdout.write("       Please complete OAuth flow in the web interface first")
                return
        except Exception as e:
            self.stdout.write(self.style.ERROR(f"[ERROR] Failed to get OAuth token: {e}"))
            return

        # Create facade with user
        self.stdout.write("\n[STEP 1] Creating ConnectAPIFacade with user...")
        try:
            facade = ConnectAPIFacade(user=user)
            self.stdout.write("[OK] Facade created")
            self.stdout.write(f"     - has_oauth_token: {facade.has_oauth_token}")
            self.stdout.write(f"     - production_url: {facade.production_url}")
            if facade.http_client:
                self.stdout.write(f"     - http_client: {facade.http_client}")
        except Exception as e:
            self.stdout.write(self.style.ERROR(f"[ERROR] Failed to create facade: {e}"))
            import traceback

            self.stdout.write(traceback.format_exc())
            return

        # Authenticate
        self.stdout.write("\n[STEP 2] Authenticating...")
        try:
            auth_result = facade.authenticate()
            if auth_result:
                self.stdout.write("[OK] Authentication successful")
            else:
                self.stdout.write(self.style.ERROR("[ERROR] Authentication failed"))
                return
        except Exception as e:
            self.stdout.write(self.style.ERROR(f"[ERROR] Authentication error: {e}"))
            import traceback

            self.stdout.write(traceback.format_exc())
            return

        # Search for opportunities
        self.stdout.write(f"\n[STEP 3] Searching for opportunities: '{query}'...")
        try:
            opportunities = facade.search_opportunities(query, limit=10)
            self.stdout.write("[OK] Search completed")
            self.stdout.write(f"     Found {len(opportunities)} opportunities")

            if opportunities:
                self.stdout.write("\n[RESULTS]")
                for i, opp in enumerate(opportunities, 1):
                    self.stdout.write(f"\n{i}. {opp.name}")
                    self.stdout.write(f"   - ID: {opp.id}")
                    self.stdout.write(f"   - Organization: {opp.organization_name}")
                    self.stdout.write(f"   - Program: {opp.program_name}")
                    if hasattr(opp, "visit_count"):
                        self.stdout.write(f"   - Visits: {opp.visit_count}")
            else:
                self.stdout.write(self.style.WARNING("\n[WARNING] No opportunities found"))

        except Exception as e:
            self.stdout.write(self.style.ERROR(f"\n[ERROR] Search failed: {e}"))
            import traceback

            self.stdout.write("\n[TRACEBACK]")
            self.stdout.write(traceback.format_exc())
            return

        # Clean up
        try:
            facade.close()
            self.stdout.write("\n[OK] Facade closed")
        except Exception as e:
            self.stdout.write(f"\n[WARNING] Error closing facade: {e}")

        self.stdout.write("\n" + "=" * 80)
        self.stdout.write("Test complete!")
        self.stdout.write("=" * 80 + "\n")
