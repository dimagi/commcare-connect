import pytest
from django.contrib.auth.models import Permission
from django.db import IntegrityError

from commcare_connect.users.models import ConnectIDUserLink, User, UserCredential
from commcare_connect.users.tests.factories import ConnectIdUserLinkFactory


@pytest.mark.django_db
class TestUser:
    def test_get_absolute_url(self, user: User):
        assert user.get_absolute_url() == "/accounts/email/"

    def test_str_returns_email(self):
        user = User(email="alice@example.com", username="alice")
        assert str(user) == "alice@example.com"

    def test_str_returns_username_when_no_email(self):
        user = User(username="alice")
        assert str(user) == "alice"

    def test_show_internal_features_without_permissions(self, user):
        assert not user.show_internal_features

    @pytest.mark.parametrize(
        "codename",
        ["otp_access", "demo_users_access", "kpi_report_access", "all_org_access", "product_features_access"],
    )
    def test_show_internal_features_with_permission(self, user, codename):
        perm = Permission.objects.get(codename=codename)
        user.user_permissions.add(perm)
        user = User.objects.get(pk=user.pk)
        assert user.show_internal_features


@pytest.mark.django_db
class TestConnectIDUserLink:
    def test_unique_constraint_user_and_commcare_username(self):
        link = ConnectIdUserLinkFactory()
        with pytest.raises(IntegrityError):
            ConnectIDUserLink.objects.create(
                user=link.user,
                commcare_username=link.commcare_username,
            )


class TestUserCredential:
    def test_delivery_level_num_for_delivery(self):
        result = UserCredential.delivery_level_num("25_DELIVERIES", UserCredential.CredentialType.DELIVERY)
        assert result == 25

    def test_delivery_level_num_for_learn(self):
        result = UserCredential.delivery_level_num("LEARN_PASSED", UserCredential.CredentialType.LEARN)
        assert result is None

    def test_get_title_for_learn(self):
        title = UserCredential.get_title(UserCredential.CredentialType.LEARN, "LEARN_PASSED", "Vaccination")
        assert title == "Passed learning assessment for Vaccination"

    def test_get_title_for_delivery(self):
        title = UserCredential.get_title(UserCredential.CredentialType.DELIVERY, "50_DELIVERIES", "Vaccination")
        assert title == "Completed 50 deliveries for Vaccination"
