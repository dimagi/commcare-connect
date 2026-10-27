import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from commcare_connect.organization.forms import PrimarySectorForm
from commcare_connect.organization.models import PrimarySector
from commcare_connect.users.tests.factories import OrganizationFactory
from commcare_connect.utils.permission_const import ORG_MANAGEMENT_SETTINGS_ACCESS


@pytest.fixture
def sector(db):
    return PrimarySector.objects.create(name="Health", slug="health", description="Health programs")


@pytest.fixture
def settings_admin(user):
    app_label, codename = ORG_MANAGEMENT_SETTINGS_ACCESS.split(".")
    user.user_permissions.add(
        user.user_permissions.model.objects.get(content_type__app_label=app_label, codename=codename)
    )
    return user


@pytest.mark.django_db
class TestPrimarySectorForm:
    def test_create_with_blank_slug_generates_it_from_name(self):
        form = PrimarySectorForm({"name": "Maternal Health", "slug": "", "description": ""})
        assert form.is_valid(), form.errors
        assert form.save().slug == "maternal-health"

    def test_create_with_custom_slug(self):
        form = PrimarySectorForm({"name": "Maternal Health", "slug": "mch", "description": ""})
        assert form.is_valid(), form.errors
        assert form.save().slug == "mch"

    @pytest.mark.parametrize("name", ["Health", "health", "  HEALTH  "])
    def test_rejects_duplicate_name(self, sector, name):
        form = PrimarySectorForm({"name": name, "slug": "other", "description": ""})
        assert not form.is_valid()
        assert "name" in form.errors

    @pytest.mark.parametrize("slug", ["health", ""])
    def test_rejects_duplicate_slug(self, sector, slug):
        sector.name = "Renamed"
        sector.save()
        form = PrimarySectorForm({"name": "Health", "slug": slug, "description": ""})
        assert not form.is_valid()
        assert "slug" in form.errors

    @pytest.mark.parametrize("slug", ["Upper", "has space", "dot.ted", "-leading"])
    def test_rejects_invalid_slug(self, slug):
        form = PrimarySectorForm({"name": "Education", "slug": slug, "description": ""})
        assert not form.is_valid()
        assert "slug" in form.errors

    def test_rejects_blank_slug_when_name_has_no_slug(self):
        form = PrimarySectorForm({"name": "!!!", "slug": "", "description": ""})
        assert not form.is_valid()
        assert "slug" in form.errors

    def test_create_wires_slug_autofill(self):
        form = PrimarySectorForm()
        assert form.fields["name"].widget.attrs["x-on:input"] == "nameChanged()"
        assert form.fields["slug"].widget.attrs["x-on:input"] == "slugChanged()"

    def test_edit_locks_slug_and_has_no_autofill(self, sector):
        form = PrimarySectorForm(instance=sector)
        assert form.fields["slug"].disabled
        assert "x-on:input" not in form.fields["name"].widget.attrs

    def test_rename_keeps_slug(self, sector):
        form = PrimarySectorForm({"name": "Global Health", "slug": "health", "description": "x"}, instance=sector)
        assert form.is_valid(), form.errors
        form.save()
        sector.refresh_from_db()
        assert (sector.name, sector.slug) == ("Global Health", "health")

    @pytest.mark.parametrize("slug", ["global-health", "", "Not Valid"])
    def test_edit_ignores_posted_slug(self, sector, slug):
        form = PrimarySectorForm({"name": "Health", "slug": slug, "description": ""}, instance=sector)
        assert form.is_valid(), form.errors
        form.save()
        sector.refresh_from_db()
        assert sector.slug == "health"


@pytest.mark.django_db
class TestPrimarySectorInUse:
    def test_unused(self, sector):
        assert not sector.is_in_use()

    def test_used_by_organization(self, sector):
        OrganizationFactory().primary_sectors.add(sector)
        assert sector.is_in_use()


@pytest.mark.django_db
class TestPrimarySectorViews:
    @pytest.mark.parametrize(
        "url_name, method, needs_pk",
        [
            ("users:primary_sector_list", "get", False),
            ("users:primary_sector_create", "post", False),
            ("users:primary_sector_update", "get", True),
            ("users:primary_sector_delete", "post", True),
        ],
    )
    def test_requires_permission(self, client, user, sector, url_name, method, needs_pk):
        client.force_login(user)
        url = reverse(url_name, args=[sector.pk] if needs_pk else [])
        response = getattr(client, method)(url)
        assert response.status_code == 403

    @pytest.mark.parametrize(
        "url_name, needs_pk, title",
        [
            ("users:primary_sector_create", False, "Add Primary Sector"),
            ("users:primary_sector_update", True, "Edit Primary Sector"),
        ],
    )
    def test_modal_form_renders(self, client, settings_admin, sector, url_name, needs_pk, title):
        client.force_login(settings_admin)
        url = reverse(url_name, args=[sector.pk] if needs_pk else [])
        response = client.get(url, HTTP_HX_REQUEST="true")
        content = response.content.decode()
        assert response.status_code == 200
        assert title in content
        assert f'hx-post="{url}"' in content

    @pytest.mark.parametrize(
        "url_name, needs_pk", [("users:primary_sector_create", False), ("users:primary_sector_update", True)]
    )
    def test_direct_visit_to_modal_form_redirects_to_list(self, client, settings_admin, sector, url_name, needs_pk):
        client.force_login(settings_admin)
        response = client.get(reverse(url_name, args=[sector.pk] if needs_pk else []))
        assert response.status_code == 302
        assert response.url == reverse("users:primary_sector_list")

    def test_list_renders(self, client, settings_admin, sector):
        client.force_login(settings_admin)
        response = client.get(reverse("users:primary_sector_list"))
        assert response.status_code == 200
        assert reverse("users:primary_sector_update", args=[sector.pk]) in response.content.decode()

    def test_list_disables_delete_for_sector_in_use(self, client, settings_admin, sector):
        OrganizationFactory().primary_sectors.add(sector)
        client.force_login(settings_admin)
        content = client.get(reverse("users:primary_sector_list")).content.decode()
        assert 'x-tooltip.raw="Used by organizations, so it cannot be deleted."' in content
        assert "formRef: $el.form" not in content

    def test_delete_confirmation_escapes_sector_name(self, client, settings_admin):
        PrimarySector.objects.create(name="Women's Health", slug="womens-health", description="")
        client.force_login(settings_admin)
        content = client.get(reverse("users:primary_sector_list")).content.decode()
        assert "$store.confirmModal.show(" in content
        assert "Women\\u0027s Health" in content

    def test_create_from_modal_redirects_to_list(self, client, settings_admin):
        client.force_login(settings_admin)
        response = client.post(
            reverse("users:primary_sector_create"),
            {"name": "Nutrition", "slug": "", "description": ""},
            HTTP_HX_REQUEST="true",
        )
        assert response.headers["HX-Redirect"] == reverse("users:primary_sector_list")
        assert PrimarySector.objects.filter(slug="nutrition").exists()

    def test_create_from_modal_returns_form_with_errors(self, client, settings_admin, sector):
        client.force_login(settings_admin)
        response = client.post(
            reverse("users:primary_sector_create"),
            {"name": "Health", "slug": "", "description": ""},
            HTTP_HX_REQUEST="true",
        )
        assert response.status_code == 200
        assert "HX-Redirect" not in response.headers
        assert 'id="managed-option-form-wrapper"' in response.content.decode()
        assert PrimarySector.objects.filter(name__iexact="health").count() == 1

    def test_edit_from_modal_redirects_to_list(self, client, settings_admin, sector):
        client.force_login(settings_admin)
        response = client.post(
            reverse("users:primary_sector_update", args=[sector.pk]),
            {"name": "Global Health", "slug": "health", "description": ""},
            HTTP_HX_REQUEST="true",
        )
        assert response.headers["HX-Redirect"] == reverse("users:primary_sector_list")
        sector.refresh_from_db()
        assert sector.name == "Global Health"

    def test_delete_unused(self, client, settings_admin, sector):
        client.force_login(settings_admin)
        client.post(reverse("users:primary_sector_delete", args=[sector.pk]))
        assert not PrimarySector.objects.filter(pk=sector.pk).exists()

    def test_delete_locks_sector_before_checking_use(self, client, settings_admin, sector):
        client.force_login(settings_admin)
        with CaptureQueriesContext(connection) as queries:
            client.post(reverse("users:primary_sector_delete", args=[sector.pk]))
        assert any(
            'FROM "organization_primarysector"' in q["sql"] and q["sql"].endswith("FOR UPDATE")
            for q in queries.captured_queries
        )

    def test_delete_in_use_is_refused(self, client, settings_admin, sector):
        OrganizationFactory().primary_sectors.add(sector)
        client.force_login(settings_admin)
        client.post(reverse("users:primary_sector_delete", args=[sector.pk]))
        assert PrimarySector.objects.filter(pk=sector.pk).exists()

    def test_internal_features_shows_card(self, client, settings_admin):
        client.force_login(settings_admin)
        response = client.get(reverse("users:internal_features"))
        assert reverse("users:primary_sector_list") in response.content.decode()
