import pytest
from jsonpath_ng.exceptions import JSONPathError

from commcare_connect.opportunity.forms import FormJsonValidationRulesForm
from commcare_connect.opportunity.models.verification import parse_question_path
from commcare_connect.opportunity.tests.factories import DeliverUnitFactory, OpportunityFactory


@pytest.mark.parametrize(
    "question_path, form_json, expected",
    [
        ("form.value", {"form": {"value": "1"}}, ["1"]),
        ("$.form.value", {"form": {"value": "1"}}, ["1"]),
        ("/data/value", {"form": {"value": "1"}}, ["1"]),
        ("data/group/value", {"form": {"group": {"value": "1"}}}, ["1"]),
        (
            "/data/unit1/sk-test-deliver-a/entity_id",
            {"form": {"unit1": {"sk-test-deliver-a": {"entity_id": "1"}}}},
            ["1"],
        ),
        # Slash-path segments are matched literally, including JsonPath special characters.
        ("/data/a\\b", {"form": {"a\\b": "1"}}, ["1"]),
        ("/data/$x/[y]", {"form": {"$x": {"[y]": "1"}}}, ["1"]),
        ("/data/it's/q\"t", {"form": {"it's": {'q"t': "1"}}}, ["1"]),
        ("/data/missing", {"form": {"value": "1"}}, []),
    ],
)
def test_parse_question_path_finds_value(question_path, form_json, expected):
    assert [m.value for m in parse_question_path(question_path).find(form_json)] == expected


@pytest.mark.parametrize("question_path", ["form.[[", "/", "//", " / "])
def test_parse_question_path_invalid(question_path):
    with pytest.raises(JSONPathError):
        parse_question_path(question_path)


@pytest.mark.django_db
@pytest.mark.parametrize(
    "question_path, valid", [("/data/answer", True), ("form.answer", True), ("form.[[", False), ("/", False)]
)
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
