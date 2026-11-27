from datetime import timedelta

import pytest
from django.utils.timezone import now

from commcare_connect.program.tests.factories import _get_default_country
from commcare_connect.solicitation.forms import (
    CriterionForm,
    QuestionForm,
    SolicitationClosureForm,
    SolicitationForm,
    SolicitationPublishForm,
    publish_errors,
)
from commcare_connect.solicitation.models import Solicitation, SolicitationQuestion
from commcare_connect.solicitation.tests.conftest import deadline
from commcare_connect.solicitation.tests.factories import SolicitationEvaluationCriterionFactory, SolicitationFactory

PUBLISH_ONLY_DATA = {"description": "Scope", "contact_email": "pm@example.com"}


def solicitation_form(data, organization, user, instance=None):
    return SolicitationForm(data, instance=instance, organization=organization, user=user)


@pytest.fixture
def publish_data(draft_data):
    return draft_data | PUBLISH_ONLY_DATA | {"country": _get_default_country().pk}


@pytest.fixture
def active_solicitation(program_manager_org):
    return SolicitationFactory(organization=program_manager_org, status=Solicitation.Status.ACTIVE)


@pytest.mark.django_db
class TestSolicitationForm:
    def test_draft_saves_with_partial_data(self, draft_data, program_manager_org, program_manager_org_user_admin):
        form = solicitation_form(draft_data, program_manager_org, program_manager_org_user_admin)

        assert form.is_valid(), form.errors
        solicitation = form.save()
        assert solicitation.status == Solicitation.Status.DRAFT
        assert solicitation.organization == program_manager_org

    @pytest.mark.parametrize(
        "overrides, field",
        [
            *[({field: ""}, field) for field in ("title", "type", "currency", "application_deadline")],
            ({"budget_min": 100, "budget_max": 50}, "budget_max"),
        ],
    )
    def test_invalid_draft(self, draft_data, overrides, field, program_manager_org, user):
        form = solicitation_form(draft_data | overrides, program_manager_org, user)

        assert field in form.errors

    @pytest.mark.parametrize("field", ["description", "contact_email", "country"])
    def test_active_keeps_publish_fields_required(self, publish_data, field, active_solicitation, user):
        form = solicitation_form(
            publish_data | {field: ""}, active_solicitation.organization, user, instance=active_solicitation
        )

        assert field in form.errors

    @pytest.mark.parametrize("days, valid", [(60, True), (1, False)])
    def test_active_deadline_can_only_be_extended(self, publish_data, days, valid, active_solicitation, user):
        data = publish_data | {"application_deadline": deadline(days)}

        form = solicitation_form(data, active_solicitation.organization, user, instance=active_solicitation)

        assert form.is_valid() is valid

    @pytest.mark.parametrize("hide_scores", [True, False])
    def test_hide_scores_until_submit_persists(self, draft_data, hide_scores, program_manager_org, user):
        data = draft_data | ({"hide_scores_until_submit": "on"} if hide_scores else {})
        form = solicitation_form(data, program_manager_org, user)

        assert form.is_valid(), form.errors
        form.save()
        assert Solicitation.objects.get(pk=form.instance.pk).hide_scores_until_submit is hide_scores


@pytest.mark.django_db
@pytest.mark.parametrize(
    "overrides, weights, blocked_steps",
    [
        ({}, [60, 40], set()),
        ({"description": ""}, [60, 40], {"details"}),
        ({"contact_email": ""}, [60, 40], {"details"}),
        ({"country": None}, [60, 40], {"details"}),
        ({"application_deadline": now() - timedelta(days=1)}, [60, 40], {"details"}),
        ({}, [60, 30], {"criteria"}),
        ({}, [], {"criteria"}),
        ({"description": ""}, [], {"details", "criteria"}),
    ],
)
def test_publish_errors(overrides, weights, blocked_steps):
    solicitation = SolicitationFactory(**{"country": _get_default_country()} | overrides)
    for weight in weights:
        SolicitationEvaluationCriterionFactory(solicitation=solicitation, weight=weight)

    assert set(publish_errors(solicitation)) == blocked_steps


@pytest.mark.django_db
@pytest.mark.parametrize(
    "schedule, go_live_days, valid",
    [
        (False, None, True),
        # Publishing now, so a date left in the hidden field is dropped.
        (False, -1, True),
        (True, None, False),
        (True, 1, True),
        (True, -1, False),
        # The default deadline is 30 days away, so this goes live after applications close.
        (True, 31, False),
    ],
)
def test_publish_form_go_live_date(schedule, go_live_days, valid):
    solicitation = SolicitationFactory(country=_get_default_country())
    SolicitationEvaluationCriterionFactory(solicitation=solicitation, weight=100)
    data = {"go_live_date": "" if go_live_days is None else deadline(go_live_days)}
    if schedule:
        data["schedule"] = "on"

    form = SolicitationPublishForm(data, instance=solicitation)

    assert form.is_valid() is valid
    if valid:
        assert (form.cleaned_data["go_live_date"] is not None) is schedule


@pytest.mark.django_db
@pytest.mark.parametrize(
    "factory_kwargs, weight",
    [
        ({"country": None}, 100),
        ({}, 90),
        # Live already, so there's nothing left to publish.
        ({"status": Solicitation.Status.ACTIVE}, 100),
        ({"scheduled": True, "go_live_date": now() - timedelta(hours=1)}, 100),
    ],
)
def test_publish_form_rejects_what_cant_be_published(factory_kwargs, weight):
    solicitation = SolicitationFactory(**{"country": _get_default_country()} | factory_kwargs)
    SolicitationEvaluationCriterionFactory(solicitation=solicitation, weight=weight)

    assert not SolicitationPublishForm({}, instance=solicitation).is_valid()


@pytest.mark.django_db
@pytest.mark.parametrize("scheduled", [False, True])
def test_publish_form_starts_with_schedule_on_for_a_scheduled_solicitation(scheduled):
    form = SolicitationPublishForm(instance=SolicitationFactory(scheduled=scheduled))

    assert form["schedule"].value() is scheduled


@pytest.mark.django_db
@pytest.mark.parametrize(
    "question_type, options, valid, saved_options",
    [
        (SolicitationQuestion.QuestionType.CHOICE, "Yes\r\nNo", True, ["Yes", "No"]),
        (SolicitationQuestion.QuestionType.CHOICE, "Yes\r\n\r\nNo\r\n", True, ["Yes", "No"]),
        (SolicitationQuestion.QuestionType.CHOICE, "", False, None),
        (SolicitationQuestion.QuestionType.CHOICE, "\r\n\r\n", False, None),
        (SolicitationQuestion.QuestionType.TEXT, "Yes", True, None),
    ],
)
def test_question_choice_options(question_type, options, valid, saved_options):
    data = {"text": "Pick one", "question_type": question_type, "choice_options": options, "required": "on"}
    form = QuestionForm(data, criteria=SolicitationFactory().criteria.all())

    assert form.is_valid() is valid
    if valid:
        assert form.cleaned_data["choice_options"] == saved_options


@pytest.mark.django_db
@pytest.mark.parametrize("question_type", ["", SolicitationQuestion.QuestionType.CHOICE])
def test_question_form_shows_choice_options_only_for_choice(question_type):
    form = QuestionForm(initial={"question_type": question_type}, criteria=SolicitationFactory().criteria.all())
    question_type_wrapper = form.helper.layout[2]

    assert f'x-data="{{&quot;questionType&quot;: &quot;{question_type}&quot;}}"' in question_type_wrapper.flat_attrs
    assert 'x-show="questionType === &#x27;choice&#x27;"' in question_type_wrapper[1].flat_attrs


@pytest.mark.django_db
def test_program_choices_show_names(program, user):
    form = SolicitationForm(organization=program.organization, user=user)

    assert (str(program.pk), program.name) in [(str(value), label) for value, label in form.fields["program"].choices]


@pytest.mark.django_db
def test_program_select_carries_its_delivery_types(program, user):
    form = SolicitationForm(organization=program.organization, user=user)
    program_field = form.helper.layout[5][0]

    assert program_field.attrs["data-delivery-types"] == f"{{&quot;{program.pk}&quot;: {program.delivery_type_id}}}"


@pytest.mark.parametrize("weight, valid", [(0, True), (100, True), (-50, False), (150, False)])
def test_criterion_weight_bounds(weight, valid):
    form = CriterionForm({"label": "Technical expertise", "weight": weight})

    assert form.is_valid() is valid


@pytest.mark.parametrize(
    "reason, valid",
    [("Funding withdrawn", True), ("", False), ("   ", False), ("x" * 150, True), ("x" * 151, False)],
)
def test_closure_reason(reason, valid):
    assert SolicitationClosureForm({"closure_reason": reason}).is_valid() is valid
