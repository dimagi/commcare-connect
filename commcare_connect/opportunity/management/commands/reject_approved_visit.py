"""Reject an approved visit on a managed opportunity that cannot be rejected through the UI.

Scope
-----
Managed opportunities only. In non-managed opportunities the UI Reject flow is
unrestricted, so this command refuses and instructs the operator to use the UI.

When to use
-----------
Use this when a visit reaches a state where neither NM nor PM can act on it:

    status=approved, review_status=agree, review_created_on=null

The UI gates all rejection/disagree actions on these fields, so the visit
becomes stuck. This command flips the visit to ``rejected`` and recomputes
the related CompletedWork so its cached ``saved_payment_accrued`` zeroes out
and it stops landing on the next invoice.

Safety checks (each raises CommandError)
----------------------------------------
1. Opportunity exists by ``opportunity_id`` UUID.
2. Visit exists by ``user_visit_id`` UUID and belongs to that opportunity.
3. Opportunity is managed.
4. Visit is currently in ``approved`` status.
5. Visit has a linked CompletedWork (otherwise there is no payment to undo).
6. CompletedWork is not already linked to a PaymentInvoice — if it is, the
   payment is already on a generated invoice and unwinding it requires a
   separate ops step (unlink CompletedWork, adjust invoice ``amount``).

Suspended access is intentionally allowed
-----------------------------------------
The typical caller workflow is: suspend a worker (fraud detected, contract
terminated), then withhold their pending approved work from the next invoice.
We call ``update_payment_accrued_for_user`` directly (instead of
``update_payment_accrued``) so the recompute runs even when
``OpportunityAccess.suspended=True`` — the bulk path filters suspended
accesses out.

Example
-------
::

    ./manage.py reject_approved_visit \\
        --opportunity-id <opp-uuid> \\
        --visit-id <visit-uuid> \\
        --reason "Per CCCT-XXXX: stuck in agree state with null review_created_on" \\
        --actor-email ops@dimagi.com \\
        --dry-run
"""

import pghistory
from django.core.management import BaseCommand, CommandError
from django.db import transaction

from commcare_connect.opportunity.models import Opportunity, UserVisit, VisitValidationStatus
from commcare_connect.opportunity.visit_import import update_payment_accrued_for_user


class Command(BaseCommand):
    help = (
        "Reject an approved visit on a managed opportunity that cannot be rejected through the UI "
        "(e.g. stuck with review_status=agree and review_created_on=null). Refuses on non-managed "
        "opportunities, missing CompletedWork, or CompletedWork already linked to an invoice. "
        "Suspended accesses are supported by design. See module docstring for full details."
    )

    def add_arguments(self, parser):
        parser.add_argument("--opportunity-id", required=True, help="Opportunity UUID (opportunity_id field).")
        parser.add_argument("--visit-id", required=True, help="UserVisit UUID (user_visit_id field).")
        parser.add_argument("--reason", required=True, help="Rejection reason stored on the visit.")
        parser.add_argument("--actor-email", required=True, help="Email recorded in the pghistory audit trail.")
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Run checks and print what would change without mutating any data.",
        )

    def handle(self, *args, **options):
        opportunity = self._get_opportunity(options["opportunity_id"])
        visit = self._get_visit(opportunity, options["visit_id"])
        self._check_safe_to_reject(opportunity, visit)

        if options["dry_run"]:
            self.stdout.write(
                f"[dry-run] Would reject visit {visit.user_visit_id} (opp {opportunity.opportunity_id}) "
                f"with reason {options['reason']!r}; CompletedWork {visit.completed_work_id} "
                "would be recomputed."
            )
            return

        with transaction.atomic():
            with pghistory.context(username=options["actor_email"], user_email=options["actor_email"]):
                visit.status = VisitValidationStatus.rejected
                visit.reason = options["reason"]
                visit.save(update_fields=["status", "reason", "status_modified_date"])
            update_payment_accrued_for_user(visit.opportunity_access, incremental=False)
        self.stdout.write(
            self.style.SUCCESS(
                f"Rejected visit {visit.user_visit_id}; CompletedWork {visit.completed_work_id} recomputed."
            )
        )

    def _check_safe_to_reject(self, opportunity, visit):
        if not opportunity.managed:
            raise CommandError(
                f"Opportunity {opportunity.opportunity_id} is not a managed opportunity; "
                "this command only handles the managed-opportunity stuck state."
            )
        if visit.status != VisitValidationStatus.approved:
            raise CommandError(f"Visit {visit.user_visit_id} is not in 'approved' status (current: {visit.status}).")
        if visit.completed_work is None:
            raise CommandError(f"Visit {visit.user_visit_id} has no linked CompletedWork; cannot recompute payment.")
        if visit.completed_work.invoice_id is not None:
            raise CommandError(
                f"CompletedWork {visit.completed_work_id} is already linked to invoice "
                f"{visit.completed_work.invoice_id}; reject through invoice flow instead."
            )

    def _get_opportunity(self, opportunity_uuid):
        try:
            return Opportunity.objects.get(opportunity_id=opportunity_uuid)
        except Opportunity.DoesNotExist as e:
            raise CommandError(f"Opportunity {opportunity_uuid} not found.") from e

    def _get_visit(self, opportunity, visit_uuid):
        try:
            return UserVisit.objects.select_related("completed_work", "opportunity_access").get(
                user_visit_id=visit_uuid, opportunity=opportunity
            )
        except UserVisit.DoesNotExist as e:
            raise CommandError(f"Visit {visit_uuid} not found in opportunity {opportunity.opportunity_id}.") from e
