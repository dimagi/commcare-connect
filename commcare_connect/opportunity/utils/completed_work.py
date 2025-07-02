from collections import defaultdict

from django.db.models import Sum

from commcare_connect.opportunity.models import (
    CompletedWork,
    CompletedWorkStatus,
    DeliverUnit,
    OpportunityAccess,
    Payment,
    PaymentUnit,
    VisitReviewStatus,
    VisitValidationStatus,
)


class CompletedWorkUpdater:
    def __init__(self, opportunity_access: OpportunityAccess, completed_works, compute_payment=True):
        self.opportunity_access = opportunity_access
        self.opportunity = opportunity_access.opportunity
        self.compute_payment = compute_payment
        self.completed_works = completed_works

        self.counts = defaultdict(lambda: {"completed": 0, "approved": 0})
        self.parent_child_payment_unit_map = defaultdict(list)
        self.deliver_unit_map = defaultdict(list)

    def _prepare_deliver_payment_unit_maps(self):
        payment_units = PaymentUnit.objects.filter(
            id__in=self.completed_works.values_list("payment_unit_id", flat=True)
        ).values("id", "parent_payment_unit")
        deliver_units = DeliverUnit.objects.filter(
            payment_unit__in=self.completed_works.values_list("payment_unit_id", flat=True)
        ).values("id", "optional", "payment_unit_id")

        for payment_unit in payment_units:
            parent_id = payment_unit["parent_payment_unit"]
            self.parent_child_payment_unit_map[parent_id].append(payment_unit["id"])

        for deliver_unit in deliver_units:
            pu_id = deliver_unit["payment_unit_id"]
            du_id = deliver_unit["id"]
            optional = deliver_unit.get("optional", False)
            self.deliver_unit_map[pu_id].append((du_id, optional))

    def _get_completed_work_counts(self):
        self._prepare_deliver_payment_unit_maps()

        for completed_work in self.completed_works:
            if completed_work.id in self.counts:
                continue

            unit_counts = defaultdict(int)
            approved_unit_counts = defaultdict(int)
            for user_visit in completed_work.uservisit_set.all():
                unit_counts[user_visit.deliver_unit_id] += 1
                if user_visit.status == VisitValidationStatus.approved.value:
                    approved_unit_counts[user_visit.deliver_unit_id] += 1

            payment_unit_id = completed_work.payment_unit_id
            deliver_units = self.deliver_unit_map[payment_unit_id]
            required_deliver_units = [du_id for du_id, optional in deliver_units if not optional]
            optional_deliver_units = [du_id for du_id, optional in deliver_units if optional]
            number_completed = min([unit_counts[deliver_id] for deliver_id in required_deliver_units], default=0)
            number_approved = min(
                [approved_unit_counts[deliver_id] for deliver_id in required_deliver_units], default=0
            )

            if optional_deliver_units:
                optional_completed = sum(unit_counts[deliver_id] for deliver_id in optional_deliver_units)
                number_completed = min(number_completed, optional_completed)

                optional_approved = sum(approved_unit_counts[deliver_id] for deliver_id in optional_deliver_units)
                number_approved = min(number_approved, optional_approved)

            child_payment_units = self.parent_child_payment_unit_map[payment_unit_id]
            if child_payment_units:
                child_completed_works = CompletedWork.objects.filter(
                    opportunity_access=completed_work.opportunity_access,
                    payment_unit__in=child_payment_units,
                    entity_id=completed_work.entity_id,
                )
                child_completed_work_count = child_approved_work_count = 0
                for child_completed_work in child_completed_works:
                    if child_completed_work.id not in self.counts:
                        self.counts[child_completed_work.id]["approved"] = child_completed_work.approved_count
                        self.counts[child_completed_work.id]["completed"] = child_completed_work.completed_count
                    child_approved_work_count += self.counts[child_completed_work.id]["approved"]
                    child_completed_work_count += self.counts[child_completed_work.id]["completed"]
                number_completed = min(number_completed, child_completed_work_count)
                number_approved = min(number_approved, child_approved_work_count)

            self.counts[completed_work.id]["approved"] = number_approved
            self.counts[completed_work.id]["completed"] = number_completed

    def _update_status(self, completed_work):
        updated = False
        if self.opportunity.auto_approve_payments:
            visits = completed_work.uservisit_set.values_list("status", "reason", "review_status")
            if any(status == VisitValidationStatus.rejected for status, *_ in visits):
                completed_work.status = CompletedWorkStatus.rejected
                completed_work.reason = "\n".join(reason for _, reason, _ in visits if reason)
            elif all(status == VisitValidationStatus.approved for status, *_ in visits):
                completed_work.status = CompletedWorkStatus.approved

            if (
                self.opportunity.managed
                and not all(review_status == VisitReviewStatus.agree for *_, review_status in visits)
                and completed_work.status == CompletedWorkStatus.approved
            ):
                completed_work.status = CompletedWorkStatus.pending
            updated = True
        return updated

    def _update_payment(self, completed_work):
        updated = False
        if self.compute_payment:
            completed_count = self.counts[completed_work.id]["completed"]
            approved_count = self.counts[completed_work.id]["approved"]
            amount_accrued = amount_accrued_usd = org_amount_accrued = org_amount_accrued_usd = 0
            if approved_count > 0 and completed_work.status == CompletedWorkStatus.approved:
                from commcare_connect.opportunity.visit_import import get_exchange_rate

                amount_accrued = approved_count * completed_work.payment_unit.amount
                exchange_rate = get_exchange_rate(self.opportunity.currency, completed_work.status_modified_date)
                amount_accrued_usd = amount_accrued / exchange_rate
                # if it's a managed opportunity we also need to update the org payment amounts
                if self.opportunity.managed:
                    org_amount_accrued = approved_count * self.opportunity.org_pay_per_visit
                    org_amount_accrued_usd = org_amount_accrued / exchange_rate

            completed_work.saved_completed_count = completed_count
            completed_work.saved_approved_count = approved_count
            completed_work.saved_payment_accrued = amount_accrued
            completed_work.saved_payment_accrued_usd = amount_accrued_usd
            completed_work.saved_org_payment_accrued = org_amount_accrued
            completed_work.saved_org_payment_accrued_usd = org_amount_accrued_usd
            updated = True
        return updated

    def update_status_and_set_saved_fields(self):
        self._get_completed_work_counts()

        to_update = []
        for completed_work in self.completed_works:
            completed_count = self.counts[completed_work.id]["completed"]
            if completed_count < 1:
                continue

            status_updated = self._update_status(completed_work)
            payment_updated = self._update_payment(completed_work)
            if status_updated or payment_updated:
                to_update.append(completed_work)

        CompletedWork.objects.bulk_update(
            to_update,
            fields=[
                "reason",
                "status",
                "status_modified_date",
                "saved_completed_count",
                "saved_approved_count",
                "saved_payment_accrued",
                "saved_payment_accrued_usd",
                "saved_org_payment_accrued",
                "saved_org_payment_accrued_usd",
            ],
        )


def update_status(completed_works, opportunity_access, compute_payment=True):
    """
    Updates the status of completed works and optionally calculates & update total payment_accrued.

    If compute_payment is True, the saved fields related to completed/approved work and payments
    earned will also be saved against the model.
    """
    CompletedWorkUpdater(opportunity_access, completed_works).update_status_and_set_saved_fields()
    if compute_payment:
        opportunity_access.payment_accrued = (
            CompletedWork.objects.filter(opportunity_access=opportunity_access)
            .aggregate(payment_accrued=Sum("saved_payment_accrued"))
            .get("payment_accrued", 0)
            or 0
        )
        opportunity_access.save()


def _update_status_set_saved_fields_and_get_payment_accrued(completed_work, opportunity_access, compute_payment):
    completed_count = completed_work.completed_count
    if completed_count < 1:
        return 0

    amount_accrued = 0
    made_changes = False
    if opportunity_access.opportunity.auto_approve_payments:
        visits = completed_work.uservisit_set.exclude(status__in=[VisitValidationStatus.duplicate]).values_list(
            "status", "reason", "review_status"
        )
        if any(status == VisitValidationStatus.rejected for status, *_ in visits):
            completed_work.status = CompletedWorkStatus.rejected
            completed_work.reason = "\n".join(reason for _, reason, _ in visits if reason)
        elif all(status == VisitValidationStatus.approved for status, *_ in visits):
            completed_work.status = CompletedWorkStatus.approved

        if (
            opportunity_access.opportunity.managed
            and not all(review_status == VisitReviewStatus.agree for *_, review_status in visits)
            and completed_work.status == CompletedWorkStatus.approved
        ):
            completed_work.status = CompletedWorkStatus.pending

        made_changes = True

    if compute_payment:
        approved_count = completed_work.approved_count

        amount_accrued = amount_accrued_usd = org_amount_accrued = org_amount_accrued_usd = 0
        if approved_count > 0 and completed_work.status == CompletedWorkStatus.approved:
            from commcare_connect.opportunity.visit_import import get_exchange_rate

            amount_accrued = approved_count * completed_work.payment_unit.amount
            exchange_rate = get_exchange_rate(
                opportunity_access.opportunity.currency, completed_work.status_modified_date
            )
            amount_accrued_usd = amount_accrued / exchange_rate
            # if it's a managed opportunity we also need to update the org payment amounts
            if opportunity_access.opportunity.managed:
                org_amount_accrued = approved_count * opportunity_access.managed_opportunity.org_pay_per_visit
                org_amount_accrued_usd = org_amount_accrued / exchange_rate

        completed_work.saved_completed_count = completed_count
        completed_work.saved_approved_count = approved_count
        completed_work.saved_payment_accrued = amount_accrued
        completed_work.saved_payment_accrued_usd = amount_accrued_usd
        completed_work.saved_org_payment_accrued = org_amount_accrued
        completed_work.saved_org_payment_accrued_usd = org_amount_accrued_usd
        made_changes = True

    if made_changes:
        completed_work.save()

    return amount_accrued


def update_work_payment_date(access: OpportunityAccess):
    payments = Payment.objects.filter(opportunity_access=access).order_by("date_paid")
    completed_works = CompletedWork.objects.filter(opportunity_access=access).order_by("status_modified_date")

    if not payments or not completed_works:
        return

    works_to_update = []
    completed_works_iter = iter(completed_works)
    current_work = next(completed_works_iter)

    remaining_amount = 0

    for payment in payments:
        remaining_amount += payment.amount

        while remaining_amount >= current_work.payment_accrued:
            current_work.payment_date = payment.date_paid
            works_to_update.append(current_work)
            remaining_amount -= current_work.payment_accrued

            try:
                current_work = next(completed_works_iter)
            except StopIteration:
                break
        else:
            continue

        # we've broken out of the inner while loop so all completed_works are processed.
        break

    if works_to_update:
        CompletedWork.objects.bulk_update(works_to_update, ["payment_date"])
