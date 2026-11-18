import pytest
from jsonpath_ng.exceptions import JSONPathError

from commcare_connect.opportunity.forms import FormJsonValidationRulesForm
from commcare_connect.opportunity.models.verification import normalize_question_path, parse_question_path
from commcare_connect.opportunity.tests.factories import DeliverUnitFactory, OpportunityFactory


@pytest.mark.parametrize(
    "question_path, expected",
    [
        ("form.value", "$.form.value"),
        ("$.form.value", "$.form.value"),
        ("/data/value", "$.form.value"),
        ("data/group/value", "$.form.group.value"),
        ("/data/unit1/sk-test-deliver-a/entity_id", '$.form.unit1."sk-test-deliver-a".entity_id'),
    ],
)
def test_normalize_question_path(question_path, expected):
    assert normalize_question_path(question_path) == expected


def test_parse_question_path_finds_value():
    form_json = {"form": {"unit1": {"sk-test-deliver-a": {"entity_id": "123"}}}}
    path = parse_question_path("/data/unit1/sk-test-deliver-a/entity_id")
    assert [m.value for m in path.find(form_json)] == ["123"]


def test_parse_question_path_invalid():
    with pytest.raises(JSONPathError):
        parse_question_path("form.[[")


@pytest.mark.django_db
@pytest.mark.parametrize("question_path, valid", [("/data/answer", True), ("form.answer", True), ("form.[[", False)])
def test_form_json_validation_rules_form_question_path(question_path, valid):
    opportunity = OpportunityFactory()
    deliver_unit = DeliverUnitFactory(app=opportunity.deliver_app, payment_unit=None)
    form = FormJsonValidationRulesForm(
        data={
            "name": "Rule",
            "question_path": question_path,
            "question_value": "yes",
            "deliver_unit": [deliver_unit.pk],
        },
        opportunity=opportunity,
    )
    assert form.is_valid() == valid
    if not valid:
        assert "question_path" in form.errors
