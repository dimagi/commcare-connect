"""Refresh Connect's cached worker names from PersonalID.

PersonalID owns the worker profile; Connect keeps a copy in ``User.name`` so names can be
rendered, sorted and exported without a remote call. Historically that copy was only written
when a worker was invited to an opportunity, so a worker who renamed themselves on mobile kept
their old name on Connect web until their next invite.

This is a one-shot repair for names that drifted before the push from PersonalID existed (see
``UpdateUserProfileView``), not a scheduled job. With the push in place there is nothing for a
recurring run to find; rerun it manually if pushes are ever known to have been lost.

Flow:
1. Collect users who have at least one OpportunityAccess (only those appear on Connect web).
2. Skip any without a phone number, since PersonalID's fetch_users is keyed by phone number.
3. Look names up in batches (--batch-size, default 100) and save the ones that differ with a
   single bulk update per batch.
"""

from django.core.management.base import BaseCommand, CommandError

from commcare_connect.connect_id_client import fetch_users
from commcare_connect.users.helpers import NAME_MAX_LENGTH
from commcare_connect.users.models import User
from commcare_connect.utils.itertools import batched

DEFAULT_BATCH_SIZE = 100
# --batch-size sizes the PersonalID lookup; cap the UPDATE separately so a large lookup batch
# doesn't turn into one enormous CASE WHEN statement.
UPDATE_BATCH_SIZE = 500


class Command(BaseCommand):
    help = "Refresh Connect's cached copy of worker names from PersonalID."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Report what would change, without saving.",
        )
        parser.add_argument(
            "--batch-size",
            type=int,
            default=DEFAULT_BATCH_SIZE,
            help=f"Number of phone numbers to look up per PersonalID call (default {DEFAULT_BATCH_SIZE}).",
        )

    def handle(self, *args, **options):
        batch_size = options["batch_size"]
        if batch_size < 1:
            raise CommandError("--batch-size must be at least 1.")

        dry_run = options["dry_run"]
        users = User.objects.filter(opportunityaccess__isnull=False).distinct()
        names_by_phone = {}
        without_phone = 0
        for user in users:
            if user.phone_number:
                names_by_phone.setdefault(user.phone_number, []).append(user)
            else:
                without_phone += 1

        if not names_by_phone:
            self.stdout.write("No workers with a phone number to refresh.")
            return

        phone_numbers = sorted(names_by_phone)
        totals = {"checked": len(phone_numbers), "updated": 0, "unchanged": 0, "skipped": 0, "not_found": 0}
        for batch in batched(phone_numbers, batch_size):
            self._refresh_batch(batch, names_by_phone, totals, dry_run)

        self._report(totals, without_phone, dry_run)

    def _refresh_batch(self, batch, users_by_phone, totals, dry_run):
        try:
            found_users = fetch_users(batch)
        except Exception as e:
            # One bad batch must not abort the run; a rerun picks up whatever was missed.
            self.stderr.write(f"Lookup failed for {len(batch)} phone number(s), skipping batch: {e}")
            totals["skipped"] += len(batch)
            return

        found_phone_numbers = set()
        to_update = []
        for connectid_user in found_users:
            found_phone_numbers.add(connectid_user.phone_number)
            for user in users_by_phone.get(connectid_user.phone_number, []):
                if self._stage_update(user, connectid_user.name, totals, dry_run):
                    to_update.append(user)
        totals["not_found"] += len(set(batch) - found_phone_numbers)

        if to_update:
            User.objects.bulk_update(to_update, ["name"], batch_size=UPDATE_BATCH_SIZE)

    def _stage_update(self, user, new_name, totals, dry_run):
        """Set ``user.name`` in memory, returning whether it needs saving."""
        new_name = (new_name or "").strip()
        if not new_name:
            # ConnectID allows a blank name; leave the existing local one alone.
            self.stderr.write(f"Skipping {user.username}: name is blank in ConnectID")
            totals["skipped"] += 1
            return False
        if len(new_name) > NAME_MAX_LENGTH:
            # ConnectID stores the name in a TextField, so it can hold more than this column accepts.
            self.stderr.write(f"Skipping {user.username}: name exceeds {NAME_MAX_LENGTH} characters")
            totals["skipped"] += 1
            return False
        if user.name == new_name:
            totals["unchanged"] += 1
            return False

        totals["updated"] += 1
        if dry_run:
            self.stdout.write(f"{user.username}: {user.name!r} -> {new_name!r}")
            return False
        user.name = new_name
        return True

    def _report(self, totals, without_phone, dry_run):
        verb = "would be updated" if dry_run else "updated"
        self.stdout.write(
            f"{totals['checked']} phone number(s) checked, {totals['updated']} name(s) {verb}, "
            f"{totals['unchanged']} already current, {totals['skipped']} skipped, "
            f"{totals['not_found']} not found in ConnectID, "
            f"{without_phone} worker(s) had no phone number."
        )
