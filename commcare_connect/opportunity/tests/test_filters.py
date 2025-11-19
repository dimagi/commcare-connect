import pytest

from commcare_connect.opportunity.filters import UserVisitFilterSet
from commcare_connect.opportunity.models import UserVisit
from commcare_connect.opportunity.tests.factories import (
    CompletedWorkFactory,
    DeliverUnitFactory,
    OpportunityAccessFactory,
    OpportunityFactory,
    OpportunityVerificationFlagsFactory,
    PaymentUnitFactory,
    UserVisitFactory,
)
from commcare_connect.utils.flags import Flags


@pytest.mark.django_db
def test_uservisit_filterset_filters_by_flags():
    opportunity = OpportunityFactory()
    OpportunityVerificationFlagsFactory(
        opportunity=opportunity,
        duplicate=True,
        gps=False,
        location=0,
        catchment_areas=False,
    )

    access = OpportunityAccessFactory(opportunity=opportunity)
    payment_unit = PaymentUnitFactory(opportunity=opportunity)
    deliver_unit = DeliverUnitFactory(app=opportunity.deliver_app, payment_unit=payment_unit)
    completed_work_flagged = CompletedWorkFactory(opportunity_access=access, payment_unit=payment_unit)
    completed_work_clean = CompletedWorkFactory(opportunity_access=access, payment_unit=payment_unit)

    flagged_visit = UserVisitFactory(
        opportunity=opportunity,
        opportunity_access=access,
        user=access.user,
        deliver_unit=deliver_unit,
        completed_work=completed_work_flagged,
        flagged=True,
        flag_reason={"flags": [(Flags.DUPLICATE.value, "Duplicate submission")]},
    )
    clean_visit = UserVisitFactory(
        opportunity=opportunity,
        opportunity_access=access,
        user=access.user,
        deliver_unit=deliver_unit,
        completed_work=completed_work_clean,
        flagged=False,
        flag_reason=None,
    )

    filterset = UserVisitFilterSet(
        data={"flags": [Flags.DUPLICATE.value]},
        queryset=UserVisit.objects.filter(opportunity=opportunity),
        opportunity=opportunity,
    )

    assert "flags" in filterset.filters
    available_flags = dict(filterset.filters["flags"].extra["choices"])
    assert Flags.DUPLICATE.value in available_flags

    filtered_visits = set(filterset.qs.values_list("id", flat=True))
    assert flagged_visit.id in filtered_visits
    assert clean_visit.id not in filtered_visits
