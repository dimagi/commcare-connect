from io import StringIO
from uuid import uuid4

import pytest
from django.core.management import call_command

from commcare_connect.opportunity.models import UserVisit
from commcare_connect.opportunity.tests.factories import DeliverUnitFactory, UserVisitFactory


@pytest.fixture
def setup_opportunity_with_duplicates(db):
    def _setup(opportunity, xform_id, entity_id, deliver_unit, num_duplicates):
        first_visit = UserVisitFactory.create(
            opportunity=opportunity, deliver_unit=deliver_unit, xform_id=xform_id, entity_id=entity_id
        )
        for _ in range(num_duplicates - 1):
            UserVisitFactory.create(
                opportunity=opportunity, deliver_unit=deliver_unit, xform_id=xform_id, entity_id=entity_id
            )
        return first_visit

    return _setup


@pytest.mark.django_db
@pytest.mark.parametrize(
    "num_duplicates,expected_remaining,dry_run",
    [
        (1, 1, True),  # No duplicates, dry-run mode
        (2, 2, True),  # One duplicate, dry-run mode
        (3, 3, True),  # Two duplicates, dry-run mode
        (2, 1, False),  # One duplicate, actual deletion
        (3, 1, False),  # Two duplicates, actual deletion
    ],
)
def test_delete_duplicate_visits(
    opportunity, setup_opportunity_with_duplicates, num_duplicates, expected_remaining, dry_run
):
    xform_id = str(uuid4())
    entity_id = str(uuid4())
    deliver_unit = DeliverUnitFactory()

    first_visit = setup_opportunity_with_duplicates(opportunity, xform_id, entity_id, deliver_unit, num_duplicates)

    out = StringIO()

    if dry_run:
        call_command("delete_duplicate_visits", "--opp", str(opportunity.id), "--dry-run", stdout=out)
    else:
        call_command("delete_duplicate_visits", "--opp", str(opportunity.id), stdout=out)

    remaining_visits = UserVisit.objects.filter(
        opportunity=opportunity, entity_id=entity_id, deliver_unit=deliver_unit, xform_id=xform_id
    )

    # Verify the count of remaining visits matches the expectation
    assert remaining_visits.count() == expected_remaining

    if not dry_run:
        # Ensure the first visit is still present after actual deletion
        assert remaining_visits.filter(id=first_visit.id).exists()
        assert f"Duplicate visits for opportunity {opportunity.id} deleted successfully." in out.getvalue()
    else:
        assert remaining_visits.count() == num_duplicates
        assert f"Dry-run complete for opportunity {opportunity.id}" in out.getvalue()
