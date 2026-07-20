"""Backfill ConnectIDUserLink.hq_user_uuid from the CommCare HQ user API.

Lookups are grouped by (hq_server, domain). ``opportunity.api_key`` is an opportunity-level
HQ admin credential (its ``user`` is the API account, not the mobile worker), so a single
call to ``/a/{domain}/api/v0.5/user/`` lists every mobile worker in that domain. This means
one API call per (hq_server, domain), regardless of how many users or opportunities are
involved, and a user enrolled in several opportunities sharing a domain is resolved once.

Flow:
1. Find links missing hq_user_uuid (with a domain + hq_server); group by (hq_server, domain).
2. Map each group to an opportunity admin key.
3. Confirm before running lookups (the failure-prone step).
4. Resolve UUIDs (one sweep per domain) and write a CSV reference of users to be updated,
   then confirm again with the exact count before saving.
5. Save in batches (--batch-size, default 100) with a retry, so a partial run is resumable:
   already-saved rows no longer match the "missing" filter on a rerun.
"""

import csv
import tempfile
from collections import defaultdict
from pathlib import Path

from django.core.management.base import BaseCommand
from django.db.models import Q
from django.utils import timezone

from commcare_connect.opportunity.models import Opportunity
from commcare_connect.users.helpers import fetch_hq_user_uuids
from commcare_connect.users.models import ConnectIDUserLink
from commcare_connect.utils.commcarehq_api import CommCareHQAPIException

REFERENCE_HEADER = ["user_id", "connectid_user_link_id", "commcare_username", "domain", "hq_user_uuid"]
SAVE_ATTEMPTS = 3


class Command(BaseCommand):
    help = "Backfill missing hq_user_uuid values on ConnectIDUserLink records via the CommCare HQ API."

    def add_arguments(self, parser):
        parser.add_argument(
            "--batch-size", type=int, default=100, help="Save resolved UUIDs in batches of this size (default 100)."
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Look up UUIDs and report what would change, without saving.",
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        links_by_server_and_domain = self._links_missing_uuid_by_server_and_domain()
        if not links_by_server_and_domain:
            self.stdout.write("No ConnectIDUserLink records are missing hq_user_uuid.")
            return

        api_keys = self._api_keys_by_server_and_domain()
        resolvable = {
            server_and_domain: links
            for server_and_domain, links in links_by_server_and_domain.items()
            if server_and_domain in api_keys
        }

        if not self._confirm_lookups(links_by_server_and_domain, resolvable):
            self.stdout.write("Aborted.")
            return

        to_be_updated, not_found, errors = self._resolve_uuids(resolvable, api_keys)
        no_key = sum(len(links) for links in links_by_server_and_domain.values()) - sum(
            len(links) for links in resolvable.values()
        )
        reference_path = self._save(to_be_updated, options["batch_size"], dry_run)
        self._report(not_found, no_key, errors)
        if reference_path:
            self.stdout.write(f"Reference CSV written to {reference_path}")

    def _links_missing_uuid_by_server_and_domain(self):
        links = (
            ConnectIDUserLink.objects.filter(
                Q(hq_user_uuid__isnull=True) | Q(hq_user_uuid=""),
                hq_server__isnull=False,
            )
            .exclude(domain__isnull=True)
            .exclude(domain="")
        )
        links_by_server_and_domain = defaultdict(list)
        for link in links:
            links_by_server_and_domain[(link.hq_server_id, link.domain)].append(link)
        return links_by_server_and_domain

    def _api_keys_by_server_and_domain(self):
        """Map each (hq_server, domain) to an opportunity API key that can list that domain's users.

        The API key is an opportunity-level admin credential (not the mobile worker), so any
        opportunity on the same server and domain provides a key that lists all its users.
        """
        opportunities = Opportunity.objects.filter(api_key__isnull=False).select_related(
            "api_key", "api_key__user", "api_key__hq_server", "deliver_app", "learn_app"
        )
        api_keys = {}
        for opportunity in opportunities:
            api_key = opportunity.api_key
            domain = self._opportunity_domain(opportunity)
            if api_key.hq_server_id is None or domain is None:
                continue
            api_keys.setdefault((api_key.hq_server_id, domain), api_key)
        return api_keys

    def _opportunity_domain(self, opportunity):
        for app in (opportunity.deliver_app, opportunity.learn_app):
            if app and app.cc_domain:
                return app.cc_domain
        return None

    def _confirm_lookups(self, links_by_server_and_domain, resolvable):
        total = sum(len(links) for links in links_by_server_and_domain.values())
        unique_users = len({link.user_id for links in links_by_server_and_domain.values() for link in links})
        covered = sum(len(links) for links in resolvable.values())
        self.stdout.write(f"Found {total} ConnectIDUserLink records missing hq_user_uuid across {unique_users} users.")
        self.stdout.write(
            f"{covered} are covered by {len(resolvable)} domain(s) (one API call each); "
            f"{total - covered} will be skipped (no API key)."
        )
        return self._confirm("Proceed with CommCare HQ lookups?")

    def _confirm(self, prompt):
        return input(f"{prompt} [y/N]: ").strip().lower() in ("y", "yes")

    def _resolve_uuids(self, resolvable, api_keys):
        to_be_updated = []
        not_found = errors = 0
        for server_and_domain, links in resolvable.items():
            _, domain = server_and_domain
            try:
                uuids_by_username = fetch_hq_user_uuids(api_keys[server_and_domain], domain)
            except CommCareHQAPIException as e:
                errors += 1
                self.stderr.write(self.style.WARNING(f"Lookup failed for domain {domain}: {e}"))
                continue
            for link in links:
                hq_user_uuid = uuids_by_username.get(link.commcare_username)
                if hq_user_uuid:
                    link.hq_user_uuid = hq_user_uuid
                    to_be_updated.append(link)
                else:
                    not_found += 1
        return to_be_updated, not_found, errors

    def _save(self, to_be_updated, batch_size, dry_run):
        if not to_be_updated:
            self.stdout.write("No UUIDs resolved; nothing to update.")
            return None

        path = self._write_reference_file(to_be_updated)
        self.stdout.write(f"Wrote {len(to_be_updated)} user references for review.")

        if dry_run:
            self.stdout.write(self.style.SUCCESS(f"Dry run: would update {len(to_be_updated)} records."))
            return path

        if not self._confirm(f"Update {len(to_be_updated)} records now?"):
            self.stdout.write("Aborted before saving.")
            return path

        total = len(to_be_updated)
        for start in range(0, total, batch_size):
            batch = to_be_updated[start : start + batch_size]  # noqa: E203
            self._bulk_update_with_retry(batch)
            self.stdout.write(self.style.SUCCESS(f"Updated {min(start + batch_size, total)}/{total} records."))
        return path

    def _write_reference_file(self, to_be_updated):
        timestamp = timezone.now().strftime("%Y%m%d_%H%M%S")
        path = Path(tempfile.gettempdir()) / f"hq_user_uuid_backfill_{timestamp}.csv"
        with path.open("w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(REFERENCE_HEADER)
            for link in to_be_updated:
                writer.writerow([link.user_id, link.id, link.commcare_username, link.domain, link.hq_user_uuid])
        return path

    def _bulk_update_with_retry(self, batch):
        for attempt in range(1, SAVE_ATTEMPTS + 1):
            try:
                ConnectIDUserLink.objects.bulk_update(batch, ["hq_user_uuid"])
                return
            except Exception as e:
                if attempt == SAVE_ATTEMPTS:
                    raise
                self.stderr.write(
                    self.style.WARNING(f"Save failed (attempt {attempt}/{SAVE_ATTEMPTS}), retrying: {e}")
                )

    def _report(self, not_found, no_key, errors):
        """Report lookup outcomes; the save outcome is reported by _save itself."""
        if not_found:
            self.stdout.write(f"{not_found} users not found on CommCare HQ.")
        if no_key:
            self.stdout.write(f"{no_key} skipped: no matching opportunity API key.")
        if errors:
            self.stdout.write(self.style.WARNING(f"{errors} domain lookups failed with API errors."))
