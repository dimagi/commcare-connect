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
        key = self.to_field_name or "pk"
        if isinstance(value, self.queryset.model):
            value = getattr(value, key)
        return tomselect_resolve_creatable_value(value, self.queryset, field_name=key)

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
            # Raise if an entry with this name already exists — the user explicitly
            # indicated intent to create, so a duplicate name is an error.
            if self.queryset.filter(**{self.create_key_name: value}).exists():
                raise forms.ValidationError(
                    "%(value)s already exists. Select it from the list instead of creating a new entry.",
                    code="duplicate",
                    params={"value": value},
                )
            return self.queryset.model(**{self.create_key_name: value})
        return value


class DynamicCreatableChoiceField(CreatableModelChoiceField):
    """
    CreatableModelChoiceField variant for TomSelect fields whose options are populated
    dynamically by JS (not server-rendered). Widget starts empty; queryset is validation-only.
    """

    @property
    def choices(self):
        return []

    @choices.setter
    def choices(self, value):
        # Intentionally discard: options are populated by JS, not server-rendered.
        pass
