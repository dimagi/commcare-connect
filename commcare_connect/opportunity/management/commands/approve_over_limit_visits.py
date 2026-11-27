from django.core.management import BaseCommand, CommandError
from django.db import transaction
from django.http import Http404

from commcare_connect.opportunity.models import Opportunity, UserVisit, VisitValidationStatus
from commcare_connect.opportunity.utils.visit_approval import approvable_visits, approve_user_visits
from commcare_connect.utils.db import get_object_by_uuid_or_int

# Saved on every flagged visit this approves. Edit it to describe the case before running the command.
JUSTIFICATION = (
    "Marked over limit before the worker's visit limit was raised. Checked against the submission and approved."
)
JUSTIFICATION_MAX_LENGTH = UserVisit._meta.get_field("justification").max_length


class Command(BaseCommand):
    help = (
        "Approve an opportunity's over-limit visits as a Network Manager would, recalculating payment accrual "
        "and work area status. Dry run unless --commit is given."
    )

    def add_arguments(self, parser):
        parser.add_argument("--opportunity", required=True, help="Opportunity ID or opportunity_id UUID.")
        parser.add_argument(
            "--visit-ids", nargs="+", type=int, required=True, help="UserVisit IDs (primary keys) to approve."
        )
        parser.add_argument(
            "--agree",
            action="store_true",
            help="Also mark the visits agreed, as an auto-approved visit is. Only for opportunities with automatic "
            "visit verification, which have no PM review; flagged visits are skipped.",
        )
        parser.add_argument("--commit", action="store_true", help="Write the changes. Without it nothing is saved.")

    def handle(self, *args, opportunity, visit_ids, agree, commit, **options):
        _check_justification()
        opportunity = _get_opportunity(opportunity)
        _check_agree(opportunity, agree)

        candidates = list(approvable_visits(opportunity, visit_ids))
        over_limit = [visit for visit in candidates if visit.status == VisitValidationStatus.over_limit]
        flagged_skipped = [visit for visit in over_limit if visit.flagged] if agree else []
        to_approve = [visit for visit in over_limit if visit not in flagged_skipped]

        self._report(opportunity, visit_ids, candidates, over_limit, flagged_skipped, to_approve)
        self._warn_about_payment(opportunity, agree)
        if not commit:
            self.stdout.write(self.style.WARNING("Dry run: nothing was saved. Rerun with --commit to approve."))
            return

        with transaction.atomic():
            approved_count = approve_user_visits(opportunity, to_approve, JUSTIFICATION, agree=agree)
        self.stdout.write(self.style.SUCCESS(f"Approved {approved_count} visits."))

    def _report(self, opportunity, visit_ids, candidates, over_limit, flagged_skipped, to_approve):
        not_over_limit = [visit for visit in candidates if visit not in over_limit]
        candidate_ids = {visit.id for visit in candidates}
        not_approvable_ids = [visit_id for visit_id in visit_ids if visit_id not in candidate_ids]

        self.stdout.write(f"Opportunity {opportunity.pk} ({opportunity.name})")
        self._write_ids("Will approve", to_approve)
        self._write_ids("  of which flagged, saved with the justification", [v for v in to_approve if v.flagged])
        self.stdout.write(f"Justification: {JUSTIFICATION}")
        self._write_ids("Skipped, flagged (--agree leaves them for review)", flagged_skipped)
        self._write_ids("Skipped, not over limit", not_over_limit)
        self.stdout.write(
            f"Skipped, not in this opportunity or already approved: {len(not_approvable_ids)} {not_approvable_ids}"
        )

    def _warn_about_payment(self, opportunity, agree):
        if opportunity.automatic_visit_verification and not agree:
            self.stdout.write(
                self.style.WARNING(
                    "This opportunity has automatic visit verification, so no PM can agree to these visits and "
                    "they will not accrue payment. Pass --agree to mark them agreed."
                )
            )
        if not opportunity.auto_approve_payments:
            self.stdout.write(
                self.style.WARNING("auto_approve_payments is off: approving these visits will not accrue payment.")
            )

    def _write_ids(self, label, visits):
        self.stdout.write(f"{label}: {len(visits)} {sorted(visit.id for visit in visits)}")


def _get_opportunity(identifier: str) -> Opportunity:
    try:
        return get_object_by_uuid_or_int(Opportunity.objects.all(), identifier, uuid_field="opportunity_id")
    except Http404:
        raise CommandError(f"Opportunity {identifier} does not exist.")


def _check_justification():
    if not JUSTIFICATION or len(JUSTIFICATION) > JUSTIFICATION_MAX_LENGTH:
        raise CommandError(f"JUSTIFICATION must be 1 to {JUSTIFICATION_MAX_LENGTH} characters long.")


def _check_agree(opportunity: Opportunity, agree: bool):
    if agree and not opportunity.automatic_visit_verification:
        raise CommandError(
            "--agree is only for opportunities with automatic visit verification. On this opportunity the PM "
            "agrees to approved visits through review."
        )
