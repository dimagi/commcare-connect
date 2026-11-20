import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from commcare_connect.opportunity.forms import BillToAddressForm
from commcare_connect.opportunity.models import BilledEntity
from commcare_connect.opportunity.tests.factories import BilledEntityFactory, PaymentInvoiceFactory


@pytest.fixture
def default_address(db):
    return BilledEntity.objects.get(is_default=True)


@pytest.fixture
def used_address(db):
    address = BilledEntityFactory(name="Dimagi South Africa", address="1 Long St\nCape Town")
    PaymentInvoiceFactory(bill_to=address)
    return address


@pytest.fixture
def unused_address(db):
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
class TestBillToAddressForm:
    def test_default_cannot_be_deactivated(self, default_address):
        form = BillToAddressForm(instance=default_address, data=_data(name=default_address.name, is_active=""))
        assert form.fields["is_active"].disabled
        assert form.is_valid(), form.errors
        assert form.save().is_active

    @pytest.mark.parametrize("used", [True, False])
    def test_name_and_address_lock_once_used(self, used):
        address = BilledEntityFactory()
        if used:
            PaymentInvoiceFactory(bill_to=address)
        form = BillToAddressForm(instance=address)
        assert form.fields["name"].disabled is used
        assert form.fields["address"].disabled is used

    def test_unused_address_can_be_renamed(self):
        address = BilledEntityFactory(name="Dimagi South Africa")
        form = BillToAddressForm(instance=address, data=_data(name="Renamed", is_active=""))
        assert form.is_valid(), form.errors
        saved = form.save()
        assert (saved.name, saved.is_active) == ("Renamed", False)

    # The tests below post what a page opened before the address's first invoice sends: the fields were still
    # editable then, so the browser includes them.
    @pytest.mark.parametrize("field, value", [("name", "Renamed"), ("address", "2 Other St\r\nCape Town")])
    def test_stale_rename_of_now_used_address_is_rejected(self, used_address, field, value):
        form = BillToAddressForm(instance=used_address, data=_data(**{field: value}, is_active=""))
        assert not form.is_valid()
        assert list(form.errors) == [field]
        used_address.refresh_from_db()
        assert (used_address.name, used_address.address, used_address.is_active) == (
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
    def test_now_used_address_saves_other_changes(self, used_address, omit):
        data = {k: v for k, v in _data(address="1 Long St\r\nCape Town", is_active="").items() if k not in omit}
        form = BillToAddressForm(instance=used_address, data=data)
        assert form.is_valid(), form.errors
        assert not form.save().is_active


@pytest.mark.django_db
class TestBillToAddressViews:
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
    def test_requires_permission(self, user_client, default_address, action, method):
        args = [default_address.pk] if action in ("update", "delete", "make_default") else []
        assert getattr(user_client, method)(reverse(f"users:bill_to_address_{action}", args=args)).status_code == 403

    def test_list_counts_and_in_use(self, settings_client, default_address, used_address):
        unused = BilledEntityFactory()
        response = settings_client.get(reverse("users:bill_to_address_list"))
        assert response.status_code == 200
        rows = {r.record.pk: r.record for r in response.context["table"].rows}
        assert rows[used_address.pk].invoice_count == 1 and rows[used_address.pk].in_use
        assert rows[default_address.pk].in_use
        assert rows[unused.pk].invoice_count == 0 and not rows[unused.pk].in_use

    def test_make_default_button_only_on_other_active_addresses(self, settings_client, default_address):
        expected = {BilledEntityFactory(): True, BilledEntityFactory(is_active=False): False, default_address: False}
        content = settings_client.get(reverse("users:bill_to_address_list")).content.decode()
        for address, shown in expected.items():
            assert (reverse("users:bill_to_address_make_default", args=[address.pk]) in content) is shown

    @pytest.mark.parametrize("is_active", [True, False])
    def test_make_default(self, settings_client, is_active):
        address = BilledEntityFactory(is_active=is_active)
        response = settings_client.post(reverse("users:bill_to_address_make_default", args=[address.pk]))
        assert response.headers["Location"] == reverse("users:bill_to_address_list")
        address.refresh_from_db()
        assert address.is_default is is_active

    def test_create_records_author(self, settings_client, settings_admin):
        response = settings_client.post(reverse("users:bill_to_address_create"), _data(), HTTP_HX_REQUEST="true")
        assert response.headers["HX-Redirect"] == reverse("users:bill_to_address_list")
        created = BilledEntity.objects.get(name="Dimagi South Africa")
        assert created.created_by == settings_admin.email == created.modified_by

    def test_edit_form_renders(self, settings_client, used_address):
        response = settings_client.get(
            reverse("users:bill_to_address_update", args=[used_address.pk]), HTTP_HX_REQUEST="true"
        )
        assert response.status_code == 200
        assert "Used by invoices" in response.content.decode()

    def test_edit_locks_address_before_checking_use(self, settings_client):
        address = BilledEntityFactory()
        with CaptureQueriesContext(connection) as queries:
            settings_client.post(
                reverse("users:bill_to_address_update", args=[address.pk]), _data(), HTTP_HX_REQUEST="true"
            )
        sql = [q["sql"] for q in queries.captured_queries]
        lock = next((i for i, q in enumerate(sql) if "FOR UPDATE" in q and "billedentity" in q), None)
        in_use_check = next((i for i, q in enumerate(sql) if "opportunity_paymentinvoice" in q), None)
        assert lock is not None, "expected a locking SELECT ... FOR UPDATE on billedentity"
        assert in_use_check is not None, "expected a query referencing opportunity_paymentinvoice"
        assert lock < in_use_check

    @pytest.mark.parametrize(
        "address_fixture, deleted",
        [("unused_address", True), ("used_address", False), ("default_address", False)],
    )
    def test_delete(self, settings_client, request, address_fixture, deleted):
        address = request.getfixturevalue(address_fixture)
        settings_client.post(reverse("users:bill_to_address_delete", args=[address.pk]))
        assert BilledEntity.objects.filter(pk=address.pk).exists() is not deleted

    def test_add_form_has_no_slug_autofill(self, settings_client):
        response = settings_client.get(reverse("users:bill_to_address_create"), HTTP_HX_REQUEST="true")
        assert response.status_code == 200
        assert "slugAutofill" not in response.content.decode()

    def test_internal_features_shows_card(self, settings_client):
        assert (
            reverse("users:bill_to_address_list")
            in settings_client.get(reverse("users:internal_features")).content.decode()
        )
