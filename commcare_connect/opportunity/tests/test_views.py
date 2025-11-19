import inspect
from datetime import timedelta
from http import HTTPStatus
from unittest import mock

import pytest
from django.contrib.messages import get_messages
from django.test import Client
from django.urls import get_resolver, reverse
from django.utils.timezone import now

from commcare_connect.connect_id_client.models import ConnectIdUser
from commcare_connect.opportunity.helpers import OpportunityData, TieredQueryset
from commcare_connect.opportunity.models import (
    Opportunity,
    OpportunityAccess,
    OpportunityClaimLimit,
    UserInvite,
    UserInviteStatus,
    VisitValidationStatus,
)
from commcare_connect.opportunity.tasks import invite_user
from commcare_connect.opportunity.tests.factories import (
    OpportunityAccessFactory,
    OpportunityClaimFactory,
    OpportunityClaimLimitFactory,
    OpportunityFactory,
    PaymentFactory,
    PaymentUnitFactory,
    UserInviteFactory,
    UserVisitFactory,
)
from commcare_connect.opportunity.views import WorkerPaymentsView
from commcare_connect.organization.models import Organization
from commcare_connect.program.tests.factories import ManagedOpportunityFactory, ProgramFactory
from commcare_connect.users.models import User
from commcare_connect.users.tests.factories import MembershipFactory, UserFactory


@pytest.mark.django_db
def test_add_budget_existing_users(
    organization: Organization, org_user_member: User, opportunity: Opportunity, mobile_user: User, client: Client
):
    # access = OpportunityAccessFactory(user=user, opportunity=opportunity, accepted=True)
    # claim = OpportunityClaimFactory(end_date=opportunity.end_date, opportunity_access=access)
    payment_units = PaymentUnitFactory.create_batch(2, opportunity=opportunity, amount=1, max_total=100)
    budget_per_user = sum([p.max_total * p.amount for p in payment_units])
    opportunity.total_budget = budget_per_user

    opportunity.organization = organization
    opportunity.save()
    access = OpportunityAccess.objects.get(opportunity=opportunity, user=mobile_user)
    claim = OpportunityClaimFactory(opportunity_access=access, end_date=opportunity.end_date)
    ocl = OpportunityClaimLimitFactory(opportunity_claim=claim, payment_unit=payment_units[0], max_visits=10)
    assert opportunity.total_budget == 200
    assert opportunity.claimed_budget == 10
    end_date = now().date()

    url = reverse("opportunity:add_budget_existing_users", args=(organization.slug, opportunity.pk))
    client.force_login(org_user_member)
    response = client.post(url, data=dict(selected_users=[claim.id], additional_visits=5, end_date=end_date))
    assert response.status_code == 302
    opportunity = Opportunity.objects.get(pk=opportunity.pk)
    assert opportunity.total_budget == 205
    assert opportunity.claimed_budget == 15
    limit = OpportunityClaimLimit.objects.get(pk=ocl.pk)
    assert limit.max_visits == 15
    assert limit.opportunity_claim.end_date == end_date
    assert limit.end_date == end_date


def test_add_budget_existing_users_for_managed_opportunity(
    client, program_manager_org, org_user_admin, organization, mobile_user
):
    payment_per_visit = 5
    org_pay_per_visit = 1
    max_visits_per_user = 10

    budget_per_user = max_visits_per_user * (payment_per_visit + org_pay_per_visit)
    initial_total_budget = budget_per_user * 2

    program = ProgramFactory(organization=program_manager_org, budget=200)
    opportunity = ManagedOpportunityFactory(
        program=program,
        organization=organization,
        total_budget=initial_total_budget,
        org_pay_per_visit=org_pay_per_visit,
    )
    payment_unit = PaymentUnitFactory(opportunity=opportunity, max_total=max_visits_per_user, amount=payment_per_visit)
    access = OpportunityAccessFactory(opportunity=opportunity, user=mobile_user)
    claim = OpportunityClaimFactory(opportunity_access=access, end_date=opportunity.end_date)
    claim_limit = OpportunityClaimLimitFactory(
        opportunity_claim=claim, payment_unit=payment_unit, max_visits=max_visits_per_user
    )

    assert opportunity.total_budget == initial_total_budget
    assert opportunity.claimed_budget == budget_per_user

    url = reverse("opportunity:add_budget_existing_users", args=(opportunity.organization.slug, opportunity.pk))
    client.force_login(org_user_admin)

    additional_visits = 10
    # Budget calculation breakdown: opp_budget=120 Initial_claimed: 60 increase: 60 Final: 120 - Still under opp_budget

    budget_increase = (payment_per_visit + org_pay_per_visit) * additional_visits
    expected_claimed_budget = budget_per_user + budget_increase

    response = client.post(url, data={"selected_users": [claim.id], "additional_visits": additional_visits})
    assert response.status_code == HTTPStatus.FOUND

    opportunity.refresh_from_db()
    claim_limit.refresh_from_db()

    assert opportunity.total_budget == initial_total_budget
    assert opportunity.claimed_budget == expected_claimed_budget
    assert claim_limit.max_visits == max_visits_per_user + additional_visits

    additional_visits = 1
    # Budget calculation breakdown: Previous: claimed 120 increase: 6 final: 126 - Exceeds opp_budget budget of 120

    response = client.post(url, data={"selected_users": [claim.id], "additional_visits": additional_visits})
    assert response.status_code == HTTPStatus.OK
    form = response.context["form"]
    assert "additional_visits" in form.errors
    assert form.errors["additional_visits"][0] == "Additional visits exceed the opportunity budget."


@pytest.mark.parametrize(
    "opportunity",
    [
        {"opp_options": {"managed": True}},
        {"opp_options": {"managed": False}},
    ],
    indirect=True,
)
@pytest.mark.django_db
def test_approve_visit(
    client: Client,
    organization,
    opportunity,
):
    justification = "Justification test."
    access = OpportunityAccessFactory(opportunity=opportunity)
    visit = UserVisitFactory.create(
        opportunity=opportunity, opportunity_access=access, flagged=True, status=VisitValidationStatus.pending
    )
    user = MembershipFactory.create(organization=opportunity.organization).user
    approve_url = reverse("opportunity:approve_visits", args=(opportunity.organization.slug, opportunity.id))
    client.force_login(user)
    response = client.post(approve_url, {"justification": justification, "visit_ids[]": [visit.id]}, follow=True)
    visit.refresh_from_db()
    assert visit.status == VisitValidationStatus.approved
    if opportunity.managed:
        assert justification == visit.justification
    assert response.status_code == HTTPStatus.OK


@pytest.mark.django_db
@pytest.mark.parametrize(
    "filters, expected_count",
    [
        ({}, 4),
        ({"is_test": True}, 1),
        ({"is_test": False}, 3),
        ({"status": [0]}, 2),
        ({"status": [1]}, 1),
        ({"status": [2]}, 1),
        ({"program": ["test-program-1"]}, 1),
        ({"program": ["test-program-2"]}, 1),
        ({"program": ["test-program-1", "test-program-2"]}, 2),
    ],
)
def test_get_opportunity_list_data_all_annotations(organization, filters, expected_count):
    today = now().date()
    three_days_ago = now() - timedelta(days=3)

    program1 = ProgramFactory(organization=organization, name="Test Program 1", slug="test-program-1")
    program2 = ProgramFactory(organization=organization, name="Test Program 2", slug="test-program-2")

    # Active opportunity (status=0)
    opportunity = ManagedOpportunityFactory(
        program=program1,
        organization=organization,
        end_date=today + timedelta(days=1),
        active=True,
        is_test=True,
    )

    # Active opportunity (status=0)
    ManagedOpportunityFactory(
        program=program2,
        organization=organization,
        name="test opportunity 2",
        end_date=today + timedelta(days=1),
        active=True,
        is_test=False,
    )

    # Ended opportunity (status=1)
    OpportunityFactory(
        organization=organization,
        name="test opportunity 3",
        end_date=today - timedelta(days=1),
        active=True,
        is_test=False,
    )

    # Inactive opportunity (status=2)
    OpportunityFactory(
        organization=organization,
        name="test opportunity 4",
        end_date=today + timedelta(days=1),
        active=False,
        is_test=False,
    )

    # Create OpportunityAccesses
    oa1 = OpportunityAccessFactory(opportunity=opportunity, accepted=True, payment_accrued=1000, last_active=now())
    oa2 = OpportunityAccessFactory(
        opportunity=opportunity, accepted=True, payment_accrued=200, last_active=now() - timedelta(4)
    )
    oa3 = OpportunityAccessFactory(
        opportunity=opportunity, accepted=True, payment_accrued=0, last_active=now() - timedelta(4)
    )

    # Payments
    PaymentFactory(opportunity_access=oa1, amount=100, confirmed=True)
    PaymentFactory(opportunity_access=oa2, amount=50, confirmed=True)
    PaymentFactory(opportunity_access=oa1, amount=999, confirmed=False)
    PaymentFactory(opportunity_access=oa3, amount=0, confirmed=True)

    total_paid = 1149
    total_accrued = 1200

    # Invites
    for _ in range(3):
        UserInviteFactory(opportunity=opportunity, status=UserInviteStatus.invited)
    UserInviteFactory(opportunity=opportunity, status=UserInviteStatus.accepted)

    # Visits
    UserVisitFactory(
        opportunity=opportunity,
        opportunity_access=oa1,
        status=VisitValidationStatus.pending,
        visit_date=now(),
        completed_work__opportunity_access=oa1,
    )

    UserVisitFactory(
        opportunity=opportunity,
        opportunity_access=oa2,
        status=VisitValidationStatus.approved,
        visit_date=three_days_ago - timedelta(days=2),
    )

    UserVisitFactory(
        opportunity=opportunity,
        opportunity_access=oa3,
        status=VisitValidationStatus.rejected,
        visit_date=three_days_ago - timedelta(days=2),
    )

    queryset = OpportunityData(organization, False, filters).get_data()
    assert queryset.count() == expected_count
    if not filters:
        opp = queryset[0]
        assert opp.pending_invites == 3
        assert opp.pending_approvals == 1
        assert opp.total_accrued == total_accrued
        assert opp.total_paid == total_paid
        assert opp.payments_due == total_accrued - total_paid
        assert opp.inactive_workers == 2
        assert opp.status == 0


@pytest.mark.django_db
def test_tiered_queryset_basic():
    users = [User.objects.create(username=f"user{i}") for i in range(5)]
    base_qs = User.objects.all()

    def data_qs_fn(ids):
        qs = User.objects.filter(id__in=ids)
        return sorted(qs, key=lambda u: ids.index(u.id))

    tq = TieredQueryset(base_qs, data_qs_fn)

    assert tq.count() == 5

    # Single item access
    first_user = tq[0]
    assert first_user.username == users[0].username

    # Slice access
    sliced = tq[1:3]
    assert [u.username for u in sliced] == [users[1].username, users[2].username]

    # Iteration returns all
    all_users = list(tq)
    assert [u.username for u in all_users] == [u.username for u in users]

    # Order_by works
    tq.order_by("-id")
    desc_users = list(tq[:2])
    expected = list(User.objects.order_by("-id")[:2])
    assert [u.id for u in desc_users] == [u.id for u in expected]

    # Empty slice works
    assert tq[100:105] == []


@pytest.mark.django_db
@pytest.mark.parametrize(
    "referring_url, should_persist",
    [
        ("deliver_tab", True),
        ("/somewhere-else", False),
    ],
)
def test_tab_param_persistence(rf, opportunity, organization, referring_url, should_persist):
    tab_a_url = reverse("opportunity:worker_deliver", args=(organization.slug, opportunity.id))
    tab_b_url = reverse("opportunity:worker_payments", args=(organization.slug, opportunity.id))

    # Step 1: Visit tab A with GET params from any non-tab page
    request_a = rf.get(tab_a_url, {"status": "active"}, HTTP_REFERER="/anywhere-else")
    request_a.session = {}
    view = WorkerPaymentsView()
    view.request = request_a
    _ = view.get_tabs(organization.slug, opportunity)
    assert "worker_tab_params:payments" in request_a.session

    # Step 2: Go to tab B, with referrer varying
    if referring_url == "deliver_tab":
        referrer = tab_a_url
    else:
        referrer = referring_url

    request_b = rf.get(tab_b_url, HTTP_REFERER=referrer)
    request_b.session = request_a.session
    view.request = request_b
    tabs_b = view.get_tabs(organization.slug, opportunity)

    tab_a_link = [t["url"] for t in tabs_b if t["key"] == "payments"][0]

    if should_persist:
        assert "status=active" in tab_a_link
    else:
        assert "status=active" not in tab_a_link


@mock.patch("commcare_connect.opportunity.views.send_event_to_ga")
class TestDeleteUserInvites:
    @pytest.fixture(autouse=True)
    def setup_invites(self, organization, opportunity, org_user_member, client):
        self.client = client

        self.not_found_invites = UserInviteFactory.create_batch(
            2, opportunity=opportunity, status=UserInviteStatus.not_found
        )
        self.invited_invite = UserInviteFactory(opportunity=opportunity, status=UserInviteStatus.invited)
        self.accepted_invite = UserInviteFactory(opportunity=opportunity, status=UserInviteStatus.accepted)

        self.url = reverse("opportunity:delete_user_invites", args=(organization.slug, opportunity.id))
        self.client.force_login(org_user_member)

        self.expected_redirect = reverse("opportunity:worker_list", args=(organization.slug, opportunity.id))

    @pytest.mark.parametrize(
        "test_case,data,expected_status,expected_count,check_redirect",
        [
            ("single_valid_id", lambda self: {"user_invite_ids": [self.not_found_invites[0].id]}, 200, 3, True),
            (
                "multiple_mixed_status",
                lambda self: {
                    "user_invite_ids": [
                        self.not_found_invites[0].id,
                        self.not_found_invites[1].id,
                        self.invited_invite.id,  # Should remain (wrong status)
                        self.accepted_invite.id,  # Should remain (wrong status)
                    ]
                },
                200,
                1,
                True,
            ),
            ("nonexistent_ids", lambda self: {"user_invite_ids": [99999, 88888]}, 200, 4, True),
            ("no_ids_provided", lambda self: {}, 400, 4, False),
            ("empty_ids_list", lambda self: {"user_invite_ids": []}, 400, 4, False),
        ],
    )
    def test_delete_invites(self, mock_send_event, test_case, data, expected_status, expected_count, check_redirect):
        response = self.client.post(self.url, data=data(self))
        assert response.status_code == expected_status

        if check_redirect:
            assert response.headers["HX-Redirect"] == self.expected_redirect

        assert UserInvite.objects.count() == expected_count

    def test_messages(self, mock_send_event):
        response = self.client.post(
            self.url,
            data={"user_invite_ids": [self.not_found_invites[0].id, self.invited_invite.id, self.accepted_invite.id]},
        )
        assert response.status_code == 200
        messages = list(get_messages(response.wsgi_request))
        assert len(messages) == 2
        assert str(messages[0]) == "Successfully deleted 2 invite(s)."
        assert str(messages[1]) == "Cannot delete 1 invite(s). Accepted invites cannot be deleted."


@mock.patch("commcare_connect.opportunity.views.send_event_to_ga")
@pytest.mark.django_db
class TestResendUserInvites:
    @pytest.fixture(autouse=True)
    def setup_invites(self, organization, opportunity, org_user_admin, client):
        self.organization = organization
        self.opportunity = opportunity
        self.client = client

        self.user1 = UserFactory(phone_number="1234567890")
        self.user2 = UserFactory(phone_number="0987654321")

        self.access1 = OpportunityAccessFactory(user=self.user1, opportunity=opportunity)
        self.access2 = OpportunityAccessFactory(user=self.user2, opportunity=opportunity)

        self.recent_invite = UserInviteFactory(
            opportunity=opportunity,
            phone_number=self.user1.phone_number,
            opportunity_access=self.access1,
            status=UserInviteStatus.invited,
            notification_date=now() - timedelta(hours=12),
        )

        self.old_invite = UserInviteFactory(
            opportunity=opportunity,
            phone_number=self.user2.phone_number,
            opportunity_access=self.access2,
            status=UserInviteStatus.sms_delivered,
            notification_date=now() - timedelta(days=2),
        )

        self.not_found_invite = UserInviteFactory(
            opportunity=opportunity,
            phone_number="1111111111",
            status=UserInviteStatus.not_found,
            opportunity_access=None,
        )

        self.url = reverse("opportunity:resend_user_invites", args=(organization.slug, opportunity.id))
        self.client.force_login(org_user_admin)
        self.expected_redirect = reverse("opportunity:worker_list", args=(organization.slug, opportunity.id))

    @mock.patch("commcare_connect.opportunity.tasks.invite_user.delay")
    @mock.patch("commcare_connect.opportunity.tasks.send_message")
    @mock.patch("commcare_connect.opportunity.tasks.send_sms")
    def test_success(self, mock_send_sms, mock_send_message, mock_invite_user, mock_send_event):
        mock_sms_response = mock.Mock()
        mock_sms_response.sid = 1
        mock_send_sms.return_value = mock_sms_response

        def call_task_directly(user_id, access_pk):
            invite_user(user_id, access_pk)

        mock_invite_user.side_effect = call_task_directly
        response = self.client.post(self.url, data={"user_invite_ids": [self.old_invite.id]})

        self.old_invite.refresh_from_db()
        assert response.status_code == 200
        assert response.headers["HX-Redirect"] == self.expected_redirect

        messages = list(get_messages(response.wsgi_request))
        assert len(messages) == 1
        assert str(messages[0]) == "Successfully resent 1 invite(s)."
        assert self.old_invite.status == UserInviteStatus.invited
        assert self.old_invite.notification_date is not None

    def test_no_user_ids(self, mock_send_event):
        response = self.client.post(self.url, data={})
        assert response.status_code == 400

    @mock.patch("commcare_connect.opportunity.tasks.invite_user.delay")
    def test_recent_invite_not_resent(self, mock_invite_user, mock_send_event):
        response = self.client.post(self.url, data={"user_invite_ids": [self.recent_invite.id]})

        assert response.status_code == 200
        mock_invite_user.assert_not_called()
        messages = list(get_messages(response.wsgi_request))
        assert len(messages) == 1
        assert str(messages[0]) == (
            "The following invites were skipped, as they were sent in the last 24 hours: "
            f"{self.recent_invite.phone_number}"
        )

    @mock.patch("commcare_connect.opportunity.views.fetch_users")
    def test_not_found_invite_still_not_found(self, mock_fetch_users, mock_send_event):
        mock_fetch_users.return_value = []
        response = self.client.post(self.url, data={"user_invite_ids": [self.not_found_invite.id]})

        assert response.status_code == 200
        mock_fetch_users.assert_called_once_with(set({self.not_found_invite.phone_number}))
        messages = list(get_messages(response.wsgi_request))
        assert len(messages) == 1
        assert str(messages[0]) == (
            "The following invites were skipped, as they are not registered on "
            f"PersonalID: {self.not_found_invite.phone_number}"
        )

    @mock.patch("commcare_connect.opportunity.views.update_user_and_send_invite")
    @mock.patch("commcare_connect.opportunity.views.fetch_users")
    def test_not_found_invite_with_found_user(self, mock_fetch_users, mock_update_and_send, mock_send_event):
        mock_user = ConnectIdUser(
            name="New User",
            username="newuser",
            phone_number=self.not_found_invite.phone_number,
        )
        mock_fetch_users.return_value = [mock_user]
        response = self.client.post(self.url, data={"user_invite_ids": [self.not_found_invite.id]})

        assert response.status_code == 200
        assert response.headers["HX-Redirect"] == self.expected_redirect
        mock_update_and_send.assert_called_once_with(mock_user, self.opportunity.id)

    @mock.patch("commcare_connect.opportunity.views.update_user_and_send_invite")
    @mock.patch("commcare_connect.opportunity.views.fetch_users")
    def test_org_member_can_resend_invite(
        self, mock_fetch_users, mock_update_and_send, mock_send_event, org_user_member
    ):
        mock_user = ConnectIdUser(
            name="New User",
            username="newuser",
            phone_number=self.not_found_invite.phone_number,
        )
        mock_fetch_users.return_value = [mock_user]

        self.client.force_login(org_user_member)
        response = self.client.post(self.url, data={"user_invite_ids": [self.not_found_invite.id]})

        assert response.status_code == 200
        assert response.headers["HX-Redirect"] == self.expected_redirect


def test_views_use_opportunity_decorator_or_mixin():
    """
    Ensure all views in the opportunity module use the
    @opportunity_required decorator or the OpportunityObjectMixin,
    except explicitly excluded ones.
    The purpose of this test is to prevent new views from being
    added without the necessary authorization checks.
    """
    from commcare_connect.opportunity.views import OpportunityObjectMixin

    def unwrap_view(func):
        """Recursively unwrap all decorators."""
        while hasattr(func, "__wrapped__"):
            func = func.__wrapped__
        return func

    def collect_views():
        """Return both function-based and class-based views from opportunity URLs."""
        resolver = get_resolver("commcare_connect.opportunity.urls")
        function_views = []
        class_views = []

        def collect_patterns(patterns):
            for pattern in patterns:
                if hasattr(pattern, "url_patterns"):  # included URLConf
                    collect_patterns(pattern.url_patterns)
                else:
                    view = pattern.callback
                    pattern_name = getattr(pattern, "name", None)

                    if not pattern_name:
                        continue

                    if hasattr(view, "view_class"):
                        class_views.append(
                            {
                                "url": str(pattern.pattern),
                                "name": pattern_name,
                                "view_class": view.view_class,
                                "class_name": view.view_class.__name__,
                            }
                        )
                    else:
                        original = unwrap_view(view)
                        if not inspect.isclass(original):
                            function_views.append(
                                {
                                    "url": str(pattern.pattern),
                                    "name": pattern_name,
                                    "function": view,
                                    "function_name": original.__name__,
                                }
                            )

        collect_patterns(resolver.url_patterns)
        return function_views, class_views

    def has_opportunity_decorator(func):
        return getattr(func, "_has_opportunity_required_decorator", False)

    def has_opportunity_object_mixin(view_class):
        """Check if a class-based view uses OpportunityObjectMixin."""
        return issubclass(view_class, OpportunityObjectMixin)

    # Exclusion lists with explanations
    function_excluded = {
        "export_status",  # Uses task_id parameter, and similar check is applied directly in code.
        "download_export",  # Uses task_id parameter, and similar check is applied directly in code.
        "fetch_attachment",  # Uses blob_id parameter, not opp_id
        "add_api_key",  # API key management, no opportunity context needed
    }

    class_excluded = {
        "OpportunityList",  # OpportunityList - lists all opportunities, doesn't operate on specific one
        "OpportunityInit",  # OpportunityInit - creates new opportunity, no existing opportunity context
    }

    function_views, class_views = collect_views()

    # Check function-based views
    missing_function_decorator = [
        v
        for v in function_views
        if v["function_name"] not in function_excluded and not has_opportunity_decorator(v["function"])
    ]

    # Check class-based views that operate on opportunities
    missing_mixin = [
        v
        for v in class_views
        if (v["class_name"] not in class_excluded and not has_opportunity_object_mixin(v["view_class"]))
    ]

    # Build error messages
    errors = []

    if missing_function_decorator:
        errors.extend(
            [
                "The following function-based views are missing the `opportunity_required` decorator:",
                *[f"  - {v['name']} ({v['function_name']}) at URL: {v['url']}" for v in missing_function_decorator],
                "All function-based views that operate on opportunities must use `opportunity_required` "
                "decorator. If this view is intentionally excluded, "
                "please add it to the exclusion list (in variable `function_excluded`) in this test.",
            ]
        )

    if missing_mixin:
        if errors:
            errors.append("")  # Add empty line between sections
        errors.extend(
            [
                "The following class-based views are missing the `OpportunityObjectMixin`:",
                *[f"  - {v['name']} ({v['class_name']}) at URL: {v['url']}" for v in missing_mixin],
                "All class-based views that operate on opportunities must inherit from `OpportunityObjectMixin`. "
                "If this view is intentionally excluded, "
                "please add it to the exclusion list (in variable `class_excluded`) in this test.",
            ]
        )

    if errors:
        pytest.fail("\n".join(errors))
