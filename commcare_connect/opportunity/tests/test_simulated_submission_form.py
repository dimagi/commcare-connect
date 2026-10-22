from datetime import UTC, datetime

import pytest

from commcare_connect.form_receiver.submission_simulator_types import SubmissionOverrides
from commcare_connect.opportunity.forms import SimulatedSubmissionForm
from commcare_connect.opportunity.models import OpportunityAccess
from commcare_connect.opportunity.tests.factories import (
    DeliverUnitFactory,
    LearnModuleFactory,
    OpportunityAccessFactory,
)


@pytest.fixture
def access(opportunity, mobile_user):
    return OpportunityAccess.objects.get(user=mobile_user, opportunity=opportunity)


def _form(opportunity, **data):
    return SimulatedSubmissionForm(data={"deliver_block_count": 1, **data}, opportunity=opportunity)


def test_deliver_form_is_valid(opportunity, access):
    deliver_unit = DeliverUnitFactory(app=opportunity.deliver_app)
    form = _form(opportunity, access=access.pk, form_type="deliver", deliver_unit=deliver_unit.pk)
    assert form.is_valid(), form.errors


def test_form_rejects_deliver_unit_from_other_opportunity(opportunity, access):
    other_unit = DeliverUnitFactory()
    form = _form(opportunity, access=access.pk, form_type="deliver", deliver_unit=other_unit.pk)
    assert "deliver_unit" in form.errors


def test_form_rejects_access_from_other_opportunity(opportunity):
    other_access = OpportunityAccessFactory()
    deliver_unit = DeliverUnitFactory(app=opportunity.deliver_app)
    form = _form(opportunity, access=other_access.pk, form_type="deliver", deliver_unit=deliver_unit.pk)
    assert "access" in form.errors


def test_form_rejects_learn_module_from_deliver_app(opportunity, access):
    module = LearnModuleFactory(app=opportunity.deliver_app)
    form = _form(opportunity, access=access.pk, form_type="learn", learn_modules=[module.pk])
    assert "learn_modules" in form.errors


@pytest.mark.parametrize(
    "form_type, missing", [("deliver", "deliver_unit"), ("learn", "learn_modules"), ("assessment", "score")]
)
def test_form_requires_fields_per_type(opportunity, access, form_type, missing):
    form = _form(opportunity, access=access.pk, form_type=form_type)
    assert missing in form.errors


def test_get_overrides_maps_fields(opportunity, access):
    form = _form(
        opportunity,
        access=access.pk,
        form_type="assessment",
        score=80,
        entity_id="",
        entity_name="",
        visit_time="2026-01-02T10:00",
        latitude="1.5",
        longitude="2.5",
        deliver_block_count=3,
    )
    assert form.is_valid(), form.errors
    overrides = form.get_overrides()
    assert overrides == SubmissionOverrides(
        entity_id=None,
        entity_name=None,
        visit_time=overrides.visit_time,
        latitude=1.5,
        longitude=2.5,
        deliver_block_count=3,
    )
    assert overrides.visit_time.astimezone(UTC).replace(tzinfo=None) == datetime(2026, 1, 2, 10, 0)


@pytest.mark.parametrize("field", ["entity_id", "entity_name"])
def test_form_rejects_entity_values_too_long_to_store(opportunity, access, field):
    # The columns hold 255; extra deliver blocks append up to 3 characters ("-10")
    form = _form(opportunity, access=access.pk, form_type="assessment", score=80, **{field: "x" * 241})
    assert field in form.errors
