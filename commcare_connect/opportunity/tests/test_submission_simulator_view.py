import importlib
import re
from contextlib import contextmanager

import pytest
from django.urls import Resolver404, clear_url_caches, resolve, reverse
from django.utils.html import escape

from commcare_connect.opportunity.models import OpportunityAccess, PaymentUnit, UserVisit
from commcare_connect.opportunity.tests.factories import (
    DeliverUnitFactory,
    OpportunityAccessFactory,
    UserInviteFactory,
)


def _reload_urls():
    import commcare_connect.opportunity.urls
    import config.urls

    importlib.reload(commcare_connect.opportunity.urls)
    importlib.reload(config.urls)
    clear_url_caches()


@contextmanager
def _simulator_urls(settings):
    # The route is registered at import time, so reload the URLconf with the gate on
    original_debug = settings.DEBUG
    settings.DEBUG = True
    settings.ENABLE_TEST_SUBMISSIONS = True
    _reload_urls()
    try:
        yield
    finally:
        # Restore before reloading, or DEBUG-only routes stay registered for later tests
        settings.DEBUG = original_debug
        settings.ENABLE_TEST_SUBMISSIONS = False
        _reload_urls()


@pytest.fixture
def simulator_on(settings):
    with _simulator_urls(settings):
        yield


def test_simulator_urls_restores_urlconf(settings):
    with _simulator_urls(settings):
        resolve("/404/")
    with pytest.raises(Resolver404):
        resolve("/404/")


@pytest.fixture
def page_url(simulator_on, opportunity):
    return reverse("opportunity:test_submissions", args=(opportunity.organization.slug, opportunity.opportunity_id))


def test_page_lists_workers(client, org_user_admin, page_url, mobile_user_with_connect_link):
    client.force_login(org_user_admin)
    response = client.get(page_url)
    assert response.status_code == 200
    assert mobile_user_with_connect_link.name in response.content.decode()
    # datetime-local has no offset and the server runs in UTC, so the label must say so
    assert "Visit time (UTC" in response.content.decode()


def test_post_submits_deliver_form(client, org_user_admin, page_url, opportunity, mobile_user_with_connect_link):
    access = OpportunityAccess.objects.get(user=mobile_user_with_connect_link, opportunity=opportunity)
    deliver_unit = DeliverUnitFactory(
        app=opportunity.deliver_app, payment_unit=PaymentUnit.objects.filter(opportunity=opportunity).first()
    )
    client.force_login(org_user_admin)
    response = client.post(
        page_url,
        {"access": access.pk, "form_type": "deliver", "deliver_unit": deliver_unit.pk, "deliver_block_count": 1},
    )
    assert response.status_code == 200
    visit = UserVisit.objects.get(opportunity_access=access)
    content = response.content.decode()
    assert f"Visit {visit.pk}" in content
    # links to the worker's visit page by UUIDs, which is what that page looks visits up by
    visits_url = reverse(
        "opportunity:user_visits_list", args=(opportunity.organization.slug, opportunity.opportunity_id)
    )
    assert f'href="{visits_url}?user={access.user.user_id}&' in content
    assert f"visit_id={visit.user_visit_id}" in content


def test_page_404s_when_gate_switched_off(client, org_user_admin, page_url, settings):
    settings.ENABLE_TEST_SUBMISSIONS = False
    client.force_login(org_user_admin)
    assert client.get(page_url).status_code == 404


def test_dashboard_menu_links_to_page(client, org_user_admin, page_url, opportunity, mobile_user_with_connect_link):
    # the worker fixture also adds payment units and a budget, so the dashboard counts as set up
    client.force_login(org_user_admin)
    dashboard = reverse("opportunity:detail", args=(opportunity.organization.slug, opportunity.opportunity_id))
    assert page_url in client.get(dashboard).content.decode()


@pytest.mark.parametrize("missing_app", ["learn_app", "deliver_app"])
def test_page_renders_without_app(client, org_user_admin, page_url, opportunity, mobile_user, missing_app):
    setattr(opportunity, missing_app, None)
    opportunity.save()
    client.force_login(org_user_admin)
    response = client.get(page_url)
    assert response.status_code == 200
    assert "no ConnectID link" in response.content.decode()


def test_dashboard_menu_hides_link_when_gate_off(client, org_user_admin, opportunity, mobile_user_with_connect_link):
    client.force_login(org_user_admin)
    dashboard = reverse("opportunity:detail", args=(opportunity.organization.slug, opportunity.opportunity_id))
    response = client.get(dashboard)
    assert response.status_code == 200
    assert "Test submissions" not in response.content.decode()


def _step_url(opportunity):
    return reverse(
        "opportunity:test_submissions_worker_step", args=(opportunity.organization.slug, opportunity.opportunity_id)
    )


def test_step_button_accepts_invite(client, org_user_admin, page_url, opportunity, mobile_user):
    access = OpportunityAccess.objects.get(user=mobile_user, opportunity=opportunity)
    UserInviteFactory(opportunity=opportunity, opportunity_access=access)
    client.force_login(org_user_admin)
    assert "Accept invite" in client.get(page_url).content.decode()
    response = client.post(_step_url(opportunity), {"access": access.pk})
    assert response.status_code == 200
    access.refresh_from_db()
    assert access.accepted
    content = response.content.decode()
    assert "Accepted the invite" in content
    # The worker's whole row is swapped out of band, so its button shows the next step. It must
    # arrive inside a <template>: the response starts with the log entry's <div>, and an HTML
    # parser drops <tr>/<td> tags that follow one, so the row would lose its id and spill its
    # buttons into the log.
    assert re.search(rf'<template>\s*<tr id="worker-row-{access.pk}"\s+hx-swap-oob="true"', content)
    assert "Start learning" in content


def test_step_rejects_worker_from_other_opportunity(client, org_user_admin, page_url, opportunity):
    other_access = OpportunityAccessFactory()
    client.force_login(org_user_admin)
    assert client.post(_step_url(opportunity), {"access": other_access.pk}).status_code == 404


def test_step_button_walks_worker_to_ready(client, org_user_admin, page_url, opportunity, mobile_user):
    access = OpportunityAccess.objects.get(user=mobile_user, opportunity=opportunity)
    UserInviteFactory(opportunity=opportunity, opportunity_access=access)
    client.force_login(org_user_admin)
    learn_username = escape(f"{mobile_user.username.lower()}@{opportunity.learn_app.cc_domain}.commcarehq.org")
    expected = ["Accepted the invite", "Started learning", "Claimed the opportunity"]
    for message in expected:
        content = client.post(_step_url(opportunity), {"access": access.pk}).content.decode()
        assert message in content
        if message == "Started learning":
            # the swapped-in row already has the new link, so Submit works without a reload
            assert learn_username in content
    # the learn and claim steps linked the worker, so the page now shows their usernames for both apps
    page = client.get(page_url).content.decode()
    for app in (opportunity.learn_app, opportunity.deliver_app):
        assert escape(f"{mobile_user.username.lower()}@{app.cc_domain}.commcarehq.org") in page


def test_assessment_score_defaults_to_a_passing_score_of_zero(
    client, org_user_admin, page_url, opportunity, mobile_user
):
    opportunity.learn_app.passing_score = 0
    opportunity.learn_app.save()
    client.force_login(org_user_admin)
    page = client.get(page_url).content.decode()
    assert re.search(r'name="score"\s+value="0"', page)
