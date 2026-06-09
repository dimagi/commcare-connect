import pytest
from rest_framework.test import APIRequestFactory

from commcare_connect.users.api.serializers import UserSerializer
from commcare_connect.users.api.views import UserViewSet
from commcare_connect.users.models import User


class TestUserViewSet:
    @pytest.fixture
    def api_rf(self) -> APIRequestFactory:
        return APIRequestFactory()

    def test_get_queryset(self, user: User, api_rf: APIRequestFactory):
        view = UserViewSet()
        request = api_rf.get("/fake-url/")
        request.user = user

        view.request = request

        assert user in view.get_queryset()

    def test_me(self, user: User, api_rf: APIRequestFactory):
        view = UserViewSet()
        request = api_rf.get("/fake-url/")
        request.user = user

        view.request = request

        response = view.me(request)  # type: ignore

        assert response.data == {
            "url": f"http://testserver/api/users/{user.pk}/",
            "name": user.name,
            "email": user.email,
        }

    def test_email_is_read_only(self, user: User):
        original_email = user.email
        serializer = UserSerializer(user, data={"email": "new@example.com", "name": user.name}, partial=True)
        assert serializer.is_valid(), serializer.errors
        serializer.save()
        user.refresh_from_db()
        assert user.email == original_email
