import os
from datetime import date, timedelta
from decimal import Decimal

import yaml
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand

from commcare_connect.organization.models import Organization, UserOrganizationMembership
from commcare_connect.program.models import DeliveryType, Program
from commcare_connect.solicitations.models import (
    Solicitation,
    SolicitationQuestion,
    SolicitationResponse,
    SolicitationReview,
)

User = get_user_model()


class Command(BaseCommand):
    help = "Load realistic UAT data from YAML fixture file"

    def add_arguments(self, parser):
        parser.add_argument("--clear", action="store_true", help="Clear all data first (preserves users)")
        parser.add_argument("--file", type=str, default="sample_solicitations.yaml", help="YAML fixture file name")
        parser.add_argument("--with-responses", action="store_true", help="Create sample responses and reviews")
        parser.add_argument(
            "--existing-user",
            type=str,
            required=True,
            help="Username of existing user to associate with some responses (e.g., jjackson-dev@dimagi.com)",
        )

    def load_yaml_data(self, filename):
        """Load YAML fixture data"""
        fixture_path = os.path.join(settings.BASE_DIR, "commcare_connect", "solicitations", "fixtures", filename)

        with open(fixture_path, encoding="utf-8") as file:
            return yaml.safe_load(file)

    def create_delivery_types(self, data):
        """Create delivery types from YAML data"""
        delivery_types = {}

        for dt_data in data.get("delivery_types", []):
            dt_name = dt_data["name"]  # Extract name from dict
            dt, created = DeliveryType.objects.get_or_create(name=dt_name)
            if created:
                self.stdout.write(f"Created delivery type: {dt.name}")
            delivery_types[dt.name] = dt

        return delivery_types

    def create_organizations_and_users(self, data):
        """Create organizations and users from YAML data"""
        organizations = {}

        # Create program organizations
        for org_data in data.get("program_organizations", []):
            org, created = Organization.objects.get_or_create(
                name=org_data["name"], defaults={"program_manager": org_data.get("program_manager", True)}
            )

            if created:
                self.stdout.write(f"Created program organization: {org.name} (slug: {org.slug})")

                # Create manager user
                user, user_created = User.objects.get_or_create(
                    email=org_data["manager_email"],
                    defaults={"username": org.slug + "_manager", "name": org_data["manager_name"]},
                )

                if user_created:
                    user.set_password("testpass123")
                    user.save()
                    self.stdout.write(f"Created user: {user.email}")

                # Create membership
                UserOrganizationMembership.objects.get_or_create(
                    user=user,
                    organization=org,
                    defaults={"role": UserOrganizationMembership.Role.ADMIN, "accepted": True},
                )

            organizations[org_data["slug"]] = org

        # Create implementing organizations
        for org_data in data.get("implementing_organizations", []):
            org, created = Organization.objects.get_or_create(
                name=org_data["name"], defaults={"program_manager": org_data.get("program_manager", False)}
            )

            if created:
                self.stdout.write(
                    f"Created implementing organization: {org.name} ({org_data['region']}) (slug: {org.slug})"
                )

                # Create contact user
                user, user_created = User.objects.get_or_create(
                    email=org_data["contact_email"],
                    defaults={"username": org.slug + "_contact", "name": org_data["contact_name"]},
                )

                if user_created:
                    user.set_password("testpass123")
                    user.save()
                    self.stdout.write(f"Created user: {user.email}")

                # Create membership
                UserOrganizationMembership.objects.get_or_create(
                    user=user,
                    organization=org,
                    defaults={"role": UserOrganizationMembership.Role.MEMBER, "accepted": True},
                )

            organizations[org_data["slug"]] = org

        return organizations

    def create_programs(self, data, organizations, delivery_types):
        """Create programs from YAML data"""
        programs = {}

        for prog_data in data.get("programs", []):
            org = organizations[prog_data["organization_slug"]]
            delivery_type = delivery_types[prog_data["delivery_type"]]

            program, created = Program.objects.get_or_create(
                slug=prog_data["slug"],
                defaults={
                    "name": prog_data["name"],
                    "organization": org,
                    "delivery_type": delivery_type,
                    "budget": Decimal(prog_data["budget"]),
                    "start_date": date.today(),
                    "end_date": date.today() + timedelta(days=730),
                },
            )

            if created:
                self.stdout.write(f"Created program: {program.name}")

            programs[prog_data["slug"]] = program

        return programs

    def create_solicitations(self, data, programs):
        """Create solicitations from YAML data"""
        solicitations = []

        for sol_data in data.get("solicitations", []):
            program = programs[sol_data["program_slug"]]

            solicitation, created = Solicitation.objects.get_or_create(
                title=sol_data["title"],
                defaults={
                    "program": program,
                    "description": sol_data["description"],
                    "target_population": sol_data.get("target_population", "General population"),
                    "estimated_scale": sol_data.get("estimated_scale", "Regional"),
                    "status": sol_data.get("status", "active"),
                    "solicitation_type": sol_data.get("solicitation_type", "eoi"),
                    "application_deadline": date.today() + timedelta(days=30),
                },
            )

            if created:
                # Create questions
                for order, q_data in enumerate(sol_data.get("questions", []), 1):
                    SolicitationQuestion.objects.create(
                        solicitation=solicitation,
                        question_text=q_data["text"],
                        question_type=q_data.get("type", "text"),
                        is_required=q_data.get("required", True),
                        order=order,
                    )

                self.stdout.write(f"Created solicitation: {solicitation.title}")

            solicitations.append(solicitation)

        return solicitations

    def create_sample_responses_and_reviews(self, solicitations, organizations, existing_user=None):
        """Create sample responses and reviews with specific requirements:
        - Active solicitations: 5 responses each, 2 reviewed
        - Closed solicitations: 20 responses for EOI, 10 for RFP, all reviewed
        """
        import random

        from faker import Faker

        fake = Faker()

        responses = []
        reviews = []

        # Get existing user's organizations if user exists
        existing_user_orgs = []
        if existing_user:
            existing_user_orgs = [membership.organization for membership in existing_user.memberships.all()]

        # Get implementing organizations
        implementing_orgs = [org for org in organizations.values() if not org.program_manager]

        for solicitation in solicitations:
            # Determine number of responses based on status and type
            if solicitation.status == "active":
                num_responses = 5
                num_reviews = 2
            elif solicitation.status == "closed":
                if solicitation.solicitation_type == "eoi":
                    num_responses = 20
                else:  # rfp
                    num_responses = 10
                num_reviews = num_responses  # All closed solicitation responses are reviewed
            else:
                # Skip draft solicitations
                continue

            # Select respondent organizations
            respondent_orgs = random.sample(implementing_orgs, k=min(num_responses, len(implementing_orgs)))

            # For active solicitations, ensure existing user participates in half of them
            if solicitation.status == "active" and existing_user_orgs and random.choice([True, False]):
                # Replace one random org with existing user's org
                if existing_user_orgs[0] not in respondent_orgs and len(respondent_orgs) > 0:
                    respondent_orgs[0] = existing_user_orgs[0]

            solicitation_responses = []
            for i, org in enumerate(respondent_orgs):
                # Use existing user if this is one of their orgs
                if existing_user and org in existing_user_orgs:
                    user = existing_user
                else:
                    user = org.members.first()

                if not user:
                    continue

                # Generate responses
                question_responses = {}
                for question in solicitation.questions.all():
                    if question.question_type == "file":
                        question_responses[f"question_{question.id}"] = f"document_{org.slug}_{question.id}.pdf"
                    elif question.question_type == "number":
                        question_responses[f"question_{question.id}"] = str(random.randint(5, 50))
                    else:
                        question_responses[f"question_{question.id}"] = fake.text(max_nb_chars=300)

                # Create response
                response = SolicitationResponse.objects.create(
                    solicitation=solicitation,
                    organization=org,
                    submitted_by=user,
                    responses=question_responses,
                    status="submitted",
                )
                responses.append(response)
                solicitation_responses.append(response)

            # Create reviews
            reviewer = solicitation.program.organization.members.filter(
                memberships__role=UserOrganizationMembership.Role.ADMIN
            ).first()

            if reviewer:
                # For active solicitations: review only the first num_reviews responses
                # For closed solicitations: review all responses
                responses_to_review = (
                    solicitation_responses[:num_reviews] if solicitation.status == "active" else solicitation_responses
                )

                for response in responses_to_review:
                    score = random.randint(65, 95)
                    recommendation = (
                        "recommended" if score >= 80 else random.choice(["recommended", "neutral", "not_recommended"])
                    )

                    review = SolicitationReview.objects.create(
                        response=response,
                        reviewer=reviewer,
                        score=score,
                        recommendation=recommendation,
                        notes=f"Review of {response.organization.name}'s response to {solicitation.title}. "
                        + fake.text(max_nb_chars=200),
                    )
                    reviews.append(review)

        self.stdout.write(f"Created {len(responses)} responses and {len(reviews)} reviews")
        if existing_user:
            user_responses = [r for r in responses if r.submitted_by == existing_user]
            self.stdout.write(f"  • {len(user_responses)} responses from {existing_user.email}")

        # Summary by solicitation status
        active_responses = [r for r in responses if r.solicitation.status == "active"]
        closed_responses = [r for r in responses if r.solicitation.status == "closed"]
        active_reviews = [r for r in reviews if r.response.solicitation.status == "active"]
        closed_reviews = [r for r in reviews if r.response.solicitation.status == "closed"]

        self.stdout.write(
            f"  • Active solicitations: {len(active_responses)} responses, {len(active_reviews)} reviews"
        )
        self.stdout.write(
            f"  • Closed solicitations: {len(closed_responses)} responses, {len(closed_reviews)} reviews"
        )

        return responses, reviews

    def clear_data(self):
        """Clear all data except users"""
        self.stdout.write("Clearing all data (preserving users)...")

        SolicitationReview.objects.all().delete()
        SolicitationResponse.objects.all().delete()
        SolicitationQuestion.objects.all().delete()
        Solicitation.objects.all().delete()
        Program.objects.all().delete()
        UserOrganizationMembership.objects.all().delete()

        # Keep essential orgs like jj-test-org
        keep_orgs = ["jj-test-org"]
        Organization.objects.exclude(slug__in=keep_orgs).delete()
        DeliveryType.objects.all().delete()

        self.stdout.write("Data cleared (users preserved).")

    def associate_existing_user(self, organizations, existing_user, programs):
        """Associate existing user with program manager organizations that own half the solicitations"""
        # Get program manager organizations
        program_manager_orgs = [org for org in organizations.values() if org.program_manager]

        # Associate user with half of the program manager organizations to ensure they own half the solicitations
        num_orgs_to_join = max(1, len(program_manager_orgs) // 2)
        orgs_to_join = program_manager_orgs[:num_orgs_to_join]

        for org in orgs_to_join:
            membership, created = UserOrganizationMembership.objects.get_or_create(
                user=existing_user,
                organization=org,
                defaults={"role": UserOrganizationMembership.Role.ADMIN, "accepted": True},
            )
            if created:
                self.stdout.write(f"Associated {existing_user.email} with program manager org: {org.name}")

        # Also associate with a couple implementing organizations for responses
        implementing_orgs = [org for org in organizations.values() if not org.program_manager]
        implementing_orgs_to_join = implementing_orgs[:2]

        for org in implementing_orgs_to_join:
            membership, created = UserOrganizationMembership.objects.get_or_create(
                user=existing_user,
                organization=org,
                defaults={"role": UserOrganizationMembership.Role.MEMBER, "accepted": True},
            )
            if created:
                self.stdout.write(f"Associated {existing_user.email} with implementing org: {org.name}")

    def handle(self, *args, **options):
        clear_data = options["clear"]
        filename = options["file"]
        with_responses = options["with_responses"]
        existing_username = options["existing_user"]

        if clear_data:
            self.clear_data()

        # Find existing user if specified
        existing_user = None
        if existing_username:
            try:
                # Try to find by username first, then by email
                existing_user = User.objects.filter(username=existing_username).first()
                if not existing_user:
                    existing_user = User.objects.filter(email=existing_username).first()

                if existing_user:
                    self.stdout.write(f"Found existing user: {existing_user.email}")
                else:
                    self.stdout.write(
                        self.style.ERROR(
                            f"Error: User '{existing_username}' not found. "
                            f"Please check the username/email. Common mistake: "
                            f"use 'jjackson-dev@dimagi.com' not 'jjackson-dev'"
                        )
                    )
                    return
            except Exception as e:
                self.stdout.write(self.style.ERROR(f"Error finding user {existing_username}: {e}"))
                return

        self.stdout.write(f"Loading UAT data from {filename}...")

        try:
            # Load YAML data
            data = self.load_yaml_data(filename)

            # Create data in dependency order
            delivery_types = self.create_delivery_types(data)
            organizations = self.create_organizations_and_users(data)
            programs = self.create_programs(data, organizations, delivery_types)
            solicitations = self.create_solicitations(data, programs)

            # Associate existing user with organizations if specified
            if existing_user:
                self.associate_existing_user(organizations, existing_user, programs)

            # Create responses and reviews if requested
            responses = []
            reviews = []
            if with_responses:
                responses, reviews = self.create_sample_responses_and_reviews(
                    solicitations, organizations, existing_user
                )

            self.stdout.write(
                self.style.SUCCESS(
                    f"Successfully loaded UAT data from {filename}:\n"
                    f"  • {len(delivery_types)} delivery types\n"
                    f"  • {len(organizations)} organizations\n"
                    f"  • {len(programs)} programs\n"
                    f"  • {len(solicitations)} solicitations"
                    + (f"\n  • {len(responses)} responses\n  • {len(reviews)} reviews" if with_responses else "")
                )
            )

        except Exception as e:
            self.stdout.write(self.style.ERROR(f"Error loading data: {e}"))
            raise
