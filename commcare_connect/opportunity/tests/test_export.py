import datetime
import random
from datetime import timedelta

import pytest
from django.utils.timezone import now
from tablib import Dataset

from commcare_connect.opportunity.export import (
    UserVisitExporter,
    export_empty_payment_table,
    export_user_status_table,
    export_user_visit_review_data,
)
from commcare_connect.opportunity.models import (
    Opportunity,
    OverLimitReasonChoices,
    UserInviteStatus,
    UserVisit,
    VisitReviewStatus,
    VisitValidationStatus,
)
from commcare_connect.opportunity.tests.factories import (
    AssessmentFactory,
    CompletedModuleFactory,
    DeliverUnitFactory,
    LearnModuleFactory,
    OpportunityAccessFactory,
    OpportunityClaimFactory,
    OpportunityFactory,
    UserInviteFactory,
    UserVisitFactory,
)
from commcare_connect.users.tests.factories import MobileUserFactory


def test_export_user_visit_data(mobile_user_with_connect_link):
    opportunity = OpportunityFactory()
    deliver_units = DeliverUnitFactory.create_batch(2, app=opportunity.deliver_app)
    date1 = now()
    date2 = date1 + timedelta(minutes=10)
    UserVisit.objects.bulk_create(
        [
            UserVisit(
                opportunity=opportunity,
                user=mobile_user_with_connect_link,
                visit_date=date1,
                deliver_unit=deliver_units[0],
                form_json={"form": {"name": "test_form1"}},
            ),
            UserVisit(
                opportunity=opportunity,
                user=mobile_user_with_connect_link,
                visit_date=date2,
                deliver_unit=deliver_units[1],
                entity_id="abc",
                entity_name="A B C",
                form_json={"form": {"name": "test_form2", "group": {"q": "b"}}},
            ),
        ]
    )
    exporter = UserVisitExporter(opportunity, True)
    dataset = exporter.get_dataset(date1, date2, [])
    username = mobile_user_with_connect_link.username
    name = mobile_user_with_connect_link.name

    assert dataset.export("csv") == (
        "Visit ID,Visit date,Status,Username,Name of User,Unit Name,Payment Unit,Over Limit Reason,Rejected Reason,"
        "Justification,Duration,Entity ID,Entity Name,Flags,form.name,form.group.q\r\n"
        f",{date1.isoformat()},Pending,{username},{name},{deliver_units[0].name},,,,,,,,,test_form1,\r\n"
        f",{date2.isoformat()},Pending,{username},{name},{deliver_units[1].name},,,,,,abc,A B C,,test_form2,b\r\n"
    )


def test_export_user_visit_data_disjoint_fields(mobile_user_with_connect_link):
    opportunity = OpportunityFactory()
    deliver_unit = DeliverUnitFactory(app=opportunity.deliver_app)
    date1 = now()
    date2 = date1 + timedelta(minutes=10)
    UserVisit.objects.bulk_create(
        [
            UserVisit(
                opportunity=opportunity,
                user=mobile_user_with_connect_link,
                visit_date=date1,
                deliver_unit=deliver_unit,
                form_json={"form": {"field_a": "value_a"}},
            ),
            UserVisit(
                opportunity=opportunity,
                user=mobile_user_with_connect_link,
                visit_date=date2,
                deliver_unit=deliver_unit,
                form_json={"form": {"field_b": "value_b"}},
            ),
        ]
    )
    exporter = UserVisitExporter(opportunity, True)
    dataset = exporter.get_dataset(date1, date2, [])

    assert "form.field_a" in dataset.headers
    assert "form.field_b" in dataset.headers

    rows = dataset.dict
    row1 = next(r for r in rows if r["form.field_a"] == "value_a")
    row2 = next(r for r in rows if r["form.field_b"] == "value_b")
    assert row1["form.field_b"] == ""
    assert row2["form.field_a"] == ""


def test_export_user_visit_data_no_flatten(mobile_user_with_connect_link):
    opportunity = OpportunityFactory()
    deliver_units = DeliverUnitFactory.create_batch(2, app=opportunity.deliver_app)
    date1 = now()
    date2 = date1 + timedelta(minutes=10)
    form_json_1 = {"form": {"name": "test_form1"}}
    form_json_1_string = '{""form"": {""name"": ""test_form1""}}'
    form_json_2 = {"form": {"name": "test_form2", "group": {"q": "b"}}}
    form_json_2_string = '{""form"": {""name"": ""test_form2"", ""group"": {""q"": ""b""}}}'
    UserVisit.objects.bulk_create(
        [
            UserVisit(
                opportunity=opportunity,
                user=mobile_user_with_connect_link,
                visit_date=date1,
                deliver_unit=deliver_units[0],
                form_json=form_json_1,
            ),
            UserVisit(
                opportunity=opportunity,
                user=mobile_user_with_connect_link,
                visit_date=date2,
                deliver_unit=deliver_units[1],
                entity_id="abc",
                entity_name="A B C",
                form_json=form_json_2,
            ),
        ]
    )
    exporter = UserVisitExporter(opportunity, False)
    dataset = exporter.get_dataset(date1, date2, [])
    username = mobile_user_with_connect_link.username
    name = mobile_user_with_connect_link.name
    assert dataset.export("csv") == (
        "Visit ID,Visit date,Status,Username,Name of User,Unit Name,Payment Unit,Over Limit Reason,Rejected Reason,"
        "Justification,Duration,Entity ID,Entity Name,Flags,form_json\r\n"
        f',{date1.isoformat()},Pending,{username},{name},{deliver_units[0].name},,,,,,,,,"{form_json_1_string}"\r\n'
        f",{date2.isoformat()},Pending,{username},{name},{deliver_units[1].name},,,,,,abc,A B C,,"
        f'"{form_json_2_string}"\r\n'
    )


@pytest.mark.parametrize(
    "status, reasons, expected",
    [
        (
            VisitValidationStatus.over_limit,
            [OverLimitReasonChoices.max_visits, OverLimitReasonChoices.max_daily],
            f"{OverLimitReasonChoices.max_visits.label}; {OverLimitReasonChoices.max_daily.label}",
        ),
        (VisitValidationStatus.over_limit, [], "Not recorded (visit predates over-limit reason tracking)"),
        (VisitValidationStatus.approved, [], ""),
    ],
)
def test_export_user_visit_data_over_limit_columns(opportunity, mobile_user, status, reasons, expected):
    visit = UserVisitFactory(
        opportunity=opportunity,
        user=mobile_user,
        status=status,
        over_limit_reasons=reasons,
        visit_date=now(),
        form_json={"form": {}},
    )

    dataset = UserVisitExporter(opportunity, True).get_dataset(visit.visit_date, visit.visit_date, [])

    [row] = dataset.dict
    assert row["Unit Name"] == visit.deliver_unit.name
    assert row["Payment Unit"] == visit.completed_work.payment_unit.name
    assert row["Over Limit Reason"] == expected


def _get_prepared_dataset_for_user_status_test(data):
    headers = (
        "Name",
        "Username",
        "Status",
        "Started Learning",
        "Completed Learning",
        "Passed Assessment",
        "Job Claimed",
        "Started Delivery",
        "Last visit date",
    )
    return _get_dataset(data, headers)


def _get_dataset(data, headers):
    prepared_dataset = Dataset()
    prepared_dataset.headers = headers
    for row in data:
        prepared_dataset.append(row)
    return prepared_dataset


@pytest.mark.django_db
def test_export_user_status_table_no_data_only(opportunity: Opportunity):
    LearnModuleFactory.create_batch(3, app=opportunity.learn_app)
    mobile_users = MobileUserFactory.create_batch(2)
    rows = []
    for mobile_user in sorted(mobile_users, key=lambda x: x.name):
        date = now()
        _create_accepted_access(opportunity=opportunity, user=mobile_user, date=date)
        rows.append(
            (mobile_user.name, mobile_user.username, "Accepted", date.replace(tzinfo=None), "", False, "", "", "")
        )
    dataset = export_user_status_table(opportunity)
    prepared_test_dataset = _get_prepared_dataset_for_user_status_test(rows)
    assert prepared_test_dataset.export("csv") == dataset.export("csv")


@pytest.mark.django_db
def test_export_user_status_table_learn_data_only(opportunity: Opportunity):
    LearnModuleFactory.create_batch(3, app=opportunity.learn_app)
    mobile_users = MobileUserFactory.create_batch(2)
    rows = []
    for mobile_user in sorted(mobile_users, key=lambda x: x.name):
        date = now()
        access = _create_accepted_access(opportunity=opportunity, user=mobile_user, date=date)
        _complete_learn_modules(access=access, modules=opportunity.learn_app.learn_modules.all()[2:], date=date)
        rows.append(
            (mobile_user.name, mobile_user.username, "Accepted", date.replace(tzinfo=None), "", False, "", "", "")
        )
    dataset = export_user_status_table(opportunity)
    prepared_test_dataset = _get_prepared_dataset_for_user_status_test(rows)
    assert prepared_test_dataset.export("csv") == dataset.export("csv")


@pytest.mark.django_db
def test_export_user_status_table_learn_assessment_data_only(opportunity: Opportunity):
    LearnModuleFactory.create_batch(3, app=opportunity.learn_app)
    mobile_users = MobileUserFactory.create_batch(2)
    rows = []
    for mobile_user in sorted(mobile_users, key=lambda x: x.name):
        date = now()
        access = _create_accepted_access(opportunity=opportunity, user=mobile_user, date=date)
        _complete_learn_modules(access=access, modules=opportunity.learn_app.learn_modules.all(), date=date)
        _pass_assessment(access=access, date=date)
        rows.append(
            (
                mobile_user.name,
                mobile_user.username,
                "Accepted",
                date.replace(tzinfo=None),
                date.replace(tzinfo=None),
                True,
                "",
                "",
                "",
            )
        )
    dataset = export_user_status_table(opportunity)
    prepared_test_dataset = _get_prepared_dataset_for_user_status_test(rows)
    assert prepared_test_dataset.export("csv") == dataset.export("csv")


@pytest.mark.django_db
def test_export_user_status_table_data(opportunity: Opportunity):
    LearnModuleFactory.create_batch(3, app=opportunity.learn_app)
    mobile_users = MobileUserFactory.create_batch(2)
    rows = []
    for mobile_user in sorted(mobile_users, key=lambda x: x.name):
        date = now()
        access = _create_accepted_access(opportunity=opportunity, user=mobile_user, date=date)
        OpportunityClaimFactory(opportunity_access=access, max_payments=10, date_claimed=date)
        _complete_learn_modules(access=access, modules=opportunity.learn_app.learn_modules.all(), date=date)
        _pass_assessment(access=access, date=date)
        UserVisitFactory.create_batch(
            1,
            opportunity=opportunity,
            user=mobile_user,
            visit_date=date,
            opportunity_access=access,
            status=random.choice(["approved", "rejected", "pending"]),
        )
        rows.append(
            (
                mobile_user.name,
                mobile_user.username,
                "Accepted",
                date.replace(tzinfo=None),
                date.replace(tzinfo=None),
                True,
                date.date(),
                date.replace(tzinfo=None),
                date.replace(tzinfo=None),
            )
        )
    dataset = export_user_status_table(opportunity)
    prepared_test_dataset = _get_prepared_dataset_for_user_status_test(rows)
    assert prepared_test_dataset.export("csv") == dataset.export("csv")


def _create_accepted_access(*, opportunity, user, date):
    access = OpportunityAccessFactory(opportunity=opportunity, user=user, accepted=True, date_learn_started=date)
    UserInviteFactory(opportunity=opportunity, status=UserInviteStatus.accepted, opportunity_access=access)
    return access


def _complete_learn_modules(*, access, modules, date):
    for learn_module in modules:
        CompletedModuleFactory(
            module=learn_module, user=access.user, opportunity=access.opportunity, date=date, opportunity_access=access
        )


def _pass_assessment(*, access, date):
    AssessmentFactory(
        app=access.opportunity.learn_app,
        opportunity=access.opportunity,
        user=access.user,
        passed=True,
        date=date,
        opportunity_access=access,
    )


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("from_date", "to_date", "expected_count", "review_status"),
    [
        (now() - timedelta(days=10), now(), 20, [VisitReviewStatus.pending.value]),
        (
            now() - timedelta(days=3),
            now(),
            20,
            [VisitReviewStatus.pending.value, VisitReviewStatus.disagree.value, VisitReviewStatus.agree.value],
        ),
    ],
)
def test_export_user_visit_review_data(organization, from_date, to_date, expected_count, review_status):
    required_headers = [
        "Status",
        "Justification",
        "Visit date",
        "Program Manager Review",
        "Review Requested On",
        "Visit ID",
    ]
    opp = OpportunityFactory(organization=organization)
    now_time = now()
    pending_date = now_time - timedelta(days=3)
    agree_date = now_time - timedelta(days=10)
    disagree_date = now_time - timedelta(days=15)
    UserVisitFactory.create_batch(
        20,
        opportunity=opp,
        visit_date=pending_date,
        review_created_on=pending_date,
        review_status=VisitReviewStatus.pending,
    )
    UserVisitFactory.create_batch(
        5,
        opportunity=opp,
        visit_date=agree_date,
        review_created_on=agree_date,
        review_status=VisitReviewStatus.agree,
    )
    UserVisitFactory.create_batch(
        15,
        opportunity=opp,
        visit_date=disagree_date,
        review_created_on=disagree_date,
        review_status=VisitReviewStatus.disagree,
    )

    dataset = export_user_visit_review_data(opp, from_date, to_date, review_status)

    assert isinstance(dataset, Dataset)
    assert len(dataset.dict) == expected_count
    assert "pk" not in dataset.headers
    assert set(required_headers).issubset(set(dataset.headers))
    for row in dataset.dict:
        assert row["Program Manager Review"] in [
            status.label for status in VisitReviewStatus if status.value in review_status
        ]


@pytest.mark.django_db
def test_export_user_visit_review_data_boundary_dates(organization):
    opp = OpportunityFactory(organization=organization)

    from_date = datetime.date.today() - datetime.timedelta(days=4)
    to_date = datetime.date.today() - datetime.timedelta(days=1)

    on_from_date = datetime.datetime.combine(from_date, datetime.time.min, tzinfo=datetime.UTC)
    on_to_date = datetime.datetime.combine(to_date, datetime.time.max, tzinfo=datetime.UTC)
    before_from_date = datetime.datetime.combine(
        from_date - datetime.timedelta(days=1), datetime.time.max, tzinfo=datetime.UTC
    )
    after_to_date = datetime.datetime.combine(
        to_date + datetime.timedelta(days=1), datetime.time.min, tzinfo=datetime.UTC
    )

    UserVisitFactory.create_batch(
        3,
        opportunity=opp,
        visit_date=on_from_date,
        review_created_on=on_from_date,
        review_status=VisitReviewStatus.pending,
    )
    UserVisitFactory.create_batch(
        2,
        opportunity=opp,
        visit_date=on_to_date,
        review_created_on=on_to_date,
        review_status=VisitReviewStatus.pending,
    )
    UserVisitFactory(
        opportunity=opp,
        visit_date=before_from_date,
        review_created_on=before_from_date,
        review_status=VisitReviewStatus.pending,
    )
    UserVisitFactory(
        opportunity=opp,
        visit_date=after_to_date,
        review_created_on=after_to_date,
        review_status=VisitReviewStatus.pending,
    )

    dataset = export_user_visit_review_data(opp, from_date, to_date, [VisitReviewStatus.pending.value])

    assert isinstance(dataset, Dataset)
    assert len(dataset) == 5, f"Expected 5 visits (3 on from_date + 2 on to_date), got {len(dataset)}"


@pytest.mark.django_db
def test_user_visit_exporter_get_dataset_boundary_dates(opportunity, mobile_user_with_connect_link):
    deliver_unit = DeliverUnitFactory(app=opportunity.deliver_app)

    from_date = datetime.date.today() - datetime.timedelta(days=5)
    to_date = datetime.date.today() - datetime.timedelta(days=1)

    on_from_date = datetime.datetime.combine(from_date, datetime.time.min, tzinfo=datetime.UTC)
    on_to_date = datetime.datetime.combine(to_date, datetime.time.max, tzinfo=datetime.UTC)
    before_from_date = datetime.datetime.combine(
        from_date - datetime.timedelta(days=1), datetime.time.max, tzinfo=datetime.UTC
    )
    after_to_date = datetime.datetime.combine(
        to_date + datetime.timedelta(days=1), datetime.time.min, tzinfo=datetime.UTC
    )

    UserVisitFactory(
        opportunity=opportunity, user=mobile_user_with_connect_link, deliver_unit=deliver_unit, visit_date=on_from_date
    )
    UserVisitFactory(
        opportunity=opportunity, user=mobile_user_with_connect_link, deliver_unit=deliver_unit, visit_date=on_to_date
    )
    UserVisitFactory(
        opportunity=opportunity,
        user=mobile_user_with_connect_link,
        deliver_unit=deliver_unit,
        visit_date=before_from_date,
    )
    UserVisitFactory(
        opportunity=opportunity,
        user=mobile_user_with_connect_link,
        deliver_unit=deliver_unit,
        visit_date=after_to_date,
    )

    exporter = UserVisitExporter(opportunity, False)
    dataset = exporter.get_dataset(from_date, to_date, [])

    assert len(dataset) == 2, f"Expected 2 visits (boundary dates), got {len(dataset)}"


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("currency_code", "accrued_header"),
    [("USD", "Accrued (USD)"), (None, "Accrued")],
)
def test_export_empty_payment_table_includes_accrued(opportunity: Opportunity, currency_code, accrued_header):
    if currency_code is None:
        opportunity.currency = None
        opportunity.save()
    access = OpportunityAccessFactory(opportunity=opportunity, accepted=True, payment_accrued=1250)

    dataset = export_empty_payment_table(opportunity)

    assert dataset.headers == [
        "Username",
        "Phone Number",
        "Name",
        accrued_header,
        "Payment Amount",
        "Payment Date (YYYY-MM-DD)",
        "Payment Method",
        "Payment Operator",
    ]
    assert list(dataset) == [
        (access.user.username, access.user.phone_number, access.user.name, 1250, "", "", "", ""),
    ]
