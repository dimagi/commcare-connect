import yaml
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils.text import slugify

from commcare_connect.commcarehq.models import HQServer
from commcare_connect.opportunity.models import (
    CommCareApp,
    DeliverUnit,
    DeliveryType,
    Opportunity,
    OpportunityAccess,
    PaymentUnit,
)
from commcare_connect.organization.models import Organization

User = get_user_model()


class Command(BaseCommand):
    help = "Setup audit dependencies from YAML configuration"

    def add_arguments(self, parser):
        parser.add_argument(
            "--config",
            type=str,
            default="commcare_connect/audit/audit_dependencies.yaml",
            help="Path to YAML configuration file",
        )
        parser.add_argument(
            "--dry-run", action="store_true", help="Show what would be created without actually creating"
        )

    def handle(self, *args, **options):
        config_path = options["config"]
        dry_run = options["dry_run"]

        # Load YAML configuration
        try:
            with open(config_path) as f:
                config = yaml.safe_load(f)
        except FileNotFoundError:
            raise CommandError(f"Configuration file not found: {config_path}")
        except yaml.YAMLError as e:
            raise CommandError(f"Error parsing YAML file: {e}")

        if dry_run:
            self.stdout.write(self.style.WARNING("DRY RUN MODE - No data will be created"))

        self.stdout.write(self.style.SUCCESS("Setting up audit dependencies..."))

        try:
            with transaction.atomic():
                # Create all dependencies
                hq_server = self._create_hq_server(config["hq_server"], dry_run)
                organization = self._create_organization(config["organization"], dry_run)
                user = self._create_user(config["user"], dry_run)
                commcare_app = self._create_commcare_app(config["commcare_app"], organization, hq_server, dry_run)

                # Optional delivery type
                delivery_type = None
                if "delivery_type" in config:
                    delivery_type = self._create_delivery_type(config["delivery_type"], dry_run)

                opportunity = self._create_opportunity(
                    config["opportunity"], organization, commcare_app, delivery_type, dry_run
                )

                # Optional payment unit
                payment_unit = None
                if config.get("audit_config", {}).get("create_payment_unit", True):
                    payment_unit = self._create_payment_unit(config["payment_unit"], opportunity, dry_run)

                deliver_unit = self._create_deliver_unit(config["deliver_unit"], commcare_app, payment_unit, dry_run)

                # Optional opportunity access
                if config.get("audit_config", {}).get("create_opportunity_access", True):
                    self._create_opportunity_access(opportunity, user, dry_run)

                if not dry_run:
                    self.stdout.write(self.style.SUCCESS("Successfully created audit dependencies!"))
                    self.stdout.write(f"Organization: {organization}")
                    self.stdout.write(f"User: {user}")
                    self.stdout.write(f"Opportunity: {opportunity}")
                    self.stdout.write(f"Deliver Unit: {deliver_unit}")
                else:
                    self.stdout.write(self.style.SUCCESS("Dry run completed - no data was created"))

        except Exception as e:
            raise CommandError(f"Error creating dependencies: {e}")

    def _create_hq_server(self, config, dry_run):
        """Create or get HQ Server"""
        if dry_run:
            self.stdout.write(f'Would create HQServer: {config["name"]} ({config["url"]})')
            return None

        # Try to find an existing OAuth application or create a minimal one
        from oauth2_provider.models import Application

        oauth_app = Application.objects.first()
        if not oauth_app:
            # Create a minimal OAuth application for audit purposes
            oauth_app = Application.objects.create(
                name="Audit OAuth App",
                client_type=Application.CLIENT_CONFIDENTIAL,
                authorization_grant_type=Application.GRANT_AUTHORIZATION_CODE,
            )
            if not dry_run:
                self.stdout.write(f"Created minimal OAuth application: {oauth_app}")

        hq_server, created = HQServer.objects.get_or_create(
            url=config["url"], defaults={"name": config["name"], "oauth_application": oauth_app}
        )

        if created:
            self.stdout.write(f"Created HQServer: {hq_server}")
        else:
            self.stdout.write(f"Using existing HQServer: {hq_server}")

        return hq_server

    def _create_organization(self, config, dry_run):
        """Create or get Organization"""
        if dry_run:
            self.stdout.write(f'Would create Organization: {config["name"]}')
            return None

        # Generate slug if not provided
        slug = config.get("slug")
        if not slug:
            slug = slugify(config["name"])

        organization, created = Organization.objects.get_or_create(
            slug=slug, defaults={"name": config["name"], "program_manager": config.get("program_manager", False)}
        )

        if created:
            self.stdout.write(f"Created Organization: {organization}")
        else:
            self.stdout.write(f"Using existing Organization: {organization}")

        return organization

    def _create_user(self, config, dry_run):
        """Create or get User"""
        if dry_run:
            self.stdout.write(f'Would create User: {config["username"]}')
            return None

        user, created = User.objects.get_or_create(
            username=config["username"],
            defaults={
                "email": config.get("email"),
                "name": config.get("name", config["username"]),
            },
        )

        if created:
            # Set a default password for audit user
            user.set_password("audit123")
            user.save()
            self.stdout.write(f"Created User: {user}")
        else:
            self.stdout.write(f"Using existing User: {user}")

        return user

    def _create_commcare_app(self, config, organization, hq_server, dry_run):
        """Create or get CommCare App"""
        if dry_run:
            self.stdout.write(f'Would create CommCareApp: {config["name"]}')
            return None

        app, created = CommCareApp.objects.get_or_create(
            cc_app_id=config["cc_app_id"],
            cc_domain=config["cc_domain"],
            defaults={
                "name": config["name"],
                "description": config["description"],
                "organization": organization,
                "passing_score": config.get("passing_score", 80),
                "hq_server": hq_server,
            },
        )

        if created:
            self.stdout.write(f"Created CommCareApp: {app}")
        else:
            self.stdout.write(f"Using existing CommCareApp: {app}")

        return app

    def _create_delivery_type(self, config, dry_run):
        """Create or get Delivery Type"""
        if dry_run:
            self.stdout.write(f'Would create DeliveryType: {config["name"]}')
            return None

        delivery_type, created = DeliveryType.objects.get_or_create(name=config["name"])

        if created:
            self.stdout.write(f"Created DeliveryType: {delivery_type}")
        else:
            self.stdout.write(f"Using existing DeliveryType: {delivery_type}")

        return delivery_type

    def _create_opportunity(self, config, organization, commcare_app, delivery_type, dry_run):
        """Create or get Opportunity"""
        if dry_run:
            self.stdout.write(f'Would create Opportunity: {config["name"]}')
            return None

        from datetime import datetime

        opportunity, created = Opportunity.objects.get_or_create(
            name=config["name"],
            organization=organization,
            defaults={
                "description": config["description"],
                "short_description": config.get("short_description"),
                "active": config.get("active", True),
                "deliver_app": commcare_app,
                "learn_app": commcare_app,  # Use same app for both
                "start_date": datetime.strptime(config["start_date"], "%Y-%m-%d").date(),
                "end_date": datetime.strptime(config["end_date"], "%Y-%m-%d").date(),
                "total_budget": config.get("total_budget"),
                "currency": config.get("currency"),
                "auto_approve_visits": config.get("auto_approve_visits", True),
                "auto_approve_payments": config.get("auto_approve_payments", True),
                "is_test": config.get("is_test", True),
                "managed": config.get("managed", False),
                "delivery_type": delivery_type,
                "hq_server": commcare_app.hq_server,
            },
        )

        if created:
            self.stdout.write(f"Created Opportunity: {opportunity}")
        else:
            self.stdout.write(f"Using existing Opportunity: {opportunity}")

        return opportunity

    def _create_payment_unit(self, config, opportunity, dry_run):
        """Create or get Payment Unit"""
        if dry_run:
            self.stdout.write(f'Would create PaymentUnit: {config["name"]}')
            return None

        from datetime import datetime

        payment_unit, created = PaymentUnit.objects.get_or_create(
            name=config["name"],
            opportunity=opportunity,
            defaults={
                "amount": config["amount"],
                "max_total": config["max_total"],
                "max_daily": config["max_daily"],
                "start_date": datetime.strptime(config["start_date"], "%Y-%m-%d").date(),
                "end_date": datetime.strptime(config["end_date"], "%Y-%m-%d").date(),
            },
        )

        if created:
            self.stdout.write(f"Created PaymentUnit: {payment_unit}")
        else:
            self.stdout.write(f"Using existing PaymentUnit: {payment_unit}")

        return payment_unit

    def _create_deliver_unit(self, config, commcare_app, payment_unit, dry_run):
        """Create or get Deliver Unit"""
        if dry_run:
            self.stdout.write(f'Would create DeliverUnit: {config["name"]}')
            return None

        deliver_unit, created = DeliverUnit.objects.get_or_create(
            slug=config["slug"],
            app=commcare_app,
            defaults={
                "name": config["name"],
                "payment_unit": payment_unit,
                "optional": config.get("optional", False),
            },
        )

        if created:
            self.stdout.write(f"Created DeliverUnit: {deliver_unit}")
        else:
            self.stdout.write(f"Using existing DeliverUnit: {deliver_unit}")

        return deliver_unit

    def _create_opportunity_access(self, opportunity, user, dry_run):
        """Create or get Opportunity Access"""
        if dry_run:
            self.stdout.write(f"Would create OpportunityAccess for {user} -> {opportunity}")
            return None

        access, created = OpportunityAccess.objects.get_or_create(
            opportunity=opportunity,
            user=user,
            defaults={
                "accepted": True,
            },
        )

        if created:
            self.stdout.write(f"Created OpportunityAccess: {access}")
        else:
            self.stdout.write(f"Using existing OpportunityAccess: {access}")

        return access
