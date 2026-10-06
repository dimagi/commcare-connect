import pytest
from django.contrib.auth.models import Permission
from django.urls import reverse

from commcare_connect.opportunity.models import Country
from commcare_connect.organization.filters import OrganizationFilterSet
from commcare_connect.organization.models import Organization, OrganizationStatus, PrimarySector
from commcare_connect.users.tests.factories import OrganizationFactory, UserFactory


def _filter(params, archived=False):
    organizations = Organization.objects.archived() if archived else Organization.objects.unarchived()
    return set(OrganizationFilterSet(params, queryset=organizations, archived=archived).qs)


@pytest.fixture
def directory_user(db):
    user = UserFactory()
    user.user_permissions.add(Permission.objects.get(codename="workspace_entity_management_access"))
    return user


@pytest.fixture
def sectors(db):
    return {
        "health": PrimarySector.objects.create(name="Health", slug="health", description=""),
        "wash": PrimarySector.objects.create(name="WASH", slug="wash", description=""),
    }


@pytest.fixture
def countries(db):
    return {code: Country.objects.get(code=code) for code in ("KEN", "TZA", "IND")}


@pytest.mark.django_db
class TestOrganizationFilterSet:
    @pytest.mark.parametrize("search", ["umoja", "UCHT", "kenya", "wash"])
    def test_search_matches_name_short_name_country_and_sector(self, search, countries, sectors):
        org = OrganizationFactory(name="Umoja Health Trust", short_name="UCHT")
        org.countries.add(countries["KEN"])
        org.primary_sectors.add(sectors["wash"])
        OrganizationFactory(name="Unrelated")

        assert _filter({"search": search}) == {org}

    def test_multi_select_filters_match_any_value_without_duplicates(self, countries, sectors):
        both = OrganizationFactory()
        both.countries.add(countries["KEN"], countries["TZA"])
        both.primary_sectors.add(sectors["health"], sectors["wash"])
        india = OrganizationFactory()
        india.countries.add(countries["IND"])
        india.primary_sectors.add(sectors["health"])

        by_country = OrganizationFilterSet({"countries": ["KEN", "TZA"]}, queryset=Organization.objects.all()).qs
        assert list(by_country) == [both]
        assert _filter({"primary_sectors": [sectors["health"].pk, sectors["wash"].pk]}) == {both, india}

    def test_status_filter(self):
        active = OrganizationFactory(status=OrganizationStatus.ACTIVE)
        prospective = OrganizationFactory(status=OrganizationStatus.PROSPECTIVE)
        OrganizationFactory(status=OrganizationStatus.INACTIVE)

        assert _filter({"status": ["active", "prospective"]}) == {active, prospective}

    def test_archive_tab_has_no_status_filter(self):
        filterset = OrganizationFilterSet({}, queryset=Organization.objects.archived(), archived=True)
        assert "status" not in filterset.filters


@pytest.mark.django_db
class TestOrganizationListAccess:
    def test_requires_permission(self, client, user):
        client.force_login(user)
        assert client.get(reverse("organization_directory:list")).status_code == 403

    def test_lists_for_permitted_user(self, client, directory_user):
        org = OrganizationFactory()
        client.force_login(directory_user)

        response = client.get(reverse("organization_directory:list"))

        assert response.status_code == 200
        assert list(response.context["table"].data) == [org]
