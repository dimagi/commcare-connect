import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from commcare_connect.opportunity.forms import BilledEntityForm
from commcare_connect.opportunity.models import BilledEntity
from commcare_connect.opportunity.tests.factories import BilledEntityFactory, PaymentInvoiceFactory


@pytest.fixture
def default_entity(db):
    return BilledEntity.objects.get(is_default=True)


@pytest.fixture
def used_entity(db):
    entity = BilledEntityFactory(name="Dimagi South Africa", address="1 Long St\nCape Town")
    PaymentInvoiceFactory(bill_to=entity)
    return entity


@pytest.fixture
def unused_entity(db):
    return BilledEntityFactory()


@pytest.fixture
def user_client(client, user):
    client.force_login(user)
    return client


@pytest.fixture
def settings_client(client, settings_admin):
    client.force_login(settings_admin)
    return client


def _data(**overrides):
    return {"name": "Dimagi South Africa", "address": "1 Long St\nCape Town", "is_active": "on", **overrides}


@pytest.mark.django_db
class TestBilledEntityForm:
    def test_default_cannot_be_deactivated(self, default_entity):
        form = BilledEntityForm(instance=default_entity, data=_data(name=default_entity.name, is_active=""))
        assert form.fields["is_active"].disabled
        assert form.is_valid(), form.errors
        assert form.save().is_active

    @pytest.mark.parametrize("used", [True, False])
    def test_name_and_address_lock_once_used(self, used):
        entity = BilledEntityFactory()
        if used:
            PaymentInvoiceFactory(bill_to=entity)
        form = BilledEntityForm(instance=entity)
        assert form.fields["name"].disabled is used
        assert form.fields["address"].disabled is used

    def test_unused_entity_can_be_renamed(self):
        entity = BilledEntityFactory(name="Dimagi South Africa")
        form = BilledEntityForm(instance=entity, data=_data(name="Renamed", is_active=""))
        assert form.is_valid(), form.errors
        saved = form.save()
        assert (saved.name, saved.is_active) == ("Renamed", False)

    # The tests below post what a page opened before the entity's first invoice sends: the fields were still
    # editable then, so the browser includes them.
    @pytest.mark.parametrize("field, value", [("name", "Renamed"), ("address", "2 Other St\r\nCape Town")])
    def test_stale_rename_of_now_used_entity_is_rejected(self, used_entity, field, value):
        form = BilledEntityForm(instance=used_entity, data=_data(**{field: value}, is_active=""))
        assert not form.is_valid()
        assert list(form.errors) == [field]
        used_entity.refresh_from_db()
        assert (used_entity.name, used_entity.address, used_entity.is_active) == (
            "Dimagi South Africa",
            "1 Long St\nCape Town",
            True,
        )

    @pytest.mark.parametrize(
        "omit",
        [
            pytest.param((), id="unchanged-text-with-crlf"),
            pytest.param(("name", "address"), id="disabled-fields-left-out"),
        ],
    )
    def test_now_used_entity_saves_other_changes(self, used_entity, omit):
        data = {k: v for k, v in _data(address="1 Long St\r\nCape Town", is_active="").items() if k not in omit}
        form = BilledEntityForm(instance=used_entity, data=data)
        assert form.is_valid(), form.errors
        assert not form.save().is_active


@pytest.mark.django_db
class TestBilledEntityViews:
    @pytest.mark.parametrize(
        "action, method",
        [
            ("list", "get"),
            ("create", "post"),
            ("update", "get"),
            ("update", "post"),
            ("delete", "post"),
            ("make_default", "post"),
        ],
    )
    def test_requires_permission(self, user_client, default_entity, action, method):
        args = [default_entity.pk] if action in ("update", "delete", "make_default") else []
        assert getattr(user_client, method)(reverse(f"users:billed_entity_{action}", args=args)).status_code == 403

    def test_list_counts_and_in_use(self, settings_client, default_entity, used_entity):
        unused = BilledEntityFactory()
        response = settings_client.get(reverse("users:billed_entity_list"))
        assert response.status_code == 200
        rows = {r.record.pk: r.record for r in response.context["table"].rows}
        assert rows[used_entity.pk].invoice_count == 1 and rows[used_entity.pk].in_use
        assert rows[default_entity.pk].in_use
        assert rows[unused.pk].invoice_count == 0 and not rows[unused.pk].in_use

    def test_make_default_button_only_on_other_active_entities(self, settings_client, default_entity):
        expected = {BilledEntityFactory(): True, BilledEntityFactory(is_active=False): False, default_entity: False}
        content = settings_client.get(reverse("users:billed_entity_list")).content.decode()
        for entity, shown in expected.items():
            assert (reverse("users:billed_entity_make_default", args=[entity.pk]) in content) is shown

    @pytest.mark.parametrize("is_active", [True, False])
    def test_make_default(self, settings_client, is_active):
        entity = BilledEntityFactory(is_active=is_active)
        response = settings_client.post(reverse("users:billed_entity_make_default", args=[entity.pk]))
        assert response.headers["Location"] == reverse("users:billed_entity_list")
        entity.refresh_from_db()
        assert entity.is_default is is_active

    def test_create_records_author(self, settings_client, settings_admin):
        response = settings_client.post(reverse("users:billed_entity_create"), _data(), HTTP_HX_REQUEST="true")
        assert response.headers["HX-Redirect"] == reverse("users:billed_entity_list")
        created = BilledEntity.objects.get(name="Dimagi South Africa")
        assert created.created_by == settings_admin.email == created.modified_by

    def test_edit_form_renders(self, settings_client, used_entity):
        response = settings_client.get(
            reverse("users:billed_entity_update", args=[used_entity.pk]), HTTP_HX_REQUEST="true"
        )
        assert response.status_code == 200
        assert "Used by invoices" in response.content.decode()

    def test_edit_locks_entity_before_checking_use(self, settings_client):
        entity = BilledEntityFactory()
        with CaptureQueriesContext(connection) as queries:
            settings_client.post(
                reverse("users:billed_entity_update", args=[entity.pk]), _data(), HTTP_HX_REQUEST="true"
            )
        sql = [q["sql"] for q in queries.captured_queries]
        lock = next((i for i, q in enumerate(sql) if "FOR UPDATE" in q and "billedentity" in q), None)
        in_use_check = next((i for i, q in enumerate(sql) if "opportunity_paymentinvoice" in q), None)
        assert lock is not None, "expected a locking SELECT ... FOR UPDATE on billedentity"
        assert in_use_check is not None, "expected a query referencing opportunity_paymentinvoice"
        assert lock < in_use_check

    @pytest.mark.parametrize(
        "entity_fixture, deleted",
        [("unused_entity", True), ("used_entity", False), ("default_entity", False)],
    )
    def test_delete(self, settings_client, request, entity_fixture, deleted):
        entity = request.getfixturevalue(entity_fixture)
        settings_client.post(reverse("users:billed_entity_delete", args=[entity.pk]))
        assert BilledEntity.objects.filter(pk=entity.pk).exists() is not deleted

    def test_add_form_has_no_slug_autofill(self, settings_client):
        response = settings_client.get(reverse("users:billed_entity_create"), HTTP_HX_REQUEST="true")
        assert response.status_code == 200
        assert "slugAutofill" not in response.content.decode()

    def test_internal_features_shows_card(self, settings_client):
        assert (
            reverse("users:billed_entity_list")
            in settings_client.get(reverse("users:internal_features")).content.decode()
        )
