import pytest

from commcare_connect.microplanning.models import ImplementationArea
from commcare_connect.microplanning.services.implementation_areas import clear_implementation_areas_for_opportunity
from commcare_connect.microplanning.tests.factories import ImplementationAreaFactory, WorkAreaFactory


@pytest.mark.django_db
def test_clear_implementation_areas_keeps_work_areas_and_their_area_names(opportunity):
    area = ImplementationAreaFactory(opportunity=opportunity)
    other_area = ImplementationAreaFactory()
    work_area = WorkAreaFactory(opportunity=opportunity, implementation_area=area, implementation_area_name=area.name)

    clear_implementation_areas_for_opportunity(opportunity)

    assert list(ImplementationArea.objects.all()) == [other_area]
    work_area.refresh_from_db()
    assert work_area.implementation_area is None
    assert work_area.implementation_area_name == area.name
