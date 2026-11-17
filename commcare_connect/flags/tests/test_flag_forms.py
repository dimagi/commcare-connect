import pytest

from commcare_connect.flags.forms import FlagForm
from commcare_connect.flags.tests.factories import FlagFactory
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
