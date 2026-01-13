"""
Management command to clear all tasks data from the database.

This command removes all Task, TaskEvent, TaskComment, and TaskAISession records
from the database, providing a clean slate for testing or resetting the system.
"""

from django.core.management.base import BaseCommand

from commcare_connect.tasks.database_manager import reset_tasks_database
from commcare_connect.tasks.models import Task


class Command(BaseCommand):
    help = "Clear all tasks data from the database"

    def add_arguments(self, parser):
        parser.add_argument(
            "--yes",
            action="store_true",
            help="Skip confirmation prompt",
        )

    def handle(self, *args, **options):
        # Get counts before deletion
        task_count = Task.objects.count()

        if task_count == 0:
            self.stdout.write(self.style.SUCCESS("No tasks found. Database is already clean."))
            return

        # Get detailed counts from database manager
        from commcare_connect.tasks.database_manager import get_database_stats

        stats = get_database_stats()

        # Show what will be deleted
        self.stdout.write("\nThe following records will be deleted:")
        self.stdout.write(f"  - {stats['tasks']} task(s)")
        self.stdout.write(f"  - {stats['events']} task event(s)")
        self.stdout.write(f"  - {stats['comments']} task comment(s)")
        self.stdout.write(f"  - {stats['ai_sessions']} AI session(s)")

        # Confirm deletion unless --yes flag is provided
        if not options["yes"]:
            self.stdout.write("\n" + self.style.WARNING("This action cannot be undone!"))
            confirm = input("\nAre you sure you want to delete all tasks data? (yes/no): ")
            if confirm.lower() != "yes":
                self.stdout.write(self.style.ERROR("Aborted. No data was deleted."))
                return

        # Use database manager to reset
        self.stdout.write("\nDeleting tasks data...")
        deleted = reset_tasks_database()

        self.stdout.write(f"  > Deleted {deleted['comments']} comment(s)")
        self.stdout.write(f"  > Deleted {deleted['ai_sessions']} AI session(s)")
        self.stdout.write(f"  > Deleted {deleted['events']} event(s)")
        self.stdout.write(f"  > Deleted {deleted['tasks']} task(s)")

        self.stdout.write("\n" + self.style.SUCCESS("> All tasks data successfully cleared!"))
