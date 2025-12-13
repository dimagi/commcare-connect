import os
from datetime import datetime

import yaml
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.utils.dateparse import parse_date

from commcare_connect.opportunity.models import DeliveryType
from commcare_connect.organization.models import Organization, UserOrganizationMembership
from commcare_connect.program.models import Program
from commcare_connect.solicitations.models import Solicitation, SolicitationQuestion

User = get_user_model()


class Command(BaseCommand):
    help = "Load solicitation data from YAML fixtures"

    def add_arguments(self, parser):
        parser.add_argument(
            "--file",
            type=str,
            default="sample_solicitations.yaml",
            help="YAML file name in solicitations/fixtures/ directory",
        )
        parser.add_argument(
            "--dry-run", action="store_true", help="Show what would be created without actually creating it"
        )
        parser.add_argument(
            "--update", action="store_true", help="Update existing solicitations if they already exist"
        )
        parser.add_argument("--cleanup", action="store_true", help="Clean up existing fixture data before loading")

    def get_fixtures_path(self, filename):
        """Get the full path to the fixtures file"""
        # Get the directory where this command file is located
        current_dir = os.path.dirname(os.path.abspath(__file__))
        # Go up to the solicitations app directory, then to fixtures
        fixtures_dir = os.path.join(os.path.dirname(os.path.dirname(current_dir)), "fixtures")
        return os.path.join(fixtures_dir, filename)

    def load_yaml_data(self, filepath):
        """Load and parse YAML data file"""
        try:
            with open(filepath, encoding="utf-8") as file:
                return yaml.safe_load(file)
        except FileNotFoundError:
            raise CommandError(f"Fixture file not found: {filepath}")
        except yaml.YAMLError as e:
            raise CommandError(f"Error parsing YAML file: {e}")

    def get_or_create_organization(self, org_slug):
        """Get or create organization for programs"""
        org, created = Organization.objects.get_or_create(
            slug=org_slug,
            defaults={
                "name": org_slug.replace("-", " ").title(),
                "program_manager": True,
            },
        )

        if created:
            self.stdout.write(f"Created organization: {org.name}")

            # Create a default admin user for this org
            user, user_created = User.objects.get_or_create(
                email=f"admin@{org_slug}.com",
                defaults={
                    "username": f"{org_slug}_admin",
                    "first_name": "Admin",
                    "last_name": "User",
                },
            )

            if user_created:
                UserOrganizationMembership.objects.get_or_create(
                    user=user,
                    organization=org,
                    defaults={
                        "role": UserOrganizationMembership.Role.ADMIN,
                        "accepted": True,
                    },
                )
                self.stdout.write(f"Created admin user: {user.email}")

        return org

    def create_programs(self, programs_data, dry_run=False):
        """Create programs from YAML data"""
        programs = {}

        # Get or create a default delivery type
        if not dry_run:
            delivery_type, created = DeliveryType.objects.get_or_create(
                name="Digital Health Services",
                defaults={
                    "name": "Digital Health Services",
                    "slug": "digital-health-services",
                    "description": "Digital health service delivery programs",
                },
            )
            if created:
                self.stdout.write(f"Created delivery type: {delivery_type.name}")

        for program_data in programs_data:
            org = self.get_or_create_organization(program_data["organization_slug"])

            if dry_run:
                self.stdout.write(f"[DRY RUN] Would create program: {program_data['name']}")
                programs[program_data["slug"]] = None
                continue

            program, created = Program.objects.get_or_create(
                slug=program_data["slug"],
                defaults={
                    "name": program_data["name"],
                    "description": program_data["description"],
                    "budget": program_data["budget"],
                    "currency": program_data["currency"],
                    "start_date": datetime.now().date(),
                    "end_date": datetime.now().date().replace(year=datetime.now().year + 1),
                    "organization": org,
                    "delivery_type": delivery_type,
                },
            )

            programs[program_data["slug"]] = program

            if created:
                self.stdout.write(f"Created program: {program.name}")
            else:
                self.stdout.write(f"Using existing program: {program.name}")

        return programs

    def create_solicitations(self, solicitations_data, programs, dry_run=False, update=False):
        """Create solicitations from YAML data"""
        created_count = 0
        updated_count = 0

        for sol_data in solicitations_data:
            program = programs.get(sol_data["program_slug"])
            if not program and not dry_run:
                self.stdout.write(self.style.WARNING(f"Program not found: {sol_data['program_slug']}"))
                continue

            if dry_run:
                self.stdout.write(f"[DRY RUN] Would create solicitation: {sol_data['title']}")
                continue

            # Check if solicitation already exists
            existing = Solicitation.objects.filter(title=sol_data["title"], program=program).first()

            if existing and not update:
                self.stdout.write(
                    self.style.WARNING(f"Solicitation already exists (use --update to overwrite): {sol_data['title']}")
                )
                continue

            # Get the admin user for this program's organization
            admin_user = program.organization.members.filter(
                memberships__role=UserOrganizationMembership.Role.ADMIN, memberships__accepted=True
            ).first()

            # Prepare solicitation data
            solicitation_fields = {
                "title": sol_data["title"],
                "description": sol_data["description"],
                "target_population": sol_data["target_population"],
                "scope_of_work": sol_data["scope_of_work"],
                "solicitation_type": sol_data["solicitation_type"],
                "status": sol_data["status"],
                "is_publicly_listed": sol_data["is_publicly_listed"],
                "program": program,
                "created_by": admin_user,
                "estimated_scale": sol_data["estimated_scale"],
                "expected_start_date": parse_date(sol_data["expected_start_date"]),
                "expected_end_date": parse_date(sol_data["expected_end_date"]),
                "application_deadline": parse_date(sol_data["application_deadline"]),
            }

            if existing:
                # Update existing solicitation
                for field, value in solicitation_fields.items():
                    if field != "program":  # Don't change program
                        setattr(existing, field, value)
                existing.save()
                solicitation = existing
                updated_count += 1
                self.stdout.write(f"Updated solicitation: {solicitation.title}")
            else:
                # Create new solicitation
                solicitation = Solicitation.objects.create(**solicitation_fields)
                created_count += 1
                self.stdout.write(f"Created solicitation: {solicitation.title}")

            # Handle questions
            if update or not existing:
                # Clear existing questions if updating
                if existing:
                    solicitation.questions.all().delete()

                # Create questions
                for question_data in sol_data.get("questions", []):
                    SolicitationQuestion.objects.create(
                        solicitation=solicitation,
                        question_text=question_data["text"],
                        question_type=question_data["type"],
                        is_required=question_data["required"],
                        order=question_data["order"],
                    )

        return created_count, updated_count

    def clean_fixture_data(self):
        """Clean up existing fixture data"""
        self.stdout.write("Cleaning up existing fixture data...")

        # Delete solicitations that look like fixture data (could be refined)
        deletions = [
            lambda: SolicitationQuestion.objects.filter(solicitation__title__icontains="sample").delete(),
            lambda: Solicitation.objects.filter(title__icontains="sample").delete(),
        ]

        for delete in deletions:
            delete()

        self.stdout.write("Cleanup complete.")

    def handle(self, *args, **options):
        filename = options["file"]
        dry_run = options["dry_run"]
        update = options["update"]
        cleanup = options["cleanup"]

        if cleanup:
            self.clean_fixture_data()

        # Load YAML data
        filepath = self.get_fixtures_path(filename)
        self.stdout.write(f"Loading data from: {filepath}")

        try:
            data = self.load_yaml_data(filepath)
        except CommandError as e:
            self.stdout.write(self.style.ERROR(str(e)))
            return

        if dry_run:
            self.stdout.write(self.style.WARNING("DRY RUN MODE - No data will be created"))

        # Create programs
        programs = self.create_programs(data.get("programs", []), dry_run)

        # Create solicitations
        created_count, updated_count = self.create_solicitations(
            data.get("solicitations", []), programs, dry_run, update
        )

        if not dry_run:
            self.stdout.write(
                self.style.SUCCESS(
                    f"Successfully loaded solicitation fixture data! "
                    f"Created: {created_count}, Updated: {updated_count}"
                )
            )

            # Print helpful URLs
            self.stdout.write("\n" + "=" * 50)
            self.stdout.write("🎉 Solicitation fixture data loaded! Check these URLs:")
            self.stdout.write("=" * 50)
            self.stdout.write("📋 Public solicitations: http://localhost:8000/solicitations/")
            self.stdout.write("🔍 EOIs only: http://localhost:8000/solicitations/eoi/")
            self.stdout.write("📄 RFPs only: http://localhost:8000/solicitations/rfp/")
            self.stdout.write("=" * 50)
        else:
            self.stdout.write("Dry run completed. Use without --dry-run to actually create the data.")
