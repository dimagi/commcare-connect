from django.test import RequestFactory, override_settings

from commcare_connect.users.context_processors import allauth_settings


class TestAllauthSettings:
    @override_settings(ACCOUNT_ALLOW_REGISTRATION=False)
    def test_returns_registration_setting(self, rf: RequestFactory):
        request = rf.get("/fake-url/")
        assert allauth_settings(request) == {"ACCOUNT_ALLOW_REGISTRATION": False}
