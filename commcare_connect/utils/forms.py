from django import forms

DATE_INPUT = forms.DateInput(format="%Y-%m-%d", attrs={"type": "date"})
INPUT_CLASS = "base-input"
TEXTAREA_CLASS = "base-textarea"
SELECT_CLASS = "base-dropdown"
CHECKBOX_CLASS = "simple-toggle"


FORM_BASE_STYLE = {
    "textarea": "base-textarea",
    "select": "base-dropdown",
    "checkbox": "simple-toggle",
    "base": "base-input",
    "checkboxselectmultiple": "simple-toggle",
}
