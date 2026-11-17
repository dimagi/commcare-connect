import pytest

from commcare_connect.flags.forms import FlagForm
from commcare_connect.flags.tests.factories import FlagFactory
from commcare_connect.opportunity.tests.factories import OpportunityFactory
from commcare_connect.program.tests.factories import ProgramFactory
from commcare_connect.users.tests.factories import OrganizationFactory


@pytest.mark.django_db
@pytest.mark.parametrize("already_linked", [False, True])
def test_archived_organizations_are_offered_only_when_already_linked(already_linked):
    active, archived = OrganizationFactory(), OrganizationFactory(is_archived=True)
    flag = FlagFactory()
    if already_linked:
        flag.organizations.add(archived)

    queryset = FlagForm(instance=flag).fields["organizations"].queryset

    assert active in queryset
    assert (archived in queryset) is already_linked


@pytest.mark.django_db
@pytest.mark.parametrize("field", ["programs", "opportunities"])
@pytest.mark.parametrize("already_linked", [False, True])
def test_work_of_archived_organizations_is_offered_only_when_already_linked(field, already_linked):
    active_opportunity = OpportunityFactory()
    archived_opportunity = OpportunityFactory(
        program=ProgramFactory(organization=OrganizationFactory(is_archived=True))
    )
    active, archived = {
        "programs": (active_opportunity.program, archived_opportunity.program),
        "opportunities": (active_opportunity, archived_opportunity),
    }[field]
    flag = FlagFactory()
    if already_linked:
        getattr(flag, field).add(archived)

    queryset = FlagForm(instance=flag).fields[field].queryset

    assert active in queryset
    assert (archived in queryset) is already_linked
