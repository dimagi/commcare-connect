import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from commcare_connect.opportunity.forms import DeliveryTypeForm
from commcare_connect.opportunity.models import DeliveryType
from commcare_connect.opportunity.tests.factories import OpportunityFactory, UserCredentialFactory
from commcare_connect.program.tests.factories import ProgramFactory
from commcare_connect.solicitation.tests.factories import SolicitationFactory
from commcare_connect.utils.permission_const import ORG_MANAGEMENT_SETTINGS_ACCESS


@pytest.fixture
def delivery_type(db):
    return DeliveryType.objects.create(name="Nutrition", slug="nutrition", description="Nutrition programs")


@pytest.fixture
def settings_admin(user):
    app_label, codename = ORG_MANAGEMENT_SETTINGS_ACCESS.split(".")
    user.user_permissions.add(
        user.user_permissions.model.objects.get(content_type__app_label=app_label, codename=codename)
    )
    return user


REFERENCING_FACTORIES = [OpportunityFactory, ProgramFactory, SolicitationFactory, UserCredentialFactory]


@pytest.mark.django_db
class TestDeliveryTypeForm:
    def test_create_with_blank_slug_generates_it_from_name(self):
        form = DeliveryTypeForm({"name": "Maternal Health", "slug": "", "description": ""})
        assert form.is_valid(), form.errors
        assert form.save().slug == "maternal-health"

    def test_create_with_custom_slug(self):
        form = DeliveryTypeForm({"name": "Maternal Health", "slug": "mch", "description": ""})
        assert form.is_valid(), form.errors
        assert form.save().slug == "mch"

    @pytest.mark.parametrize("name", ["Nutrition", "nutrition", "  NUTRITION  "])
    def test_rejects_duplicate_name(self, delivery_type, name):
        form = DeliveryTypeForm({"name": name, "slug": "other", "description": ""})
        assert not form.is_valid()
        assert "name" in form.errors

    @pytest.mark.parametrize("slug", ["nutrition", ""])
    def test_rejects_duplicate_slug(self, delivery_type, slug):
        delivery_type.name = "Renamed"
        delivery_type.save()
        form = DeliveryTypeForm({"name": "Nutrition", "slug": slug, "description": ""})
        assert not form.is_valid()
        assert "slug" in form.errors

    @pytest.mark.parametrize("slug", ["Upper", "has space", "dot.ted", "-leading"])
    def test_rejects_invalid_slug(self, slug):
        form = DeliveryTypeForm({"name": "Immunization", "slug": slug, "description": ""})
        assert not form.is_valid()
        assert "slug" in form.errors

    def test_rejects_blank_slug_when_name_has_no_slug(self):
        form = DeliveryTypeForm({"name": "!!!", "slug": "", "description": ""})
        assert not form.is_valid()
        assert "slug" in form.errors

    def test_create_wires_slug_autofill(self):
        form = DeliveryTypeForm()
        assert form.fields["name"].widget.attrs["x-on:input"] == "nameChanged()"
        assert form.fields["slug"].widget.attrs["x-on:input"] == "slugChanged()"

    def test_edit_locks_slug_and_has_no_autofill(self, delivery_type):
        form = DeliveryTypeForm(instance=delivery_type)
        assert form.fields["slug"].disabled
        assert "x-on:input" not in form.fields["name"].widget.attrs

    def test_rename_keeps_slug(self, delivery_type):
        form = DeliveryTypeForm(
            {"name": "Child Nutrition", "slug": "nutrition", "description": "x"}, instance=delivery_type
        )
        assert form.is_valid(), form.errors
        form.save()
        delivery_type.refresh_from_db()
        assert (delivery_type.name, delivery_type.slug) == ("Child Nutrition", "nutrition")

    @pytest.mark.parametrize("slug", ["child-nutrition", "", "Not Valid"])
    def test_edit_ignores_posted_slug(self, delivery_type, slug):
        form = DeliveryTypeForm({"name": "Nutrition", "slug": slug, "description": ""}, instance=delivery_type)
        assert form.is_valid(), form.errors
        form.save()
        delivery_type.refresh_from_db()
        assert delivery_type.slug == "nutrition"


@pytest.mark.django_db
class TestDeliveryTypeInUse:
    def test_unused(self, delivery_type):
        assert not delivery_type.is_in_use()

    @pytest.mark.parametrize("factory", REFERENCING_FACTORIES)
    def test_used(self, delivery_type, factory):
        factory(delivery_type=delivery_type)
        assert delivery_type.is_in_use()


@pytest.mark.django_db
class TestDeliveryTypeViews:
    @pytest.mark.parametrize(
        "url_name, method, needs_pk",
        [
            ("users:delivery_type_list", "get", False),
            ("users:delivery_type_create", "post", False),
            ("users:delivery_type_update", "get", True),
            ("users:delivery_type_delete", "post", True),
        ],
    )
    def test_requires_permission(self, client, user, delivery_type, url_name, method, needs_pk):
        client.force_login(user)
        url = reverse(url_name, args=[delivery_type.pk] if needs_pk else [])
        response = getattr(client, method)(url)
        assert response.status_code == 403

    @pytest.mark.parametrize(
        "url_name, needs_pk, title",
        [
            ("users:delivery_type_create", False, "Add Delivery Type"),
            ("users:delivery_type_update", True, "Edit Delivery Type"),
        ],
    )
    def test_modal_form_renders(self, client, settings_admin, delivery_type, url_name, needs_pk, title):
        client.force_login(settings_admin)
        url = reverse(url_name, args=[delivery_type.pk] if needs_pk else [])
        response = client.get(url, HTTP_HX_REQUEST="true")
        content = response.content.decode()
        assert response.status_code == 200
        assert title in content
        assert f'hx-post="{url}"' in content

    @pytest.mark.parametrize(
        "url_name, needs_pk", [("users:delivery_type_create", False), ("users:delivery_type_update", True)]
    )
    def test_direct_visit_to_modal_form_redirects_to_list(
        self, client, settings_admin, delivery_type, url_name, needs_pk
    ):
        client.force_login(settings_admin)
        response = client.get(reverse(url_name, args=[delivery_type.pk] if needs_pk else []))
        assert response.status_code == 302
        assert response.url == reverse("users:delivery_type_list")

    def test_list_renders(self, client, settings_admin, delivery_type):
        client.force_login(settings_admin)
        response = client.get(reverse("users:delivery_type_list"))
        assert response.status_code == 200
        assert reverse("users:delivery_type_update", args=[delivery_type.pk]) in response.content.decode()

    def test_list_counts_and_in_use(self, client, settings_admin, delivery_type):
        unused = DeliveryType.objects.create(name="Unused", slug="unused", description="")
        ProgramFactory.create_batch(2, delivery_type=delivery_type)
        OpportunityFactory.create_batch(3, delivery_type=delivery_type)
        client.force_login(settings_admin)
        table = client.get(reverse("users:delivery_type_list")).context["table"]
        rows = {row.record.pk: row.record for row in table.rows}
        assert (rows[delivery_type.pk].program_count, rows[delivery_type.pk].opportunity_count) == (2, 3)
        assert (rows[unused.pk].program_count, rows[unused.pk].opportunity_count) == (0, 0)
        assert rows[delivery_type.pk].in_use
        assert not rows[unused.pk].in_use

    @pytest.mark.parametrize("factory", [SolicitationFactory, UserCredentialFactory])
    def test_list_in_use_without_counted_references(self, client, settings_admin, delivery_type, factory):
        factory(delivery_type=delivery_type)
        client.force_login(settings_admin)
        table = client.get(reverse("users:delivery_type_list")).context["table"]
        (record,) = [row.record for row in table.rows if row.record.pk == delivery_type.pk]
        assert record.in_use

    @pytest.mark.parametrize(
        "sort, expected",
        [("opportunity_count", ["Unused", "Nutrition"]), ("-opportunity_count", ["Nutrition", "Unused"])],
    )
    def test_list_sorts_by_opportunity_count(self, client, settings_admin, delivery_type, sort, expected):
        DeliveryType.objects.create(name="Unused", slug="unused", description="")
        OpportunityFactory(delivery_type=delivery_type)
        client.force_login(settings_admin)
        response = client.get(reverse("users:delivery_type_list"), {"sort": sort})
        # Factories can add delivery types of their own; only the order of these two matters.
        names = [row.record.name for row in response.context["table"].rows]
        assert [name for name in names if name in expected] == expected

    def test_list_disables_delete_for_delivery_type_in_use(self, client, settings_admin, delivery_type):
        OpportunityFactory(delivery_type=delivery_type)
        client.force_login(settings_admin)
        content = client.get(reverse("users:delivery_type_list")).content.decode()
        assert (
            'x-tooltip.raw="Used by programs, opportunities, solicitations or worker credentials, so it cannot be '
            'deleted."' in content
        )
        assert "formRef: $el.form" not in content

    def test_delete_confirmation_escapes_name(self, client, settings_admin):
        DeliveryType.objects.create(name="Women's Health", slug="womens-health", description="")
        client.force_login(settings_admin)
        content = client.get(reverse("users:delivery_type_list")).content.decode()
        assert "$store.confirmModal.show(" in content
        assert "Women\\u0027s Health" in content

    def test_create_from_modal_redirects_to_list(self, client, settings_admin):
        client.force_login(settings_admin)
        response = client.post(
            reverse("users:delivery_type_create"),
            {"name": "Immunization", "slug": "", "description": ""},
            HTTP_HX_REQUEST="true",
        )
        assert response.headers["HX-Redirect"] == reverse("users:delivery_type_list")
        assert DeliveryType.objects.filter(slug="immunization").exists()

    def test_create_from_modal_returns_form_with_errors(self, client, settings_admin, delivery_type):
        client.force_login(settings_admin)
        response = client.post(
            reverse("users:delivery_type_create"),
            {"name": "Nutrition", "slug": "", "description": ""},
            HTTP_HX_REQUEST="true",
        )
        assert response.status_code == 200
        assert "HX-Redirect" not in response.headers
        assert 'id="managed-option-form-wrapper"' in response.content.decode()
        assert DeliveryType.objects.filter(name__iexact="nutrition").count() == 1

    def test_edit_from_modal_redirects_to_list(self, client, settings_admin, delivery_type):
        client.force_login(settings_admin)
        response = client.post(
            reverse("users:delivery_type_update", args=[delivery_type.pk]),
            {"name": "Child Nutrition", "slug": "nutrition", "description": ""},
            HTTP_HX_REQUEST="true",
        )
        assert response.headers["HX-Redirect"] == reverse("users:delivery_type_list")
        delivery_type.refresh_from_db()
        assert delivery_type.name == "Child Nutrition"

    def test_delete_unused(self, client, settings_admin, delivery_type):
        client.force_login(settings_admin)
        client.post(reverse("users:delivery_type_delete", args=[delivery_type.pk]))
        assert not DeliveryType.objects.filter(pk=delivery_type.pk).exists()

    def test_delete_locks_delivery_type_before_checking_use(self, client, settings_admin, delivery_type):
        client.force_login(settings_admin)
        with CaptureQueriesContext(connection) as queries:
            client.post(reverse("users:delivery_type_delete", args=[delivery_type.pk]))
        assert any(
            'FROM "opportunity_deliverytype"' in q["sql"] and q["sql"].endswith("FOR UPDATE")
            for q in queries.captured_queries
        )

    @pytest.mark.parametrize("factory", REFERENCING_FACTORIES)
    def test_delete_in_use_is_refused(self, client, settings_admin, delivery_type, factory):
        factory(delivery_type=delivery_type)
        client.force_login(settings_admin)
        client.post(reverse("users:delivery_type_delete", args=[delivery_type.pk]))
        assert DeliveryType.objects.filter(pk=delivery_type.pk).exists()

    def test_internal_features_shows_card(self, client, settings_admin):
        client.force_login(settings_admin)
        response = client.get(reverse("users:internal_features"))
        assert reverse("users:delivery_type_list") in response.content.decode()
