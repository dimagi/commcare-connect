import datetime

import pytest
from django import forms
from django.contrib import messages
from django.http import HttpResponse
from django.shortcuts import redirect
from django.urls import path
from django.utils import timezone

from commcare_connect.simulation.steps.browser import StepRequestError

pytestmark = [pytest.mark.django_db, pytest.mark.urls(__name__)]


class NameForm(forms.Form):
    name = forms.CharField()


def form_page(request):
    form = NameForm(request.POST)
    if form.is_valid():
        return redirect("/done/")
    return HttpResponse(form.errors.as_ul())


def refused(request):
    messages.error(request, "Not allowed for this organization.")
    return HttpResponse(status=400)


def crispy_errors(request):
    return HttpResponse('<p id="error_1_id_total_budget" class="text-red-500"><strong>Budget too high.</strong></p>')


def saved(request):
    messages.success(request, "Saved.")
    return redirect("/done/")


def htmx_done(request):
    return HttpResponse(headers={"HX-Redirect": "/done/"})


def whoami(request):
    return HttpResponse(request.user.email)


urlpatterns = [
    path("form/", form_page),
    path("refused/", refused),
    path("crispy/", crispy_errors),
    path("saved/", saved),
    path("htmx/", htmx_done),
    path("whoami/", whoami),
]


@pytest.fixture
def browser(sim):
    return sim.browser(sim.pm_admin)


def test_acts_as_the_user(browser, sim):
    assert browser.get("/whoami/").content.decode() == sim.pm_admin.email


def test_same_user_shares_one_session(sim):
    assert sim.browser(sim.pm_admin) is sim.browser(sim.pm_admin)


def test_redirect_is_success(browser):
    assert browser.post("/form/", {"name": "x"}).status_code == 302


def test_htmx_redirect_header_is_success(browser):
    assert browser.post("/htmx/", htmx=True).headers["HX-Redirect"] == "/done/"


def test_form_errors_are_raised(browser):
    with pytest.raises(StepRequestError, match="This field is required"):
        browser.post("/form/", {})


def test_messages_are_raised(browser):
    with pytest.raises(StepRequestError, match="400. Not allowed for this organization."):
        browser.post("/refused/")


def test_succeeded_overrides_the_redirect_check(browser):
    response = browser.post("/whoami/", succeeded=lambda response: response.status_code == 200)

    assert response.status_code == 200


def test_crispy_field_errors_are_raised(browser):
    with pytest.raises(StepRequestError, match="Budget too high."):
        browser.post("/crispy/")


def test_earlier_messages_are_not_reported_again(browser):
    browser.post("/saved/")

    with pytest.raises(StepRequestError) as refused:
        browser.post("/refused/")

    assert "Saved." not in str(refused.value)


def test_session_outlasts_the_simulated_history(sim):
    with sim.at(timezone.now() - datetime.timedelta(days=60)):
        browser = sim.browser(sim.pm_admin)

    assert browser.get("/whoami/").content.decode() == sim.pm_admin.email
