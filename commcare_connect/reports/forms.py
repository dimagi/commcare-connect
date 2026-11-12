from django import forms
from django.utils.translation import gettext_lazy as _

from commcare_connect.users.models import User
from commcare_connect.utils.permission_const import KPI_REPORT_ACCESS


class AddRecipientForm(forms.Form):
    email = forms.EmailField(label=_("User email"))

    def clean_email(self):
        user = User.objects.filter(email__iexact=self.cleaned_data["email"], is_active=True).first()
        if not user:
            raise forms.ValidationError(_("No active Connect user with this email."))
        if not user.has_perm(KPI_REPORT_ACCESS):
            raise forms.ValidationError(_("This user does not have access to the Delivery Stats report."))
        return user
