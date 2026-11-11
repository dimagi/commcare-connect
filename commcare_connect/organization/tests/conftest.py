import pytest

from commcare_connect.opportunity.models import Country
from commcare_connect.organization.models import PrimarySector


@pytest.fixture
def sectors(db):
    return {
        "health": PrimarySector.objects.create(name="Health", slug="health", description=""),
        "wash": PrimarySector.objects.create(name="WASH", slug="wash", description=""),
    }


@pytest.fixture
def countries(db):
    return {code: Country.objects.get(code=code) for code in ("KEN", "TZA", "IND")}
