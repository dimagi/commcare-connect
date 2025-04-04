from django.contrib import messages
from django.forms import modelformset_factory
from django.shortcuts import render
from django.test.utils import override_settings

from commcare_connect.opportunity.models import (
    DeliverUnit,
    DeliverUnitFlagRules,
    FormJsonValidationRules,
    OpportunityVerificationFlags,
)
from commcare_connect.opportunity.tw_forms import (
    DeliverUnitFlagsForm,
    FormJsonValidationRulesForm,
    OpportunityVerificationFlagsConfigForm,
)
from commcare_connect.opportunity.views import get_opportunity_or_404


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
