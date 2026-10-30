from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from commcare_connect.simulation.fakes import local_fakes
from commcare_connect.simulation.reset import reset_orgs
from commcare_connect.simulation.scenarios import get_scenario, scenario_names
from commcare_connect.simulation.steps import Sim
from commcare_connect.users.models import User


class Command(BaseCommand):
    help = "Build a standard scenario's data by driving the app's real forms, functions and APIs."

    def add_arguments(self, parser):
        parser.add_argument("scenario", choices=scenario_names() or None, help="Scenario to build")
        parser.add_argument("--admin-email", help="Existing user to make admin of every organization it creates")
        parser.add_argument("--pm-email", help="Existing user for the program-manager org (overrides --admin-email)")
        parser.add_argument("--nm-email", help="Existing user for the network-manager org (overrides --admin-email)")
        parser.add_argument("--small", action="store_true", help="Build the fewest records that show every state")
        parser.add_argument("--seed", type=int, default=42, help="Random seed, so reruns build the same data")
        parser.add_argument("--keep", action="store_true", help="Don't delete the scenario's organizations first")
        parser.add_argument("--force", action="store_true", help="Run even when DEBUG is off")

    def handle(self, *args, **options):
        if not settings.DEBUG and not options["force"]:
            raise CommandError("run_scenario is for local development; pass --force to run with DEBUG off.")

        try:
            scenario = get_scenario(options["scenario"])
        except LookupError as e:
            raise CommandError(str(e))
        pm_admin = self._get_user(options["pm_email"] or options["admin_email"], "--pm-email/--admin-email")
        nm_admin = self._get_user(options["nm_email"] or options["admin_email"], "--nm-email/--admin-email")

        sim = Sim(pm_admin=pm_admin, nm_admin=nm_admin, seed=options["seed"], log=self.stdout.write)
        with local_fakes():
            if not options["keep"]:
                reset_orgs(scenario.ORG_NAMES)
            scenario.build(sim, small=options["small"])

        self.stdout.write(self.style.SUCCESS(f"Built scenario {options['scenario']!r}."))
        for label, url in sim.links:
            self.stdout.write(f"  {label}: {url}")

    def _get_user(self, email, flag):
        if not email:
            raise CommandError(f"Pass {flag} with the email of an existing local user.")
        user = User.objects.filter(email=email).first()
        if user is None:
            raise CommandError(f"No user with email {email!r}. Sign up locally first, then rerun.")
        return user
