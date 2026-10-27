import pytest
from django.urls import reverse

from commcare_connect.opportunity.models import DeliveryType
from commcare_connect.opportunity.tests.factories import OpportunityFactory, UserCredentialFactory
from commcare_connect.program.tests.factories import ProgramFactory
from commcare_connect.solicitation.tests.factories import SolicitationFactory
from commcare_connect.utils.permission_const import ORG_MANAGEMENT_SETTINGS_ACCESS

# The shared form, views and row actions are covered by organization/tests/test_primary_sectors.py; these tests
# cover only what Delivery Types adds on top of them.


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


@pytest.mark.django_db
class TestDeliveryTypeInUse:
    def test_unused(self, delivery_type):
        assert not delivery_type.is_in_use()

    @pytest.mark.parametrize(
        "factory", [OpportunityFactory, ProgramFactory, SolicitationFactory, UserCredentialFactory]
    )
    def test_used(self, delivery_type, factory):
        factory(delivery_type=delivery_type)
        assert delivery_type.is_in_use()


@pytest.mark.django_db
class TestDeliveryTypeViews:
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

    def test_internal_features_shows_card(self, client, settings_admin):
        client.force_login(settings_admin)
        response = client.get(reverse("users:internal_features"))
        assert reverse("users:delivery_type_list") in response.content.decode()
