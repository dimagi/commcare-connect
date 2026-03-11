import pytest
from django import forms

from commcare_connect.organization.models import LLOEntity
from commcare_connect.utils.forms import TOMSELECT_NEW_ENTRY_PREFIX, tomselect_resolve_creatable_value


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
