import re

import httpx
import pytest
from crispy_forms.utils import render_crispy_form
from django.urls import reverse

from commcare_connect.commcarehq.tests.factories import HQServerFactory
from commcare_connect.opportunity.forms import (
    HQApiKeyCreateForm,
    OpportunityChangeForm,
    fixed_server_api_key_choices,
    reloaded_api_key_selection,
)
from commcare_connect.opportunity.models import Opportunity
from commcare_connect.opportunity.tests.factories import HQApiKeyFactory, OpportunityAccessFactory
from commcare_connect.opportunity.tests.form_helpers import (
    OpportunityInitFormTestBase,
    change_form,
    mock_hq_apps,
    request_for,
)
from commcare_connect.organization.models import UserOrganizationMembership
from commcare_connect.users.tests.factories import UserFactory


@pytest.mark.django_db
class TestOpportunityInitFormKeyCheck(OpportunityInitFormTestBase):
    @pytest.mark.parametrize(
        "learn_status, deliver_status, expected_errors",
        [
            (200, 200, None),
            (200, 403, ['This API key cannot access the deliver app on domain "{domain}".']),
            (200, 503, ["Could not reach CommCare HQ to check this API key. Please try again."]),
            # A denial is reported over an earlier app's transient failure: retrying wouldn't fix it.
            (503, 403, ['This API key cannot access the deliver app on domain "{domain}".']),
        ],
    )
    def test_checks_the_key_can_reach_the_apps(
        self, opportunity, httpx_mock, learn_status, deliver_status, expected_errors
    ):
        mock_hq_apps(httpx_mock, opportunity, learn_status=learn_status, deliver_status=deliver_status)
        form = self._build_form(opportunity)

        if expected_errors is None:
            assert form.is_valid(), form.errors
        else:
            domain = opportunity.deliver_app.cc_domain
            assert form.errors["api_key"] == [error.format(domain=domain) for error in expected_errors]

    def test_skips_the_hq_check_when_apps_are_invalid(self, opportunity, httpx_mock):
        # httpx_mock has no responses registered, so any HQ request fails the test.
        form = self._build_form(opportunity, extra_data={"deliver_app": self._build_data(opportunity)["learn_app"]})
        assert form.errors["learn_app"] == ["Learn app and Deliver app cannot be same"]


@pytest.mark.django_db
class TestApiKeyOnChangeForm:
    """The Edit page lets opportunity admins replace the HQ API key without touching the apps."""

    @pytest.fixture
    def opp(self, opportunity):
        for app, domain in ((opportunity.learn_app, "learn-domain"), (opportunity.deliver_app, "deliver-domain")):
            app.cc_domain = domain
            app.save(update_fields=["cc_domain"])
        return opportunity

    def _replacement_form(self, opportunity, httpx_mock, deliver_status=200):
        """A form submitting a new key of the admin's own, with HQ answering for both apps."""
        mock_hq_apps(httpx_mock, opportunity, deliver_status=deliver_status)
        request = request_for(opportunity.program.organization, opportunity)
        new_key = HQApiKeyFactory(user=request.user, hq_server=opportunity.hq_server)
        return new_key, change_form(opportunity, request, api_key=str(new_key.id))

    @pytest.mark.parametrize(
        "org, role, shown",
        [
            ("program", UserOrganizationMembership.Role.ADMIN, True),
            ("program", UserOrganizationMembership.Role.MEMBER, False),
            # The delivering org can edit the opportunity, but the key belongs to whoever runs the program.
            ("delivering", UserOrganizationMembership.Role.ADMIN, False),
        ],
    )
    def test_only_program_admins_see_the_field(self, opp, org, role, shown):
        organization = opp.program.organization if org == "program" else opp.organization
        form = OpportunityChangeForm(instance=opp, request=request_for(organization, opp, role))
        assert ("api_key" in form.fields) is shown

    @pytest.mark.parametrize("missing", ["request", "hq_server"])
    def test_absent_without(self, opp, missing):
        request = None if missing == "request" else request_for(opp.program.organization, opp)
        if missing == "hq_server":
            opp.hq_server = None
        form = OpportunityChangeForm(instance=opp, request=request)
        assert "api_key" not in form.fields

    def test_choices_are_current_key_then_own_keys_on_the_server(self, opp):
        request = request_for(opp.program.organization, opp)
        own = HQApiKeyFactory(user=request.user, hq_server=opp.hq_server)
        HQApiKeyFactory(user=request.user)  # other server: excluded
        field = OpportunityChangeForm(instance=opp, request=request).fields["api_key"]
        assert [value for value, _ in field.widget.choices if value] == [opp.api_key_id, own.id]
        assert field.initial == opp.api_key_id
        assert field.widget.attrs["hx-get"] == reverse(
            "program:opportunity_api_key_options",
            args=(request.org.slug, opp.program.program_id, opp.opportunity_id),
        )

    def test_without_a_current_key_nothing_is_preselected(self, opp):
        Opportunity.objects.filter(pk=opp.pk).update(api_key=None)
        opp.refresh_from_db()
        request = request_for(opp.program.organization, opp)
        HQApiKeyFactory(user=request.user, hq_server=opp.hq_server)
        field = OpportunityChangeForm(instance=opp, request=request).fields["api_key"]
        assert field.widget.choices[0] == ("", "Select an API key")

    def test_server_name_is_not_rendered_as_a_template(self, opp):
        opp.hq_server.name = "HQ {{ 1|add:1 }}"
        form = OpportunityChangeForm(instance=opp, request=request_for(opp.program.organization, opp))
        assert "HQ {{ 1|add:1 }}" in render_crispy_form(form)

    def test_replaces_key_after_workers_join(self, opp, httpx_mock):
        OpportunityAccessFactory(opportunity=opp)
        apps = (opp.learn_app_id, opp.deliver_app_id)
        new_key, form = self._replacement_form(opp, httpx_mock)

        assert form.is_valid(), form.errors
        form.save()
        saved = Opportunity.objects.values_list("api_key_id", "learn_app_id", "deliver_app_id").get(pk=opp.pk)
        assert saved == (new_key.id, *apps)

    def test_rejects_key_hq_refuses(self, opp, httpx_mock):
        _, form = self._replacement_form(opp, httpx_mock, deliver_status=403)
        assert form.errors["api_key"] == ['This API key cannot access the deliver app on domain "deliver-domain".']

    @pytest.mark.parametrize("failure", ["timeout", 500, 502, 429])
    def test_reports_hq_failure_without_blaming_the_key(self, opp, httpx_mock, failure):
        if failure == "timeout":
            httpx_mock.add_exception(httpx.ConnectTimeout("timed out"), is_reusable=True)
            request = request_for(opp.program.organization, opp)
            new_key = HQApiKeyFactory(user=request.user, hq_server=opp.hq_server)
            form = change_form(opp, request, api_key=str(new_key.id))
        else:
            _, form = self._replacement_form(opp, httpx_mock, deliver_status=failure)
        assert form.errors["api_key"] == ["Could not reach CommCare HQ to check this API key. Please try again."]

    @pytest.mark.parametrize("case", ["another_users_key", "own_key_on_other_server"])
    def test_rejects_keys_that_are_not_allowed(self, opp, case):
        request = request_for(opp.program.organization, opp)
        owner = {"hq_server": opp.hq_server} if case == "another_users_key" else {"user": request.user}
        form = change_form(opp, request, api_key=str(HQApiKeyFactory(**owner).id))
        assert form.errors["api_key"] == ["Select one of your own API keys for this HQ server."]

    @pytest.mark.parametrize("posted", ["current", "", None])
    def test_keeping_the_key_makes_no_hq_calls(self, opp, httpx_mock, posted):
        # httpx_mock has no responses registered, so any HQ request fails the test.
        overrides = {} if posted is None else {"api_key": str(opp.api_key_id) if posted == "current" else posted}
        form = change_form(opp, request_for(opp.program.organization, opp), **overrides)
        assert form.is_valid(), form.errors
        assert form.save().api_key_id == opp.api_key_id


@pytest.mark.django_db
class TestHQApiKeyCreateForm:
    def test_pinned_server_is_shown_disabled(self):
        server = HQServerFactory(name="Pinned HQ")
        form = HQApiKeyCreateForm(fixed_hq_server=server)
        assert form.fields["hq_server"].disabled
        html = render_crispy_form(form)
        assert re.search(r'<select[^>]*name="hq_server"[^>]*\bdisabled\b', html)
        assert re.search(rf'value="{server.pk}"\s+selected', html)

    @pytest.mark.parametrize("pinned", [True, False])
    def test_another_server_is_ignored_only_when_pinned(self, pinned):
        server, other = HQServerFactory(), HQServerFactory()
        form = HQApiKeyCreateForm(
            data={"hq_server": other.id, "api_key": "a-new-api-key-value"},
            fixed_hq_server=server if pinned else None,
        )
        assert form.is_valid(), form.errors
        assert form.cleaned_data["hq_server"] == (server if pinned else other)


@pytest.mark.parametrize(
    "choices, pending, current_key_id, expected",
    [
        ([(1, "current"), (2, "mine")], "2", 1, "2"),  # the admin's unsaved pick survives the reload
        ([(1, "current"), (2, "mine")], None, 1, "1"),  # nothing sent: the opportunity's key
        ([(1, "current"), (2, "mine")], "99", 1, "1"),  # no longer offered: the opportunity's key
        ([("", "Select an API key"), (2, "mine")], None, None, ""),  # no current key: the placeholder
    ],
)
def test_reloaded_api_key_selection(choices, pending, current_key_id, expected):
    opportunity = Opportunity(api_key_id=current_key_id)
    assert reloaded_api_key_selection(choices, pending, opportunity) == expected


@pytest.mark.django_db
@pytest.mark.parametrize("current_is_own", [False, True])
def test_fixed_server_api_key_choices(opportunity, current_is_own):
    """The current key comes first, labelled with who added it, then the user's other keys on the server, newest
    first."""
    current = opportunity.api_key
    user = current.user if current_is_own else UserFactory()
    older = HQApiKeyFactory(user=user, hq_server=opportunity.hq_server)
    newer = HQApiKeyFactory(user=user, hq_server=opportunity.hq_server)
    HQApiKeyFactory(user=user)  # another server
    owner = current.user
    assert fixed_server_api_key_choices(opportunity, user) == [
        (current.id, f"{current.masked} (added by {owner.name or str(owner)})"),
        (newer.id, newer.masked),
        (older.id, older.masked),
    ]
