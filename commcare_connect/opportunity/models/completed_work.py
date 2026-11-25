"""Completed work: the payable unit built up from a worker's visits."""

from collections import Counter

from django.db import models
from django.utils.timezone import now
from django.utils.translation import gettext, gettext_lazy

from commcare_connect.opportunity.models.access import OpportunityAccess
from commcare_connect.opportunity.models.base import CHOICE_FIELD_MAX_LENGTH
from commcare_connect.opportunity.models.payment_units import PaymentUnit


class CompletedWorkStatus(models.TextChoices):
    pending = "pending", gettext("Pending")
    approved = "approved", gettext("Approved")
    rejected = "rejected", gettext("Rejected")
    over_limit = "over_limit", gettext("Over Limit")
    incomplete = "incomplete", gettext("Incomplete")


class CompletedWork(models.Model):
    opportunity_access = models.ForeignKey(OpportunityAccess, on_delete=models.CASCADE)
    payment_unit = models.ForeignKey(PaymentUnit, on_delete=models.DO_NOTHING)
    status = models.CharField(
        max_length=CHOICE_FIELD_MAX_LENGTH, choices=CompletedWorkStatus, default=CompletedWorkStatus.incomplete
    )
    last_modified = models.DateTimeField(auto_now=True)
    entity_id = models.CharField(max_length=255, null=True, blank=True)
    entity_name = models.CharField(max_length=255, null=True, blank=True)
    reason = models.CharField(max_length=300, null=True, blank=True)
    status_modified_date = models.DateTimeField(null=True, default=now)
    payment_date = models.DateTimeField(null=True)
    date_created = models.DateTimeField(auto_now_add=True)

    # these fields are the stored/cached versions of the completed_count and approved_count
    # and the associated calculations needed to do reporting on payments.
    # it is expected that they are updated every time the completed_count or approved_count is updated,
    # but should not be used for real-time display of that information until confirmed to be working.
    saved_completed_count = models.IntegerField(default=0)
    saved_approved_count = models.IntegerField(default=0)
    saved_payment_accrued = models.IntegerField(default=0, help_text="Payment accrued for the FLW.")
    saved_payment_accrued_usd = models.DecimalField(
        max_digits=10, decimal_places=2, default=0, help_text="Payment accrued for the FLW in USD."
    )
    saved_org_payment_accrued = models.IntegerField(
        default=0, help_text=gettext_lazy("Payment accrued for the organization")
    )
    saved_org_payment_accrued_usd = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=0,
        help_text=gettext_lazy("Payment accrued for the organization in USD."),
    )
    invoiced_approved_count = models.IntegerField(
        default=0,
        help_text=gettext_lazy("Approved units already billed on a live invoice."),
    )

    class Meta:
        unique_together = ("opportunity_access", "entity_id", "payment_unit")

    def __init__(self, *args, **kwargs):
        self.status = CompletedWorkStatus.incomplete
        super().__init__(*args, **kwargs)

    def __setattr__(self, name, value):
        if name == "status":
            if getattr(self, "status", None) != value:  # Check if status has changed
                self.status_modified_date = now()
        super().__setattr__(name, value)

    # TODO: add caching on this property
    @property
    def completed_count(self):
        """Returns the no of completion of this work. Includes duplicate submissions."""
        visits = self.uservisit_set.values_list("deliver_unit_id", flat=True)
        return self.calculate_completed(visits)

    @property
    def approved_count(self):
        from commcare_connect.opportunity.models.visits import VisitReviewStatus, VisitValidationStatus

        qs = self.uservisit_set.filter(status=VisitValidationStatus.approved, review_status=VisitReviewStatus.agree)
        visits = qs.values_list("deliver_unit_id", flat=True)
        return self.calculate_completed(visits, approved=True)

    def calculate_completed(self, visits, approved=False):
        """Count completed deliveries for this entity by taking the minimum across required deliver units.

        A delivery is "complete" when all required deliver unit forms have been submitted.
        The count is the minimum across required units (all must be done), capped by the
        total of optional units if any exist, and further constrained by child payment
        unit completion counts.
        """
        unit_counts = Counter(visits)
        deliver_units = self.payment_unit.deliver_units.values("id", "optional")
        required_deliver_units = list(
            du["id"] for du in filter(lambda du: not du.get("optional", False), deliver_units)
        )
        optional_deliver_units = list(du["id"] for du in filter(lambda du: du.get("optional", False), deliver_units))
        # NOTE: The min unit count is the completed required deliver units for an entity_id
        if required_deliver_units:
            number_completed = min(unit_counts[deliver_id] for deliver_id in required_deliver_units)
        else:
            # this is an unexpected case, but can show up in old/test data
            number_completed = 0
        if optional_deliver_units:
            # The sum calculates the number of optional deliver units completed and to process
            # duplicates with extra optional deliver units
            optional_completed = sum(unit_counts[deliver_id] for deliver_id in optional_deliver_units)
            number_completed = min(number_completed, optional_completed)
        child_payment_units = self.payment_unit.child_payment_units.all()
        if child_payment_units:
            child_completed_works = CompletedWork.objects.filter(
                opportunity_access=self.opportunity_access,
                payment_unit__in=child_payment_units,
                entity_id=self.entity_id,
            )
            child_completed_work_count = 0
            for completed_work in child_completed_works:
                if approved:
                    child_completed_work_count += completed_work.approved_count
                else:
                    child_completed_work_count += completed_work.completed_count
            number_completed = min(number_completed, child_completed_work_count)
        return number_completed

    @property
    def completed(self):
        return self.completed_count > 0

    @property
    def payment_accrued(self):
        """Returns the total payment accrued for this completed work. Includes duplicates"""
        return self.approved_count * self.payment_unit.amount

    @property
    def flags(self):
        from commcare_connect.opportunity.models.visits import VisitValidationStatus

        visits = self.uservisit_set.exclude(status=VisitValidationStatus.approved).values_list(
            "flag_reason", flat=True
        )
        flags = set()
        for visit in visits:
            if not visit:
                continue
            for flag, _ in visit.get("flags", []):
                flags.add(flag)
        return list(flags)

    @property
    def completion_date(self):
        visit = self.uservisit_set.order_by("visit_date").last()
        return visit.visit_date if visit else None
