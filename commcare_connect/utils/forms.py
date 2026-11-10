from crispy_forms.helper import FormHelper
from django import forms
from django.core.exceptions import ValidationError
from django.utils.text import slugify
from django.utils.timezone import localdate
from django.utils.translation import gettext
from django.utils.translation import gettext_lazy as _

from commcare_connect.utils.datetime import DateRanges


class PresetDateRangeField(forms.MultiValueField):
    """A date range picked from relative presets ("Last 3 months") or entered as a custom From/To range.

    Cleans to an inclusive `slice(date_from, date_to)`, the range value django-filter's RangeFilter takes (either
    bound of a custom range may be None), or to None when no range is chosen. Presets are resolved against today
    when the form is cleaned, so a saved URL stays relative. The submitted values are `<name>_range`, `<name>_from`
    and `<name>_to`.
    """

    def __init__(self, *, presets, **kwargs):
        choices = [("", _("Any time"))] + [(p.value, p.label) for p in (*presets, DateRanges.CUSTOM)]
        fields = (
            forms.ChoiceField(choices=choices, required=False),
            forms.DateField(required=False),
            forms.DateField(required=False),
        )
        kwargs.setdefault("required", False)
        kwargs.setdefault("widget", PresetDateRangeWidget(choices=choices))
        super().__init__(fields, require_all_fields=False, **kwargs)

    def compress(self, data_list):
        preset, date_from, date_to = data_list or ("", None, None)
        if not preset:
            return None
        if preset != DateRanges.CUSTOM:
            return slice(*DateRanges(preset).bounds(localdate()))
        if not (date_from or date_to):
            return None
        if date_from and date_to and date_from > date_to:
            raise ValidationError(_("The From date must be on or before the To date."), code="invalid_range")
        return slice(date_from, date_to)


class PresetDateRangeWidget(forms.MultiWidget):
    """A preset dropdown, with From/To date inputs that Alpine shows (and enables) only for "Custom range".

    The To input's `min` follows the From date, so the browser refuses a range that ends before it starts.
    """

    template_name = "widgets/preset_date_range.html"

    def __init__(self, choices, attrs=None):
        # Styled here because crispy-tailwind only styles the widgets it recognises by class name.
        # Hidden From/To inputs are disabled too, so a preset doesn't send stale custom dates.
        custom_only = {"type": "date", "class": "base-input", "x-bind:disabled": f"preset !== '{DateRanges.CUSTOM}'"}
        widgets = {
            "range": forms.Select(choices=choices, attrs={"class": "base-input", "x-model": "preset"}),
            "from": forms.DateInput(format="%Y-%m-%d", attrs={**custom_only, "x-model": "dateFrom"}),
            "to": forms.DateInput(format="%Y-%m-%d", attrs={**custom_only, "x-bind:min": "dateFrom"}),
        }
        super().__init__(widgets, attrs)

    def decompress(self, value):
        if not value:
            return ["", None, None]
        return [DateRanges.CUSTOM.value, value.start, value.stop]

    def get_context(self, name, value, attrs):
        context = super().get_context(name, value, attrs)
        context["custom"] = DateRanges.CUSTOM.value
        return context


class ManagedOptionForm(forms.ModelForm):
    """Adds or edits a name/slug/description option that Internal Features manages (see users/managed_options.py).

    On create the slug is filled in from the name as you type (see slugAutofill in alpine_data.js) and, if left
    blank, generated from it on save. After that it is read-only, as other systems identify options by it; subclasses
    say which in `locked_slug_help_text`.
    """

    locked_slug_help_text = None

    class Meta:
        fields = ("name", "slug", "description")
        widgets = {"description": forms.Textarea(attrs={"rows": 3})}
        help_texts = {"slug": _("Lowercase letters, numbers, hyphens and underscores.")}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["description"].required = False
        if self.instance.pk:
            self.fields["slug"].disabled = True
            self.fields["slug"].help_text = self.locked_slug_help_text
        else:
            self.fields["slug"].required = False
            self.fields["name"].widget.attrs.update({"x-ref": "name", "x-on:input": "nameChanged()"})
            self.fields["slug"].widget.attrs.update({"x-ref": "slug", "x-on:input": "slugChanged()"})
        self.helper = FormHelper(self)
        self.helper.form_tag = False
        self.helper.disable_csrf = True

    def clean_name(self):
        name = self.cleaned_data["name"].strip()
        if self._others().filter(name__iexact=name).exists():
            raise ValidationError(gettext("A %(option)s with this name already exists."), params=self._params())
        return name

    def clean_slug(self):
        if self.instance.pk:
            return self.instance.slug
        slug = self.cleaned_data["slug"].strip() or slugify(self.cleaned_data.get("name", ""))
        if not slug:
            raise ValidationError(gettext("Enter a slug, or a name containing at least one letter or number."))
        if slug != slugify(slug):
            raise ValidationError(gettext("Use only lowercase letters, numbers, hyphens and underscores."))
        if self._others().filter(slug=slug).exists():
            raise ValidationError(gettext("A %(option)s with this slug already exists."), params=self._params())
        return slug

    def _others(self):
        return self._meta.model.objects.exclude(pk=self.instance.pk)

    def _params(self):
        return {"option": self._meta.model._meta.verbose_name}
