import pytest
from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib.auth.models import AnonymousUser
from django.http import Http404, HttpResponse
from django.test import RequestFactory
from django.views import View
from waffle.testutils import override_switch

from commcare_connect.flags.switch_names import SOLICITATIONS
from commcare_connect.solicitation.decorators import SolicitationsRequiredMixin, solicitations_required


@solicitations_required
def function_view(request):
    return HttpResponse("ok")


class ClassView(SolicitationsRequiredMixin, LoginRequiredMixin, View):
    def get(self, request):
        return HttpResponse("ok")


@pytest.fixture
def request_for(user):
    request = RequestFactory().get("/")
    request.user = user
    return request


@pytest.mark.django_db
@pytest.mark.parametrize("view", [function_view, ClassView.as_view()])
class TestSolicitationsGate:
    def test_switch_on_serves_view(self, view, request_for):
        with override_switch(SOLICITATIONS, active=True):
            response = view(request_for)
        assert response.status_code == 200

    def test_switch_off_404s(self, view, request_for):
        with override_switch(SOLICITATIONS, active=False):
            with pytest.raises(Http404):
                view(request_for)

    def test_switch_missing_404s(self, view, request_for):
        # Without a Switch row waffle treats the switch as off, so an unconfigured
        # environment keeps the module hidden.
        with pytest.raises(Http404):
            view(request_for)


@pytest.mark.django_db
def test_mixin_404s_before_login_redirect():
    request = RequestFactory().get("/")
    request.user = AnonymousUser()
    with override_switch(SOLICITATIONS, active=False):
        with pytest.raises(Http404):
            ClassView.as_view()(request)
