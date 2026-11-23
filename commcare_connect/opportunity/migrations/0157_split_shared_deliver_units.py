"""Give every opportunity its own copy of each deliver unit it uses (part 2 of CCCT-3057).

Before 0156, deliver units belonged only to an app, so opportunities that reused an app shared its
units, and the newest opportunity's payment units took them over. 0156 gave each unit one owning
opportunity. Here every other opportunity that uses a unit gets its own copy:

- An opportunity "uses" a unit if it has visits on it, flag rules or form-validation rules for it, or
  one of its payment units counts it.
- The copy keeps the unit's app, slug, name, display name and optional flag. Its payment unit is the
  one this opportunity's payment unit counted it under, else the one the opportunity's completed work
  used for its visits on the unit, else none.
- The opportunity's visits, flag rules and form-validation rules move to its copy.

A unit's payment unit must belong to the unit's own opportunity, so a unit left on another
opportunity's payment unit is detached from it (that opportunity got a copy that keeps it). Units
with no opportunity are then deleted: nothing refers to them any more.
"""

from collections import Counter

from django.db import migrations
from django.db.models import F, Q


def split_shared_deliver_units(apps, schema_editor):
    DeliverUnit = apps.get_model("opportunity", "DeliverUnit")
    for unit_id, opportunity_id in sorted(_units_used_by_another_opportunity(apps)):
        unit = DeliverUnit.objects.get(pk=unit_id)
        copy = _copy_for_opportunity(apps, unit, opportunity_id)
        _move_references(apps, unit, copy, opportunity_id)
    _detach_other_opportunities_payment_units(apps)
    DeliverUnit.objects.filter(opportunity__isnull=True).delete()


def _units_used_by_another_opportunity(apps):
    """(unit id, opportunity id) for each opportunity that uses a unit it doesn't own."""
    UserVisit = apps.get_model("opportunity", "UserVisit")
    DeliverUnit = apps.get_model("opportunity", "DeliverUnit")
    DeliverUnitFlagRules = apps.get_model("opportunity", "DeliverUnitFlagRules")
    FormJsonValidationRules = apps.get_model("opportunity", "FormJsonValidationRules")
    FormJsonLink = FormJsonValidationRules.deliver_unit.through

    def not_owner(owner_field, user_field):
        return Q(**{f"{owner_field}__isnull": True}) | ~Q(**{owner_field: F(user_field)})

    pairs = set()
    pairs |= set(
        UserVisit.objects.filter(not_owner("deliver_unit__opportunity_id", "opportunity_id"))
        .values_list("deliver_unit_id", "opportunity_id")
        .order_by()
        .distinct()
    )
    pairs |= set(
        DeliverUnitFlagRules.objects.filter(not_owner("deliver_unit__opportunity_id", "opportunity_id"))
        .values_list("deliver_unit_id", "opportunity_id")
        .order_by()
        .distinct()
    )
    pairs |= set(
        FormJsonLink.objects.filter(
            not_owner("deliverunit__opportunity_id", "formjsonvalidationrules__opportunity_id")
        )
        .values_list("deliverunit_id", "formjsonvalidationrules__opportunity_id")
        .order_by()
        .distinct()
    )
    pairs |= set(
        DeliverUnit.objects.filter(payment_unit__isnull=False)
        .filter(not_owner("opportunity_id", "payment_unit__opportunity_id"))
        .values_list("id", "payment_unit__opportunity_id")
    )
    return pairs


def _copy_for_opportunity(apps, unit, opportunity_id):
    """The opportunity's own unit for the slug, created if it has none yet."""
    DeliverUnit = apps.get_model("opportunity", "DeliverUnit")
    payment_unit_id = _payment_unit_for(apps, unit, opportunity_id)
    copy, created = DeliverUnit.objects.get_or_create(
        opportunity_id=opportunity_id,
        app_id=unit.app_id,
        slug=unit.slug,
        defaults={
            "name": unit.name,
            "display_name": unit.display_name,
            "optional": unit.optional,
            "payment_unit_id": payment_unit_id,
        },
    )
    if not created and copy.payment_unit_id is None and payment_unit_id is not None:
        # An opportunity that reused the app after 0156 already has its own unit; keep its configuration,
        # but restore the payment unit if it has none
        copy.payment_unit_id = payment_unit_id
        copy.optional = unit.optional
        copy.save(update_fields=["payment_unit_id", "optional"])
    return copy


def _payment_unit_for(apps, unit, opportunity_id):
    """The opportunity's payment unit that counted this unit, if any."""
    PaymentUnit = apps.get_model("opportunity", "PaymentUnit")
    UserVisit = apps.get_model("opportunity", "UserVisit")
    if (
        unit.payment_unit_id
        and PaymentUnit.objects.filter(pk=unit.payment_unit_id, opportunity_id=opportunity_id).exists()
    ):
        return unit.payment_unit_id
    used = Counter(
        UserVisit.objects.filter(
            deliver_unit_id=unit.pk,
            opportunity_id=opportunity_id,
            completed_work__payment_unit__opportunity_id=opportunity_id,
        ).values_list("completed_work__payment_unit_id", flat=True)
    )
    # Most visits first, then the newest payment unit
    return max(used, key=lambda pk: (used[pk], pk)) if used else None


def _move_references(apps, unit, copy, opportunity_id):
    """Point the opportunity's visits and rules on ``unit`` at its own ``copy``."""
    UserVisit = apps.get_model("opportunity", "UserVisit")
    DeliverUnitFlagRules = apps.get_model("opportunity", "DeliverUnitFlagRules")
    FormJsonValidationRules = apps.get_model("opportunity", "FormJsonValidationRules")
    FormJsonLink = FormJsonValidationRules.deliver_unit.through

    UserVisit.objects.filter(deliver_unit_id=unit.pk, opportunity_id=opportunity_id).update(deliver_unit_id=copy.pk)

    flag_rules = DeliverUnitFlagRules.objects.filter(deliver_unit_id=unit.pk, opportunity_id=opportunity_id)
    if DeliverUnitFlagRules.objects.filter(deliver_unit_id=copy.pk, opportunity_id=opportunity_id).exists():
        # Flag rules are unique per (unit, opportunity); the copy's own rule wins
        flag_rules.delete()
    else:
        flag_rules.update(deliver_unit_id=copy.pk)

    links = FormJsonLink.objects.filter(deliverunit_id=unit.pk, formjsonvalidationrules__opportunity_id=opportunity_id)
    for rule_id in links.values_list("formjsonvalidationrules_id", flat=True):
        FormJsonLink.objects.get_or_create(formjsonvalidationrules_id=rule_id, deliverunit_id=copy.pk)
    links.delete()


def _detach_other_opportunities_payment_units(apps):
    """A payment unit only counts its own opportunity's units; that opportunity now has a copy."""
    DeliverUnit = apps.get_model("opportunity", "DeliverUnit")
    DeliverUnit.objects.filter(opportunity__isnull=False, payment_unit__isnull=False).exclude(
        payment_unit__opportunity_id=F("opportunity_id")
    ).update(payment_unit=None, optional=False)


class Migration(migrations.Migration):
    dependencies = [
        ("opportunity", "0156_deliverunit_opportunity"),
    ]

    operations = [
        # The secondary database receives the changed rows by replication.
        migrations.RunPython(
            split_shared_deliver_units, migrations.RunPython.noop, hints={"run_on_secondary": False}
        ),
    ]
