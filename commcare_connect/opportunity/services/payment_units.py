"""Creating and changing an opportunity's payment units.

The UI's payment unit pages and the automation API both call these functions, so access, validation and the
follow-up work on claim limits are decided once (ADR-0003, ADR-0004). Callers pass plain values and translate
``ValidationError`` (messages keyed by ``PaymentUnitInput`` field) and ``PermissionDenied`` into their own format.
"""

from dataclasses import dataclass, field
from datetime import date

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils.translation import gettext, gettext_lazy
from waffle import switch_is_active

from commcare_connect.flags.switch_names import OPTIONAL_DAILY_LIMIT
from commcare_connect.opportunity.models import (
    DeliverUnit,
    Opportunity,
    OpportunityClaim,
    OpportunityClaimLimit,
    PaymentUnit,
)
from commcare_connect.program.utils import user_is_opportunity_pm

ASSIGNED_ELSEWHERE = gettext_lazy("These deliver units don't exist or already count for another payment unit: {ids}")


@dataclass(frozen=True)
class PaymentUnitInput:
    name: str
    amount: int
    org_amount: int
    max_total: int
    # None means no daily limit, which only the OPTIONAL_DAILY_LIMIT switch allows.
    max_daily: int | None
    description: str = ""
    display_name: str = ""
    start_date: date | None = None
    end_date: date | None = None
    required_deliver_units: list[int] = field(default_factory=list)
    optional_deliver_units: list[int] = field(default_factory=list)
    # Top-level payment units of the same opportunity that must complete before this one does.
    child_payment_units: list[int] = field(default_factory=list)
    # Names shown to workers for some of the selected deliver units, by deliver unit id.
    deliver_unit_display_names: dict[int, str] = field(default_factory=dict)

    @property
    def deliver_units(self):
        return {*self.required_deliver_units, *self.optional_deliver_units}


def create_payment_units(user, opportunity: Opportunity, units: list[PaymentUnitInput]) -> list[PaymentUnit]:
    """Add payment units to the opportunity together, so either all are created or none are."""
    _check_can_manage_payment_units(user, opportunity)
    _validate_deliver_units_not_shared(units)
    for unit in units:
        _validate_unit(opportunity, unit)
    _validate_budget(opportunity, [_unsaved(unit) for unit in units], exclude=None)

    with transaction.atomic():
        created = [_create(opportunity, unit) for unit in units]
        _refresh_claim_limits(opportunity)
    return created


def update_payment_unit(user, payment_unit: PaymentUnit, unit: PaymentUnitInput) -> PaymentUnit:
    """Replace the payment unit's settings, deliver units and child payment units with ``unit``'s."""
    opportunity = payment_unit.opportunity
    _check_can_manage_payment_units(user, opportunity)
    _validate_unit(opportunity, unit, payment_unit)
    _validate_budget(opportunity, [_unsaved(unit)], exclude=payment_unit)

    with transaction.atomic():
        _set_fields(payment_unit, unit)
        payment_unit.save()
        _unlink_deliver_units(payment_unit, keep=unit.deliver_units)
        _link_deliver_units(payment_unit, unit)
        _unlink_child_payment_units(payment_unit, keep=unit.child_payment_units)
        _link_child_payment_units(payment_unit, unit.child_payment_units)
    return payment_unit


def _check_can_manage_payment_units(user, opportunity):
    """Payment units are a program-manager action: ADMIN through an org other than the delivering one."""
    if not user_is_opportunity_pm(user, opportunity):
        raise PermissionDenied


def _validate_deliver_units_not_shared(units):
    seen = set()
    for unit in units:
        shared = seen & unit.deliver_units
        if shared:
            raise ValidationError(
                {
                    "required_deliver_units": gettext(
                        "Deliver units assigned to multiple payment units: {ids}"
                    ).format(ids=sorted(shared))
                }
            )
        seen |= unit.deliver_units


def _validate_unit(opportunity, unit, payment_unit=None):
    errors = {}
    if unit.start_date and unit.end_date and unit.end_date < unit.start_date:
        errors["end_date"] = gettext("End date cannot be earlier than start date.")
    if unit.max_daily is None and not switch_is_active(OPTIONAL_DAILY_LIMIT):
        errors["max_daily"] = gettext("Enter a daily limit.")
    if set(unit.required_deliver_units) & set(unit.optional_deliver_units):
        errors["optional_deliver_units"] = gettext("A deliver unit cannot be both required and optional.")
    errors.update(_unassignable_deliver_unit_errors(opportunity, unit, payment_unit))
    if not set(unit.child_payment_units) <= _assignable_child_ids(opportunity, payment_unit):
        errors["child_payment_units"] = gettext("Select payment units of this opportunity that have no parent.")
    if errors:
        raise ValidationError(errors)


def _unassignable_deliver_unit_errors(opportunity, unit, payment_unit):
    assignable = set(
        DeliverUnit.objects.assignable(opportunity, payment_unit)
        .filter(id__in=unit.deliver_units)
        .values_list("id", flat=True)
    )
    errors = {}
    for field_name in ("required_deliver_units", "optional_deliver_units"):
        invalid = set(getattr(unit, field_name)) - assignable
        if invalid:
            errors[field_name] = ASSIGNED_ELSEWHERE.format(ids=sorted(invalid))
    return errors


def _assignable_child_ids(opportunity, payment_unit):
    """Top-level units, or this unit's current children, excluding the unit itself and its own parent."""
    candidates = opportunity.paymentunit_set.filter(parent_payment_unit__isnull=True)
    if payment_unit:
        candidates = (candidates | opportunity.paymentunit_set.filter(parent_payment_unit=payment_unit)).exclude(
            pk__in=[payment_unit.pk, payment_unit.parent_payment_unit_id]
        )
    return set(candidates.values_list("id", flat=True))


def _validate_budget(opportunity, proposed_units, exclude):
    """Reject the change if the budget can't give every worker who already claimed the full limit."""
    existing = opportunity.paymentunit_set.all()
    if exclude:
        existing = existing.exclude(pk=exclude.pk)
    opportunity.validate_budget_for_payment_units([*proposed_units, *existing])


def _unsaved(unit):
    return PaymentUnit(max_total=unit.max_total, amount=unit.amount, org_amount=unit.org_amount)


def _create(opportunity, unit):
    payment_unit = PaymentUnit(opportunity=opportunity)
    _set_fields(payment_unit, unit)
    payment_unit.save()
    _link_deliver_units(payment_unit, unit)
    _link_child_payment_units(payment_unit, unit.child_payment_units)
    return payment_unit


def _set_fields(payment_unit, unit):
    for name in (
        "name",
        "display_name",
        "description",
        "amount",
        "org_amount",
        "max_total",
        "max_daily",
        "start_date",
        "end_date",
    ):
        setattr(payment_unit, name, getattr(unit, name))


def _link_deliver_units(payment_unit, unit):
    """Point the selected deliver units at the payment unit and save their display names.

    Rechecks under the transaction that each unit is still free, in case another request took it after
    validation.
    """
    available = DeliverUnit.objects.assignable(payment_unit.opportunity, payment_unit)
    linked = available.filter(id__in=unit.required_deliver_units).update(payment_unit=payment_unit, optional=False)
    linked += available.filter(id__in=unit.optional_deliver_units).update(payment_unit=payment_unit, optional=True)
    if linked != len(unit.deliver_units):
        raise ValidationError({"required_deliver_units": ASSIGNED_ELSEWHERE.format(ids=sorted(unit.deliver_units))})
    _save_deliver_unit_display_names(payment_unit, unit.deliver_unit_display_names)


def _save_deliver_unit_display_names(payment_unit, display_names):
    changed = []
    for deliver_unit in payment_unit.deliver_units.filter(id__in=display_names):
        if deliver_unit.display_name != display_names[deliver_unit.id]:
            deliver_unit.display_name = display_names[deliver_unit.id]
            changed.append(deliver_unit)
    DeliverUnit.objects.bulk_update(changed, ["display_name"])


def _unlink_deliver_units(payment_unit, keep):
    payment_unit.deliver_units.exclude(id__in=keep).update(payment_unit=None, optional=False)


def _link_child_payment_units(payment_unit, child_ids):
    PaymentUnit.objects.filter(
        opportunity=payment_unit.opportunity, id__in=child_ids, parent_payment_unit__isnull=True
    ).update(parent_payment_unit=payment_unit)


def _unlink_child_payment_units(payment_unit, keep):
    PaymentUnit.objects.filter(parent_payment_unit=payment_unit).exclude(id__in=keep).update(parent_payment_unit=None)


def _refresh_claim_limits(opportunity):
    """Give workers who already claimed a limit for the new payment units."""
    for claim in OpportunityClaim.objects.filter(opportunity_access__opportunity=opportunity):
        OpportunityClaimLimit.create_claim_limits(opportunity, claim)
