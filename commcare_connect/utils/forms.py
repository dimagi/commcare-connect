from django import forms

TOMSELECT_NEW_ENTRY_PREFIX = "new:"


def tomselect_resolve_creatable_value(value, queryset, field_name="pk"):
    """
    Resolves a value from a creatable TomSelect field.

    Returns:
      - str: stripped new-entry text (when value starts with TOMSELECT_NEW_ENTRY_PREFIX)
      - model instance: existing record (when value is a PK or field match)

    Raises ValidationError if value is not prefixed and no matching record is found.
    """
    if value.startswith(TOMSELECT_NEW_ENTRY_PREFIX):
        return value[len(TOMSELECT_NEW_ENTRY_PREFIX) :]  # noqa: E203
    try:
        return queryset.get(**{field_name: value})
    except (ValueError, TypeError, queryset.model.DoesNotExist):
        raise forms.ValidationError(
            "Select a valid choice. %(value)s is not one of the available choices.",
            code="invalid_choice",
            params={"value": value},
        )


class CreatableModelChoiceField(forms.ModelChoiceField):
    """Dropdown field that can be used for creating new choices using the TomSelect library.
    This takes an already present Option on the Dropdown or a new user specified String value
    that will be used to create the object."""

    def __init__(self, *args, create_key_name, **kwargs):
        super().__init__(*args, **kwargs)
        self.create_key_name = create_key_name
        self.widget.attrs.update({"data-tomselect": "1", "data-tomselect:settings": '{"create": true}'})

    def to_python(self, value):
        if value in self.empty_values:
            return None
        try:
            key = self.to_field_name or "pk"
            if isinstance(value, self.queryset.model):
                value = getattr(value, key)
            return self.queryset.get(**{key: value})
        except (ValueError, TypeError, self.queryset.model.DoesNotExist):
            # TomSelect uses the same field for existing IDs and new text values.
            # If lookup fails, we assume this is a newly created entry and return the raw value.
            # Numeric-only names may conflict with existing PKs, but this is an accepted limitation for now.
            # Frontend prefixing (e.g. "id:123") would fully resolve this if needed.
            return value

    def validate(self, value):
        if isinstance(value, str):
            # Skip queryset validation for new string values used for creation.
            return
        return super().validate(value)

    def clean(self, value):
        value = super().clean(value)
        if isinstance(value, str):
            value = value.strip()
            if not value:
                if self.required:
                    raise forms.ValidationError(self.error_messages["required"], code="required")
                return None
            # If the value is string, return an unsaved instance or existing if present.
            try:
                return self.queryset.get(**{self.create_key_name: value})
            except self.queryset.model.DoesNotExist:
                return self.queryset.model(**{self.create_key_name: value})
        return value
