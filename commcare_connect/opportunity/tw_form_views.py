from django.contrib import messages
from django.forms import modelformset_factory
from django.shortcuts import render
from django.test.utils import override_settings
from django.urls import reverse
from django.views.generic import UpdateView

from commcare_connect.opportunity.models import (
    DeliverUnit,
    DeliverUnitFlagRules,
    FormJsonValidationRules,
    Opportunity,
    OpportunityVerificationFlags,
)
from commcare_connect.opportunity.tasks import add_connect_users
from commcare_connect.opportunity.tw_forms import (
    DeliverUnitFlagsForm,
    FormJsonValidationRulesForm,
    OpportunityChangeForm,
    OpportunityVerificationFlagsConfigForm,
)
from commcare_connect.opportunity.views import OrganizationUserMemberRoleMixin, get_opportunity_or_404


@override_settings(CRISPY_TEMPLATE_PACK="tailwind")
def verification_flags_config(request, org_slug=None, pk=None):
    opportunity = get_opportunity_or_404(pk=pk, org_slug=org_slug)
    verification_flags = OpportunityVerificationFlags.objects.filter(opportunity=opportunity).first()
    form = OpportunityVerificationFlagsConfigForm(instance=verification_flags, data=request.POST or None)
    deliver_unit_count = DeliverUnit.objects.filter(app=opportunity.deliver_app).count()
    DeliverUnitFlagsFormset = modelformset_factory(
        DeliverUnitFlagRules, DeliverUnitFlagsForm, extra=deliver_unit_count, max_num=deliver_unit_count
    )
    deliver_unit_flags = DeliverUnitFlagRules.objects.filter(opportunity=opportunity)
    deliver_unit_formset = DeliverUnitFlagsFormset(
        form_kwargs={"opportunity": opportunity},
        prefix="deliver_unit",
        queryset=deliver_unit_flags,
        data=request.POST or None,
        initial=[
            {"deliver_unit": du}
            for du in opportunity.deliver_app.deliver_units.exclude(
                id__in=deliver_unit_flags.values_list("deliver_unit")
            )
        ],
    )
    FormJsonValidationRulesFormset = modelformset_factory(
        FormJsonValidationRules,
        FormJsonValidationRulesForm,
        extra=1,
    )
    form_json_formset = FormJsonValidationRulesFormset(
        form_kwargs={"opportunity": opportunity},
        prefix="form_json",
        queryset=FormJsonValidationRules.objects.filter(opportunity=opportunity),
        data=request.POST or None,
    )
    if (
        request.method == "POST"
        and form.is_valid()
        and deliver_unit_formset.is_valid()
        and form_json_formset.is_valid()
    ):
        verification_flags = form.save(commit=False)
        verification_flags.opportunity = opportunity
        verification_flags.save()
        for du_form in deliver_unit_formset.forms:
            if du_form.is_valid() and du_form.cleaned_data != {}:
                du_form.instance.opportunity = opportunity
                du_form.save()
        for fj_form in form_json_formset.forms:
            if fj_form.is_valid() and fj_form.cleaned_data != {}:
                fj_form.instance.opportunity = opportunity
                fj_form.save()
        messages.success(request, "Verification flags saved successfully.")

    return render(
        request,
        "tailwind/pages/verification_flags_config.html",
        context=dict(
            opportunity=opportunity,
            title=f"{request.org.slug} - {opportunity.name}",
            form=form,
            deliver_unit_formset=deliver_unit_formset,
            form_json_formset=form_json_formset,
        ),
    )


class OpportunityEdit(OrganizationUserMemberRoleMixin, UpdateView):
    model = Opportunity
    template_name = "tailwind/pages/opportunity_edit.html"
    form_class = OpportunityChangeForm

    @override_settings(CRISPY_TEMPLATE_PACK="tailwind")
    def dispatch(self, request, *args, **kwargs):
        return super().dispatch(request, *args, **kwargs)

    def get_success_url(self):
        return reverse("opportunity:detail", args=(self.request.org.slug, self.object.id))

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["org_slug"] = self.request.org.slug
        return kwargs

    def form_valid(self, form):
        opportunity = form.instance
        opportunity.modified_by = self.request.user.email
        users = form.cleaned_data["users"]
        filter_country = form.cleaned_data["filter_country"]
        filter_credential = form.cleaned_data["filter_credential"]
        if users or filter_country or filter_credential:
            add_connect_users.delay(users, form.instance.id, filter_country, filter_credential)

        additional_users = form.cleaned_data["additional_users"]
        if additional_users:
            for payment_unit in opportunity.paymentunit_set.all():
                opportunity.total_budget += payment_unit.amount * payment_unit.max_total * additional_users
        end_date = form.cleaned_data["end_date"]
        if end_date:
            opportunity.end_date = end_date
        response = super().form_valid(form)
        return response
