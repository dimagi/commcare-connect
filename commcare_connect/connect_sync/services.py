"""
Shared service for syncing data from Connect API to local database.

This service provides lightweight methods for syncing opportunities and users
from the Connect production instance (via OAuth API) to the local database.

Usage:
    - Use this service for task creation and other lightweight operations
    - For full audit workflows with deliver units, visits, etc., use AuditDataLoader

Comparison:
    - ConnectDataSyncService: Uses OAuth API, lightweight, good for basic opp/user sync
    - AuditDataLoader: Uses Superset + API, includes deliver units/visits/attachments

Example:
    facade = ConnectAPIFacade(user=request.user)
    facade.authenticate()

    sync_service = ConnectDataSyncService(facade)
    opportunity = sync_service.sync_opportunity(385)
    users = sync_service.sync_users_by_username(['user1', 'user2'], 385)

    facade.close()
"""

from django.contrib.auth import get_user_model

from commcare_connect.audit.management.extractors.connect_api_facade import ConnectAPIFacade
from commcare_connect.opportunity.models import Opportunity
from commcare_connect.organization.models import Organization

User = get_user_model()


class ConnectDataSyncService:
    """Service for syncing data from Connect API to local database."""

    def __init__(self, facade: ConnectAPIFacade):
        """
        Initialize the sync service.

        Args:
            facade: Authenticated ConnectAPIFacade instance
        """
        self.facade = facade

    def sync_opportunity(self, opportunity_id: int) -> Opportunity:
        """
        Sync a single opportunity from Connect API to local database.

        Args:
            opportunity_id: The opportunity ID to sync

        Returns:
            The synced Opportunity instance

        Raises:
            ValueError: If opportunity not found in API
        """
        # Check if already exists locally
        existing = Opportunity.objects.filter(id=opportunity_id).first()
        if existing:
            return existing

        # Fetch from API
        opportunities = self.facade.search_opportunities(query=str(opportunity_id), limit=1)
        if not opportunities or opportunities[0].id != opportunity_id:
            raise ValueError(f"Opportunity {opportunity_id} not found in Connect API")

        opp_data = opportunities[0]

        # Get or create organization
        org, _ = Organization.objects.get_or_create(
            slug=f"org-{opportunity_id}",  # Using opportunity ID as slug fallback
            defaults={"name": opp_data.organization_name or "Unknown Organization"},
        )

        # Create opportunity
        opportunity = Opportunity.objects.create(
            id=opp_data.id,
            name=opp_data.name,
            description=opp_data.description or "",
            organization=org,
            active=opp_data.active,
            start_date=opp_data.start_date,
            end_date=opp_data.end_date,
            is_test=opp_data.is_test,
        )

        return opportunity

    def sync_opportunities(self, opportunity_ids: list[int]) -> dict[int, Opportunity]:
        """
        Sync multiple opportunities from Connect API to local database.

        Args:
            opportunity_ids: List of opportunity IDs to sync

        Returns:
            Dictionary mapping opportunity ID to Opportunity instance
        """
        opportunities = {}
        for opp_id in opportunity_ids:
            try:
                opportunities[opp_id] = self.sync_opportunity(opp_id)
            except ValueError as e:
                print(f"[WARNING] {e}")
                continue
        return opportunities

    def sync_user_by_username(self, username: str, opportunity_id: int = None) -> User:
        """
        Sync a single user from Connect API to local database by username.

        Args:
            username: The username to sync
            opportunity_id: Optional opportunity ID to fetch user data from

        Returns:
            The synced User instance

        Raises:
            ValueError: If user not found in API
        """
        # Check if already exists locally
        existing = User.objects.filter(username=username).first()
        if existing:
            return existing

        # Fetch from API - need to get field workers for an opportunity
        if not opportunity_id:
            raise ValueError(f"Cannot sync user {username}: opportunity_id required to fetch from API")

        field_workers = self.facade.get_field_workers_by_opportunity(opportunity_id)
        user_data = next((fw for fw in field_workers if fw.username == username), None)

        if not user_data:
            raise ValueError(f"User {username} not found in Connect API for opportunity {opportunity_id}")

        # Create user (using username as unique identifier since ID may not be available)
        # Prepare defaults, only include email if it's actually provided (to avoid unique constraint on empty string)
        user_defaults = {
            "name": user_data.name or "",
            "phone_number": user_data.phone_number or "",
            "is_active": True,
        }
        if user_data.email:
            user_defaults["email"] = user_data.email

        user, created = User.objects.update_or_create(
            username=user_data.username,
            defaults=user_defaults,
        )

        return user

    def sync_users_by_username(self, usernames: list[str], opportunity_id: int) -> dict[str, User]:
        """
        Sync multiple users from Connect API to local database by username.

        Args:
            usernames: List of usernames to sync
            opportunity_id: Opportunity ID to fetch user data from

        Returns:
            Dictionary mapping username to User instance
        """
        users = {}

        # Fetch all field workers for the opportunity at once (more efficient)
        field_workers = self.facade.get_field_workers_by_opportunity(opportunity_id)
        field_workers_dict = {fw.username: fw for fw in field_workers}

        for username in usernames:
            try:
                # Check if already exists locally
                existing = User.objects.filter(username=username).first()
                if existing:
                    users[username] = existing
                    continue

                # Get from API data
                user_data = field_workers_dict.get(username)
                if not user_data:
                    print(f"[WARNING] User {username} not found in opportunity {opportunity_id}")
                    continue

                # Create user (only include email if provided to avoid unique constraint)
                user_defaults = {
                    "name": user_data.name or "",
                    "phone_number": user_data.phone_number or "",
                    "is_active": True,
                }
                if user_data.email:
                    user_defaults["email"] = user_data.email

                user, created = User.objects.update_or_create(
                    username=user_data.username,
                    defaults=user_defaults,
                )
                users[username] = user

            except Exception as e:
                print(f"[WARNING] Failed to sync user {username}: {e}")
                continue

        return users
