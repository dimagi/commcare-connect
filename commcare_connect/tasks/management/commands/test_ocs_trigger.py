"""
Management command to test OCS trigger_bot API call.
"""
import time

from django.core.management.base import BaseCommand

from commcare_connect.tasks.ocs_client import get_recent_session, trigger_bot


class Command(BaseCommand):
    help = "Test OCS trigger_bot API call with sample data"

    def add_arguments(self, parser):
        parser.add_argument(
            "--identifier",
            type=str,
            default="test_user_123",
            help="Participant identifier (username/phone/UUID)",
        )
        parser.add_argument(
            "--bot-id",
            type=str,
            required=True,
            help="OCS Bot ID (experiment UUID)",
        )
        parser.add_argument(
            "--platform",
            type=str,
            default="commcare_connect",
            help="Platform name",
        )

    def handle(self, *args, **options):
        identifier = options["identifier"]
        bot_id = options["bot_id"]
        platform = options["platform"]

        self.stdout.write("Testing OCS trigger_bot with:")
        self.stdout.write(f"  Identifier: {identifier}")
        self.stdout.write(f"  Bot ID: {bot_id}")
        self.stdout.write(f"  Platform: {platform}")
        self.stdout.write("")

        try:
            result = trigger_bot(
                identifier=identifier,
                platform=platform,
                bot_id=bot_id,
                prompt_text="This is a test message from the management command.",
                start_new_session=True,
                session_data={"test": "data"},
            )

            self.stdout.write(self.style.SUCCESS("✓ Successfully triggered bot!"))
            self.stdout.write(f"Response: {result}")
            self.stdout.write("")

            # Wait briefly and try to fetch the session
            self.stdout.write("Waiting 1 second for session to be created...")
            time.sleep(1)

            self.stdout.write("Fetching recent session...")
            sessions = get_recent_session(experiment_id=bot_id, identifier=identifier, limit=1)

            if sessions:
                session = sessions[0]
                self.stdout.write(self.style.SUCCESS("✓ Found session!"))
                self.stdout.write(f"  Session ID: {session.get('id')}")
                self.stdout.write(f"  Created at: {session.get('created_at')}")
                self.stdout.write(f"  Participant: {session.get('participant', {}).get('identifier')}")
            else:
                self.stdout.write(self.style.WARNING("⚠ No session found yet (may still be processing)"))

        except Exception as e:
            self.stdout.write(self.style.ERROR(f"✗ Failed: {e}"))
            import traceback

            self.stdout.write(traceback.format_exc())
