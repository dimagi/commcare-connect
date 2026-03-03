from django import forms


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
            return value

    def validate(self, value):
        if isinstance(value, str):
            # Skip queryset validation for new string values used for creation.
            return
        return super().validate(value)

    def clean(self, value):
        value = super().clean(value)
        if isinstance(value, str) and value.strip():
            # If the value is string, create the object and return it.
            obj, _ = self.queryset.model.objects.get_or_create(**{self.create_key_name: value})
            return obj
        return value
