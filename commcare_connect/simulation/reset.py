"""Remove what a scenario built, so ``run_scenario`` can rebuild it from a clean slate.

Reset is cleanup, not simulation, so it deletes rows directly. Several of the relations it walks are
``PROTECT`` or ``DO_NOTHING``, so it deletes the referring rows first rather than relying on the cascade.

It only deletes what the named organizations own. Before deleting anything it refuses, with ``ResetBlocked``,
when another organization's opportunity hangs off them (through their program, their supervision or their apps),
or when a protected record such as a solicitation still points at them.

Users are kept: the admins existed before the scenario, and worker users are matched by phone number when a
scenario invites them again, as ConnectID users would be.
"""

import operator
from functools import reduce

from django.db import transaction
from django.db.models import ProtectedError, Q

from commcare_connect.opportunity.models import (
    Assessment,
    CompletedModule,
    CompletedWork,
    CompletedWorkInvoice,
    DeliverUnit,
    Opportunity,
    OpportunityAccess,
    Payment,
    PaymentInvoice,
    PaymentUnit,
    UserVisit,
)
from commcare_connect.organization.models import Organization
from commcare_connect.program.models import Program, ProgramApplication

# How another organization's opportunity can hang off the organizations being reset.
OUTSIDE_DEPENDENCIES = (
    "program__organization__in",
    "supervising_organization__in",
    "learn_app__organization__in",
    "deliver_app__organization__in",
)


class ResetBlocked(Exception):
    """Records outside the scenario's organizations depend on them, so resetting would delete or break those."""


@transaction.atomic
def reset_orgs(org_names, log=print):
    """Delete the named organizations and everything a scenario built under them. Missing names are ignored."""
    orgs = list(Organization.objects.filter(name__in=org_names))
    if not orgs:
        return
    programs = Program.objects.filter(organization__in=orgs)
    _refuse_outside_opportunities(orgs)
    for org in orgs:
        log(f"Deleting organization {org.name} ({org.slug})")
    try:
        _delete(orgs, programs)
    except ProtectedError as e:
        blockers = ", ".join(f"{type(obj).__name__} {obj.pk} ({obj})" for obj in e.protected_objects)
        raise ResetBlocked(f"Not resetting {_names(orgs)}: {blockers} still refers to them. Nothing was deleted.")


def _refuse_outside_opportunities(orgs):
    depends_on_orgs = reduce(operator.or_, (Q(**{lookup: orgs}) for lookup in OUTSIDE_DEPENDENCIES))
    outside = Opportunity.objects.exclude(organization__in=orgs).filter(depends_on_orgs).select_related("organization")
    if outside:
        listed = ", ".join(
            f"{opportunity.name} (id {opportunity.pk}, owned by {opportunity.organization.slug})"
            for opportunity in outside
        )
        raise ResetBlocked(
            f"Not resetting {_names(orgs)}: opportunities of other organizations depend on their programs or apps: "
            f"{listed}. Nothing was deleted."
        )


def _delete(orgs, programs):
    opportunities = Opportunity.objects.filter(organization__in=orgs)
    in_opportunities = {"opportunity__in": opportunities}
    accesses = OpportunityAccess.objects.filter(**in_opportunities)
    invoices = PaymentInvoice.objects.filter(**in_opportunities)

    Payment.objects.filter(
        Q(opportunity_access__in=accesses) | Q(invoice__in=invoices) | Q(organization__in=orgs)
    ).delete()
    CompletedWorkInvoice.objects.filter(invoice__in=invoices).delete()
    invoices.delete()
    UserVisit.objects.filter(**in_opportunities).delete()
    CompletedWork.objects.filter(opportunity_access__in=accesses).delete()
    CompletedModule.objects.filter(**in_opportunities).delete()
    Assessment.objects.filter(**in_opportunities).delete()
    DeliverUnit.objects.filter(payment_unit__opportunity__in=opportunities).update(payment_unit=None)
    PaymentUnit.objects.filter(**in_opportunities).update(parent_payment_unit=None)
    PaymentUnit.objects.filter(**in_opportunities).delete()
    accesses.delete()
    opportunities.delete()
    ProgramApplication.objects.filter(Q(program__in=programs) | Q(organization__in=orgs)).delete()
    programs.delete()
    Organization.objects.filter(pk__in=[org.pk for org in orgs]).delete()


def _names(orgs):
    return ", ".join(org.name for org in orgs)
