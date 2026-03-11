import pytest
from django import forms

from commcare_connect.organization.models import LLOEntity
from commcare_connect.utils.forms import (
    TOMSELECT_NEW_ENTRY_PREFIX,
    CreatableModelChoiceField,
    tomselect_resolve_creatable_value,
)


class LLOEntityForm(forms.Form):
    entity = CreatableModelChoiceField(
        queryset=LLOEntity.objects.all(),
        create_key_name="name",
        required=False,
    )


@pytest.mark.django_db
class TestResolveCreatableValue:
    def test_new_prefix_returns_stripped_text(self):
        qs = LLOEntity.objects.all()
        result = tomselect_resolve_creatable_value(TOMSELECT_NEW_ENTRY_PREFIX + "Foo Bar", qs)
        assert result == "Foo Bar"

    def test_new_prefix_double_nested(self):
        # Edge case: User types "new:deal" → JS submits "new:new:deal" → backend strips one prefix
        qs = LLOEntity.objects.all()
        result = tomselect_resolve_creatable_value("new:new:deal", qs)
        assert result == "new:deal"

    def test_existing_pk_returns_instance(self):
        entity = LLOEntity.objects.create(name="Existing Entity")
        qs = LLOEntity.objects.all()
        result = tomselect_resolve_creatable_value(str(entity.pk), qs)
        assert result == entity

    def test_invalid_pk_raises_validation_error(self):
        qs = LLOEntity.objects.all()
        with pytest.raises(forms.ValidationError) as exc_info:
            tomselect_resolve_creatable_value("99999", qs)
        assert exc_info.value.code == "invalid_choice"

    def test_plain_string_raises_validation_error(self):
        # No "new:" prefix and not a valid PK → must raise, not silently return
        qs = LLOEntity.objects.all()
        with pytest.raises(forms.ValidationError) as exc_info:
            tomselect_resolve_creatable_value("SomeName", qs)
        assert exc_info.value.code == "invalid_choice"

    def test_custom_field_name(self):
        entity = LLOEntity.objects.create(name="Named Entity")
        qs = LLOEntity.objects.all()
        result = tomselect_resolve_creatable_value("Named Entity", qs, field_name="name")
        assert result == entity


@pytest.mark.django_db
class TestCreatableModelChoiceField:
    def test_new_prefix_creates_unsaved_instance(self):
        form = LLOEntityForm(data={"entity": "new:Brand New"})
        assert form.is_valid(), form.errors
        result = form.cleaned_data["entity"]
        assert isinstance(result, LLOEntity)
        assert result.pk is None
        assert result.name == "Brand New"

    def test_new_prefix_strips_correctly_leaving_no_prefix(self):
        form = LLOEntityForm(data={"entity": "new:Foo"})
        assert form.is_valid(), form.errors
        assert form.cleaned_data["entity"].name == "Foo"

    def test_existing_pk_returns_existing_instance(self):
        entity = LLOEntity.objects.create(name="Existing")
        form = LLOEntityForm(data={"entity": str(entity.pk)})
        assert form.is_valid(), form.errors
        assert form.cleaned_data["entity"] == entity

    def test_invalid_pk_makes_form_invalid(self):
        form = LLOEntityForm(data={"entity": "99999"})
        assert not form.is_valid()
        assert "entity" in form.errors

    def test_plain_string_makes_form_invalid(self):
        form = LLOEntityForm(data={"entity": "Foo"})
        assert not form.is_valid()
        assert "entity" in form.errors

    def test_new_prefix_with_existing_name_raises_error(self):
        LLOEntity.objects.create(name="Already Exists")
        form = LLOEntityForm(data={"entity": TOMSELECT_NEW_ENTRY_PREFIX + "Already Exists"})
        assert not form.is_valid()
        assert "entity" in form.errors

    def test_to_field_name_respected(self):
        # Ensure to_field_name is passed through to tomselect_resolve_creatable_value
        entity = LLOEntity.objects.create(name="ByName")

        class LLOEntityByNameForm(forms.Form):
            entity = CreatableModelChoiceField(
                queryset=LLOEntity.objects.all(),
                create_key_name="name",
                to_field_name="name",
                required=False,
            )

        form = LLOEntityByNameForm(data={"entity": "ByName"})
        assert form.is_valid(), form.errors
        assert form.cleaned_data["entity"] == entity
