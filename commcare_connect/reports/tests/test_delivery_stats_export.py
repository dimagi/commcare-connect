import csv
import io
from datetime import UTC, date, datetime
from unittest import mock

import httpx
import pytest
from celery.exceptions import Retry
from django.contrib.auth.models import Permission
from django.contrib.messages import get_messages
from django.contrib.messages.storage.fallback import FallbackStorage
from django.urls import reverse
from django.utils.timezone import now

from commcare_connect.conftest import MobileUserFactory
from commcare_connect.connect_id_client.main import fetch_user_counts
from commcare_connect.reports.delivery_stats_export import (
    add_recipient,
    build_delivery_stats_csv,
    get_eligible_recipients,
    get_filter_values,
    get_recipient_flag,
    get_recipients,
    is_eligible_recipient,
    previous_month_filters,
    remove_recipient,
)
from commcare_connect.reports.forms import AddRecipientForm
from commcare_connect.reports.tables import AdminReportTable
from commcare_connect.reports.tasks import email_delivery_stats_export, send_delivery_stats_export
from commcare_connect.reports.tests.test_reports import _create_kpi_test_data
from commcare_connect.reports.views import queue_delivery_stats_email
from commcare_connect.users.tests.factories import UserFactory
from commcare_connect.utils.test_utils import check_basic_permissions


def _grant_kpi_access(user):
    user.user_permissions.add(Permission.objects.get(codename="kpi_report_access"))
    return user


@pytest.fixture
def kpi_user(db):
    return _grant_kpi_access(UserFactory())


@pytest.fixture
def previous_month():
    return previous_month_filters(now().date())["from_date"]


@pytest.mark.parametrize(
    "today, expected",
    [
        (date(2026, 10, 1), date(2026, 9, 1)),
        (date(2026, 10, 27), date(2026, 9, 1)),
        (date(2027, 1, 1), date(2026, 12, 1)),
        (date(2026, 3, 31), date(2026, 2, 1)),
    ],
)
def test_previous_month_filters(today, expected):
    assert previous_month_filters(today) == {"from_date": expected, "to_date": expected, "period": "monthly"}


@pytest.mark.django_db
def test_build_csv_monthly_has_one_row_with_page_headers(previous_month, httpx_mock):
    _create_kpi_test_data(
        MobileUserFactory.create_batch(2), datetime.combine(previous_month, datetime.min.time(), UTC)
    )
    fetch_user_counts.clear()
    httpx_mock.add_response(method="GET", json={"total_users": {}, "non_invited_users": {}})

    csv_text = build_delivery_stats_csv(previous_month_filters(now().date()))

    rows = list(csv.reader(io.StringIO(csv_text)))
    assert rows[0] == [str(column.header) for column in AdminReportTable([], period="monthly").columns]
    data_rows = [row for row in rows[1:] if row[0] != "Total"]
    assert len(data_rows) == 1
    assert data_rows[0][0] == previous_month.strftime("%B %Y")


@pytest.mark.django_db
def test_get_filter_values_parses_querystring_data(program):
    values = get_filter_values(
        {"from_date": "2026-09", "to_date": "2026-09", "period": "quarterly", "program": str(program.pk)}
    )

    assert values["from_date"] == date(2026, 9, 1)
    assert values["period"] == "quarterly"
    assert values["program"] == program


@pytest.mark.django_db
@pytest.mark.parametrize(
    "setup, eligible",
    [
        ("perm", True),
        ("superuser", True),
        ("no_perm", False),
        ("inactive_with_perm", False),
        ("no_email_with_perm", False),
    ],
)
def test_is_eligible_recipient(setup, eligible):
    user = UserFactory(
        is_superuser=setup == "superuser",
        is_active=setup != "inactive_with_perm",
        email=None if setup == "no_email_with_perm" else "someone@example.com",
    )
    if setup.endswith("perm") and setup != "no_perm":
        _grant_kpi_access(user)

    assert is_eligible_recipient(user) is eligible


@pytest.mark.django_db
def test_recipients_without_flag_are_empty():
    assert get_recipient_flag() is None
    assert get_recipients() == []
    assert get_eligible_recipients() == []


@pytest.mark.django_db
def test_get_eligible_recipients_skips_ineligible(kpi_user):
    no_perm_user = UserFactory()
    add_recipient(kpi_user)
    add_recipient(no_perm_user)

    assert set(get_recipients()) == {kpi_user, no_perm_user}
    assert get_eligible_recipients() == [kpi_user]


def test_add_and_remove_recipient_creates_flag(kpi_user):
    assert get_recipient_flag() is None

    add_recipient(kpi_user)
    assert get_recipient_flag().users.filter(pk=kpi_user.pk).exists()

    remove_recipient(kpi_user)
    assert get_recipients() == []


@pytest.fixture
def empty_report():
    with mock.patch(
        "commcare_connect.reports.delivery_stats_export.get_table_data_for_year_month", return_value=[]
    ) as get_data:
        yield get_data


@pytest.mark.django_db
def test_send_export_without_flag_sends_nothing(mailoutbox, empty_report):
    send_delivery_stats_export()

    assert mailoutbox == []
    empty_report.assert_not_called()


@pytest.mark.django_db
def test_send_export_only_to_eligible_recipients(mailoutbox, empty_report, kpi_user, previous_month):
    add_recipient(kpi_user)
    add_recipient(UserFactory())
    add_recipient(_grant_kpi_access(UserFactory(is_active=False)))

    send_delivery_stats_export()

    assert len(mailoutbox) == 1
    assert mailoutbox[0].to == [kpi_user.email]
    assert mailoutbox[0].subject == f"Delivery Stats — {previous_month:%B %Y}"
    name, _, mimetype = mailoutbox[0].attachments[0]
    assert name == f"delivery-stats-{previous_month:%Y-%m}.csv"
    assert mimetype == "text/csv"
    assert empty_report.call_args.kwargs == previous_month_filters(now().date())


@pytest.mark.django_db
def test_email_monthly_export_goes_only_to_requester(mailoutbox, empty_report, kpi_user):
    add_recipient(_grant_kpi_access(UserFactory()))

    email_delivery_stats_export(kpi_user.pk, None)

    assert [message.to for message in mailoutbox] == [[kpi_user.email]]
    assert empty_report.call_args.kwargs == previous_month_filters(now().date())


@pytest.mark.django_db
def test_email_filtered_export_honors_filters(mailoutbox, empty_report, kpi_user):
    email_delivery_stats_export(kpi_user.pk, {"from_date": "2026-01", "to_date": "2026-03", "period": "quarterly"})

    assert empty_report.call_args.kwargs["period"] == "quarterly"
    assert empty_report.call_args.kwargs["from_date"] == date(2026, 1, 1)
    assert mailoutbox[0].subject == "Delivery Stats export"
    assert mailoutbox[0].attachments[0][0] == f"delivery-stats-export-{now().date():%Y-%m-%d}.csv"
    assert "Period: Quarterly" in mailoutbox[0].body


@pytest.mark.django_db
def test_email_export_to_user_without_email_sends_nothing(mailoutbox, empty_report):
    user = _grant_kpi_access(UserFactory(email=None))

    email_delivery_stats_export(user.pk, None)

    assert mailoutbox == []


@pytest.mark.django_db
@pytest.mark.parametrize(
    "setup, error",
    [
        ("eligible", None),
        ("unknown", "No active Connect user with this email."),
        ("inactive", "No active Connect user with this email."),
        ("no_perm", "This user does not have access to the Delivery Stats report."),
    ],
)
def test_add_recipient_form(setup, error):
    user = UserFactory(email="recipient@example.com", is_active=setup != "inactive")
    if setup != "no_perm":
        _grant_kpi_access(user)
    email = "unknown@example.com" if setup == "unknown" else user.email

    form = AddRecipientForm({"email": email})

    if error:
        assert form.errors["email"] == [error]
    else:
        assert form.is_valid()
        assert form.cleaned_data["email"] == user


def test_add_recipient_form_is_case_insensitive(kpi_user):
    form = AddRecipientForm({"email": kpi_user.email.upper()})

    assert form.is_valid()
    assert form.cleaned_data["email"] == kpi_user


@pytest.mark.django_db
def test_recipients_page_is_gated():
    check_basic_permissions(UserFactory(), reverse("reports:delivery_stats_recipients"), "kpi_report_access")


def test_recipients_page_marks_ineligible_recipients(client, kpi_user):
    no_perm_user = UserFactory()
    add_recipient(kpi_user)
    add_recipient(no_perm_user)
    client.force_login(kpi_user)

    response = client.get(reverse("reports:delivery_stats_recipients"))

    assert set(response.context["recipients"]) == {(kpi_user, True), (no_perm_user, False)}


def test_recipients_page_adds_and_removes(client, kpi_user):
    client.force_login(kpi_user)

    client.post(reverse("reports:delivery_stats_recipients"), {"email": kpi_user.email})
    assert get_recipients() == [kpi_user]

    client.post(reverse("reports:remove_delivery_stats_recipient", args=[kpi_user.pk]))
    assert get_recipients() == []


def _request_for(rf, user):
    request = rf.post("/")
    request.user = user
    request.session = {}
    request._messages = FallbackStorage(request)
    return request


@pytest.mark.parametrize("filter_data", [{"period": "quarterly", "from_date": "2026-01"}, None])
def test_queue_email_dispatches_for_requester(rf, kpi_user, filter_data, django_capture_on_commit_callbacks):
    request = _request_for(rf, kpi_user)
    with mock.patch("commcare_connect.reports.views.email_delivery_stats_export.delay") as delay:
        with django_capture_on_commit_callbacks(execute=True):
            queue_delivery_stats_email(request, filter_data)

    delay.assert_called_once_with(kpi_user.pk, filter_data)
    assert [str(m) for m in get_messages(request)] == [f"The export will be emailed to {kpi_user.email} shortly."]


def test_queue_email_for_user_without_email_queues_nothing(rf, django_capture_on_commit_callbacks):
    user = _grant_kpi_access(UserFactory(email=None))
    request = _request_for(rf, user)
    with mock.patch("commcare_connect.reports.views.email_delivery_stats_export.delay") as delay:
        with django_capture_on_commit_callbacks(execute=True):
            queue_delivery_stats_email(request, None)

    delay.assert_not_called()
    assert [m.level_tag for m in get_messages(request)] == ["error"]


def test_email_view_passes_posted_filters(client, kpi_user):
    client.force_login(kpi_user)
    with mock.patch("commcare_connect.reports.views.queue_delivery_stats_email") as queue:
        response = client.post(
            reverse("reports:email_delivery_stats_view"),
            {"period": "quarterly", "program": "", "csrfmiddlewaretoken": "x"},
        )

    assert response.status_code == 200
    assert queue.call_args.args[1] == {"period": "quarterly", "program": ""}


def test_email_monthly_view_passes_no_filters(client, kpi_user):
    client.force_login(kpi_user)
    with mock.patch("commcare_connect.reports.views.queue_delivery_stats_email") as queue:
        client.post(reverse("reports:email_delivery_stats_monthly"), {"period": "quarterly"})

    assert queue.call_args.args[1] is None


@pytest.mark.django_db
@pytest.mark.parametrize("url_name", ["reports:email_delivery_stats_view", "reports:email_delivery_stats_monthly"])
def test_email_views_require_kpi_access(client, url_name):
    client.force_login(UserFactory())

    assert client.post(reverse(url_name)).status_code == 403


@pytest.mark.django_db
@pytest.mark.parametrize(
    "task, args",
    [(send_delivery_stats_export, ()), (email_delivery_stats_export, ("user", None))],
)
def test_export_tasks_retry_when_connectid_is_unreachable(task, args, kpi_user, mailoutbox):
    add_recipient(kpi_user)
    args = tuple(kpi_user.pk if arg == "user" else arg for arg in args)
    with (
        mock.patch(
            "commcare_connect.reports.helpers.fetch_user_counts", side_effect=httpx.ConnectError("unreachable")
        ),
        mock.patch.object(task, "retry", side_effect=Retry) as retry,
    ):
        with pytest.raises(Retry):
            task(*args)

    retry.assert_called_once()
    assert mailoutbox == []
