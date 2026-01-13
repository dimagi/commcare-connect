"""
Simplified management command to test unified dashboard links and pagination.
Run with: python manage.py test_dashboard_links
"""
import re
from urllib.parse import parse_qs, urlparse

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.test import Client

User = get_user_model()


class Command(BaseCommand):
    help = "Test unified dashboard links and pagination functionality"

    def add_arguments(self, parser):
        parser.add_argument(
            "--user",
            type=str,
            default="jjackson-dev@dimagi.com",
            help="Email of user to test with",
        )
        parser.add_argument(
            "--pagination-only",
            action="store_true",
            help="Skip to pagination test only",
        )

    def handle(self, *args, **options):
        user_email = options["user"]
        pagination_only = options["pagination_only"]

        # Get the user
        try:
            user = User.objects.get(email=user_email)
        except User.DoesNotExist:
            self.stdout.write(self.style.ERROR(f"User {user_email} not found. Run UAT data script first."))
            return

        # Temporarily add testserver to ALLOWED_HOSTS for testing
        current_allowed_hosts = list(settings.ALLOWED_HOSTS)
        if "testserver" not in current_allowed_hosts:
            settings.ALLOWED_HOSTS = current_allowed_hosts + ["testserver"]

        # Create test client and login
        client = Client()
        client.force_login(user)

        self.stdout.write(f"\n=== Testing Unified Dashboard for {user.email} ===")

        def test_url(url, description):
            try:
                response = client.get(url, follow=True)
                if response.status_code == 200:
                    self.stdout.write(f"✓ {description}")
                    return response
                else:
                    self.stdout.write(self.style.ERROR(f"✗ {description}: HTTP {response.status_code}"))
                    return None
            except Exception as e:
                self.stdout.write(self.style.ERROR(f"✗ {description}: {str(e)}"))
                return None

        def extract_solicitation_links(html_content):
            """Extract solicitation-related action links from HTML"""
            links = []
            href_pattern = r'<a[^>]+href=["\']([^"\']+)["\'][^>]*>(.*?)</a>'

            for match in re.finditer(href_pattern, html_content, re.DOTALL | re.IGNORECASE):
                href = match.group(1)
                link_content = match.group(2)

                # Only include solicitation-related links, but exclude sort links
                if ("/solicitations/" in href or href.startswith("/a/")) and "sort=" not in href:
                    # Get description from icon or text
                    description = "Action"
                    if "fa-eye" in link_content:
                        description = "View"
                    elif "fa-edit" in link_content or "fa-pen-to-square" in link_content:
                        description = "Edit"
                    elif "fa-inbox" in link_content:
                        description = "View Responses"
                    elif "Create" in link_content:
                        description = "Create"

                    links.append((href, description))

            return links

        def extract_pagination_links(html_content):
            """Extract pagination links from django-tables2 pagination"""
            pagination_links = []

            # Look for pagination links in django-tables2 format
            pagination_pattern = r'<a[^>]+href=["\']([^"\']*\?[^"\']*page=[^"\']*)["\'][^>]*>'

            for match in re.finditer(pagination_pattern, html_content):
                href = match.group(1)
                parsed_url = urlparse(href)
                query_params = parse_qs(parsed_url.query)

                if "page" in query_params:
                    page_num = query_params["page"][0]
                    pagination_links.append((href, f"Page {page_num}"))

            return pagination_links

        # 1. Test Main Dashboard
        self.stdout.write("\n--- Testing Main Dashboard ---")
        dashboard_response = test_url("/solicitations/dashboard/", "Main dashboard")
        if not dashboard_response:
            return

        dashboard_content = dashboard_response.content.decode()

        if not pagination_only:
            # 2. Test all action links on dashboard
            self.stdout.write("\n--- Testing Dashboard Action Links ---")
            dashboard_links = extract_solicitation_links(dashboard_content)

            for url, description in dashboard_links:
                if not test_url(url, f"Dashboard {description}: {url}"):
                    return

        # 3. Test Pagination (if pagination links exist on the page)
        self.stdout.write("\n--- Testing Pagination ---")

        # Extract pagination links from the main dashboard page
        pagination_links = extract_pagination_links(dashboard_content)

        if pagination_links:
            # Test the first few pagination links found on the page
            for url, desc in pagination_links[:5]:  # Test first 5 pagination links
                if not test_url(url, f"Pagination: {desc}"):
                    return
        else:
            self.stdout.write("No pagination links found on dashboard")

        # Restore original ALLOWED_HOSTS
        settings.ALLOWED_HOSTS = current_allowed_hosts

        self.stdout.write(self.style.SUCCESS("All tests passed! Dashboard working correctly."))
