from urllib.parse import urlencode

import pytest
from django.contrib.auth.models import Permission
from django.urls import reverse

from commcare_connect.opportunity.models import Country
from commcare_connect.organization.filters import OrganizationFilterSet
from commcare_connect.organization.forms import OrganizationDirectoryForm
from commcare_connect.organization.models import Organization, OrganizationStatus, PrimarySector
from commcare_connect.users.tests.factories import OrganizationFactory, UserFactory


def _filtered_organizations(params, archived=False):
    organizations = Organization.objects.archived() if archived else Organization.objects.unarchived()
    return set(OrganizationFilterSet(params, queryset=organizations, archived=archived).qs)


@pytest.fixture
def user_with_directory_access(db):
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

        assert _filtered_organizations({"search": search}) == {org}

    def test_multi_select_filters_match_any_value_without_duplicates(self, countries, sectors):
        both = OrganizationFactory()
        both.countries.add(countries["KEN"], countries["TZA"])
        both.primary_sectors.add(sectors["health"], sectors["wash"])
        india = OrganizationFactory()
        india.countries.add(countries["IND"])
        india.primary_sectors.add(sectors["health"])

        by_country = OrganizationFilterSet({"countries": ["KEN", "TZA"]}, queryset=Organization.objects.all()).qs
        assert list(by_country) == [both]
        sector_ids = [sectors["health"].pk, sectors["wash"].pk]
        assert _filtered_organizations({"primary_sectors": sector_ids}) == {both, india}

    def test_status_filter(self):
        active = OrganizationFactory(status=OrganizationStatus.ACTIVE)
        prospective = OrganizationFactory(status=OrganizationStatus.PROSPECTIVE)
        OrganizationFactory(status=OrganizationStatus.INACTIVE)

        assert _filtered_organizations({"status": ["active", "prospective"]}) == {active, prospective}

    def test_archive_tab_has_no_status_filter(self):
        filterset = OrganizationFilterSet({}, queryset=Organization.objects.archived(), archived=True)
        assert "status" not in filterset.filters


@pytest.mark.django_db
class TestOrganizationListAccess:
    def test_requires_permission(self, client, user):
        client.force_login(user)
        assert client.get(reverse("organization_directory:list")).status_code == 403

    def test_lists_for_permitted_user(self, client, user_with_directory_access):
        org = OrganizationFactory()
        client.force_login(user_with_directory_access)

        response = client.get(reverse("organization_directory:list"))

        assert response.status_code == 200
        assert list(response.context["table"].data) == [org]


def _form_data(country_by_code, sector_by_key, **overrides):
    data = {
        "name": "Umoja Health Trust",
        "short_name": "UCHT",
        "status": OrganizationStatus.ACTIVE,
        "has_used_connect": "False",
        "countries": [country_by_code["KEN"].pk],
        "regions": "Nyanza",
        "primary_sectors": [sector_by_key["health"].pk],
        "contact_emails": "a@example.com\nb@example.com",
        "latest_msa_link": "https://example.com/msa",
        "eoi_links": "https://example.com/eoi-1\nhttps://example.com/eoi-2",
    }
    data.update(overrides)
    return data


@pytest.mark.django_db
class TestOrganizationDirectoryForm:
    @pytest.mark.parametrize("field", OrganizationDirectoryForm.REQUIRED_FIELDS)
    @pytest.mark.parametrize("editing", [False, True])
    def test_required_fields(self, field, editing, countries, sectors):
        instance = OrganizationFactory() if editing else None
        data = _form_data(countries, sectors, **{field: ""})

        form = OrganizationDirectoryForm(data=data, instance=instance)

        assert not form.is_valid()
        assert field in form.errors

    @pytest.mark.parametrize(
        "current_status, archived_offered",
        [("", False), (OrganizationStatus.ACTIVE, False), (OrganizationStatus.ARCHIVED, True)],
    )
    def test_archived_offered_only_when_already_archived(self, current_status, archived_offered):
        form = OrganizationDirectoryForm(instance=OrganizationFactory(status=current_status))

        offered = [value for value, _label in form.fields["status"].choices]
        assert (OrganizationStatus.ARCHIVED in offered) == archived_offered

    def test_choosing_archived_on_an_unarchived_organization_is_rejected(self, countries, sectors):
        data = _form_data(countries, sectors, status=OrganizationStatus.ARCHIVED)

        form = OrganizationDirectoryForm(data=data, instance=OrganizationFactory())

        assert "status" in form.errors

    def test_saves_all_fields(self, countries, sectors):
        form = OrganizationDirectoryForm(data=_form_data(countries, sectors))

        assert form.is_valid(), form.errors
        org = form.save()

        assert org.status == OrganizationStatus.ACTIVE
        assert org.latest_msa_link == "https://example.com/msa"
        assert list(org.countries.all()) == [countries["KEN"]]
        assert org.contact_emails == "a@example.com\nb@example.com"
        assert org.eoi_links == "https://example.com/eoi-1\nhttps://example.com/eoi-2"

    def test_eoi_links_must_be_urls(self, countries, sectors):
        data = _form_data(countries, sectors, eoi_links="https://example.com/eoi-1\nnot a link")

        form = OrganizationDirectoryForm(data=data)

        assert "not a link" in form.errors["eoi_links"][0]


@pytest.mark.django_db
class TestOrganizationFormViews:
    def test_create_does_not_add_the_staff_member(self, client, user_with_directory_access, countries, sectors):
        client.force_login(user_with_directory_access)

        response = client.post(
            reverse("organization_directory:create"), _form_data(countries, sectors), HTTP_HX_REQUEST="true"
        )

        assert response.status_code == 200
        org = Organization.objects.get(name="Umoja Health Trust")
        assert not org.memberships.exists()

    @pytest.mark.parametrize(
        "next_url, expected",
        [
            ("/organizations/?tab=archive&search=x", "/organizations/?tab=archive&search=x"),
            ("https://evil.example.com/organizations/", "/organizations/"),
            ("/a/some-org/opportunity/", "/organizations/"),
        ],
    )
    def test_edit_returns_to_the_listing_it_came_from(
        self, client, user_with_directory_access, countries, sectors, next_url, expected
    ):
        org = OrganizationFactory()
        client.force_login(user_with_directory_access)
        url = f"{reverse('organization_directory:edit', args=(org.slug,))}?{urlencode({'next': next_url})}"

        response = client.post(url, _form_data(countries, sectors), HTTP_HX_REQUEST="true")

        assert response.headers["HX-Redirect"] == expected

    def test_invalid_submission_rerenders_the_form(self, client, user_with_directory_access, countries, sectors):
        org = OrganizationFactory()
        client.force_login(user_with_directory_access)

        response = client.post(
            reverse("organization_directory:edit", args=(org.slug,)),
            _form_data(countries, sectors, name=""),
            HTTP_HX_REQUEST="true",
        )

        assert response.status_code == 200
        assert "HX-Redirect" not in response.headers
        assert response.context["form"].errors["name"]

    @pytest.mark.parametrize("url_name", ["organization_directory:create", "organization_directory:edit"])
    def test_direct_visit_redirects_to_the_listing(self, client, user_with_directory_access, url_name):
        org = OrganizationFactory()
        client.force_login(user_with_directory_access)
        args = (org.slug,) if url_name.endswith("edit") else ()

        response = client.get(reverse(url_name, args=args))

        assert response.status_code == 302
        assert response.url == reverse("organization_directory:list")

    @pytest.mark.parametrize("url_name", ["organization_directory:create", "organization_directory:edit"])
    def test_requires_permission(self, client, user, url_name):
        org = OrganizationFactory()
        client.force_login(user)
        args = (org.slug,) if url_name.endswith("edit") else ()

        assert client.get(reverse(url_name, args=args)).status_code == 403
