import pytest

from commcare_connect.solicitation.models import SolicitationApplication
from commcare_connect.solicitation.tests.factories import SolicitationApplicationFactory, SolicitationFactory
from commcare_connect.solicitation.views import org_solicitations

Status = SolicitationApplication.Status


@pytest.mark.django_db
def test_org_solicitations_scoped_to_org_with_response_counts(program_manager_org):
    solicitation = SolicitationFactory(organization=program_manager_org)
    for status in Status:
        SolicitationApplicationFactory(solicitation=solicitation, status=status)
    empty = SolicitationFactory(organization=program_manager_org)
    SolicitationApplicationFactory(status=Status.SUBMITTED)  # another org's solicitation

    counts = {s.pk: s.response_count for s in org_solicitations(program_manager_org)}

    # Every status except draft and withdrawn counts as a response.
    assert counts == {solicitation.pk: len(Status) - 2, empty.pk: 0}
