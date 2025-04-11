from django.contrib import messages
from django.db.models import Q
from django.forms import modelformset_factory
from django.shortcuts import get_object_or_404, redirect, render
from django.test.utils import override_settings
from django.urls import reverse
from django.views.generic import CreateView

from commcare_connect.opportunity import views
from commcare_connect.opportunity.models import (
    DeliverUnit,
    DeliverUnitFlagRules,
    FormJsonValidationRules,
    OpportunityAccess,
    OpportunityClaim,
    OpportunityClaimLimit,
    OpportunityVerificationFlags,
    PaymentUnit,
)
from commcare_connect.opportunity.tasks import (
    create_learn_modules_and_deliver_units,
    send_push_notification_task,
    send_sms_task,
)
from commcare_connect.opportunity.tw_forms import (
    DeliverUnitFlagsForm,
    FormJsonValidationRulesForm,
    OpportunityChangeForm,
    OpportunityFinalizeForm,
    OpportunityInitForm,
    OpportunityVerificationFlagsConfigForm,
    PaymentInvoiceForm,
    PaymentUnitForm,
    ProgramForm,
    SendMessageMobileUsersForm,
    VisitExportForm,
)
from commcare_connect.opportunity.tw_views import TWAddBudgetExistingUsersForm
from commcare_connect.opportunity.views import OrganizationUserMemberRoleMixin, get_opportunity_or_404
from commcare_connect.organization.decorators import org_admin_required, org_member_required
from commcare_connect.program import views as program_views
from commcare_connect.users.models import User


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


class OpportunityEdit(views.OpportunityEdit):
    template_name = "tailwind/pages/opportunity_edit.html"
    form_class = OpportunityChangeForm


@override_settings(CRISPY_TEMPLATE_PACK="tailwind")
@org_member_required
def add_payment_unit(request, org_slug=None, pk=None):
    opportunity = get_opportunity_or_404(org_slug=org_slug, pk=pk)
    deliver_units = DeliverUnit.objects.filter(
        Q(payment_unit__isnull=True) | Q(payment_unit__opportunity__active=False), app=opportunity.deliver_app
    )
    form = PaymentUnitForm(
        deliver_units=deliver_units,
        data=request.POST or None,
        payment_units=opportunity.paymentunit_set.filter(parent_payment_unit__isnull=True).all(),
    )
    if form.is_valid():
        form.instance.opportunity = opportunity
        form.save()
        required_deliver_units = form.cleaned_data["required_deliver_units"]
        DeliverUnit.objects.filter(id__in=required_deliver_units, payment_unit__isnull=True).update(
            payment_unit=form.instance.id
        )
        optional_deliver_units = form.cleaned_data["optional_deliver_units"]
        DeliverUnit.objects.filter(id__in=optional_deliver_units, payment_unit__isnull=True).update(
            payment_unit=form.instance.id, optional=True
        )
        sub_payment_units = form.cleaned_data["payment_units"]
        PaymentUnit.objects.filter(id__in=sub_payment_units, parent_payment_unit__isnull=True).update(
            parent_payment_unit=form.instance.id
        )
        messages.success(request, f"Payment unit {form.instance.name} created.")
        claims = OpportunityClaim.objects.filter(opportunity_access__opportunity=opportunity)
        for claim in claims:
            OpportunityClaimLimit.create_claim_limits(opportunity, claim)
        return redirect("opportunity:add_payment_units", org_slug=request.org.slug, pk=opportunity.id)
    elif request.POST:
        messages.error(request, "Invalid Data")
        return redirect("opportunity:add_payment_units", org_slug=request.org.slug, pk=opportunity.id)
    return render(
        request,
        "partial_form.html" if request.GET.get("partial") == "True" else "tailwind/pages/form.html",
        dict(title=f"{request.org.slug} - {opportunity.name}", form_title="Payment Unit Create", form=form),
    )


@override_settings(CRISPY_TEMPLATE_PACK="tailwind")
@org_member_required
def edit_payment_unit(request, org_slug=None, opp_id=None, pk=None):
    opportunity = get_opportunity_or_404(pk=opp_id, org_slug=org_slug)
    payment_unit = get_object_or_404(PaymentUnit, id=pk, opportunity=opportunity)
    deliver_units = DeliverUnit.objects.filter(
        Q(payment_unit__isnull=True) | Q(payment_unit=payment_unit) | Q(payment_unit__opportunity__active=False),
        app=opportunity.deliver_app,
    )
    exclude_payment_units = [payment_unit.pk]
    if payment_unit.parent_payment_unit_id:
        exclude_payment_units.append(payment_unit.parent_payment_unit_id)
    payment_unit_deliver_units = {deliver_unit.pk for deliver_unit in payment_unit.deliver_units.all()}
    opportunity_payment_units = (
        opportunity.paymentunit_set.filter(
            Q(parent_payment_unit=payment_unit.pk) | Q(parent_payment_unit__isnull=True)
        )
        .exclude(pk__in=exclude_payment_units)
        .all()
    )
    form = PaymentUnitForm(
        deliver_units=deliver_units,
        instance=payment_unit,
        data=request.POST or None,
        payment_units=opportunity_payment_units,
    )
    if form.is_valid():
        form.save()
        required_deliver_units = form.cleaned_data["required_deliver_units"]
        DeliverUnit.objects.filter(id__in=required_deliver_units).update(payment_unit=form.instance.id, optional=False)
        optional_deliver_units = form.cleaned_data["optional_deliver_units"]
        DeliverUnit.objects.filter(id__in=optional_deliver_units).update(payment_unit=form.instance.id, optional=True)
        sub_payment_units = form.cleaned_data["payment_units"]
        PaymentUnit.objects.filter(id__in=sub_payment_units, parent_payment_unit__isnull=True).update(
            parent_payment_unit=form.instance.id
        )
        # Remove deliver units which are not selected anymore
        deliver_units = required_deliver_units + optional_deliver_units
        removed_deliver_units = payment_unit_deliver_units - {int(deliver_unit) for deliver_unit in deliver_units}
        DeliverUnit.objects.filter(id__in=removed_deliver_units).update(payment_unit=None, optional=False)
        removed_payment_units = {payment_unit.id for payment_unit in opportunity_payment_units} - {
            int(payment_unit_id) for payment_unit_id in sub_payment_units
        }
        PaymentUnit.objects.filter(id__in=removed_payment_units, parent_payment_unit=form.instance.id).update(
            parent_payment_unit=None
        )
        messages.success(request, f"Payment unit {form.instance.name} updated. Please reset the budget")
        return redirect("opportunity:finalize", org_slug=request.org.slug, pk=opportunity.id)
    return render(
        request,
        "tailwind/pages/form.html",
        dict(title=f"{request.org.slug} - {opportunity.name}", form_title="Payment Unit Edit", form=form),
    )


@override_settings(CRISPY_TEMPLATE_PACK="tailwind")
@org_admin_required
def send_message_mobile_users(request, org_slug=None, pk=None):
    opportunity = get_opportunity_or_404(pk=pk, org_slug=org_slug)
    user_ids = OpportunityAccess.objects.filter(opportunity=opportunity).values_list("user_id", flat=True)
    users = User.objects.filter(pk__in=user_ids)
    form = SendMessageMobileUsersForm(users=users, data=request.POST or None)

    if form.is_valid():
        selected_user_ids = form.cleaned_data["selected_users"]
        title = form.cleaned_data["title"]
        body = form.cleaned_data["body"]
        message_type = form.cleaned_data["message_type"]
        if "notification" in message_type:
            send_push_notification_task.delay(selected_user_ids, title, body)
        if "sms" in message_type:
            send_sms_task.delay(selected_user_ids, body)
        return redirect("opportunity:detail", org_slug=request.org.slug, pk=pk)

    return render(
        request,
        "tailwind/pages/send_message.html",
        context=dict(
            title=f"{request.org.slug} - {opportunity.name}",
            form_title="Send Message",
            form=form,
            users=users,
            user_ids=list(user_ids),
        ),
    )


@override_settings(CRISPY_TEMPLATE_PACK="tailwind")
@org_member_required
def invoice_create(request, org_slug=None, pk=None):
    opportunity = get_opportunity_or_404(pk, org_slug)
    # if not opportunity.managed or request.org_membership.is_program_manager:
    #     return redirect("opportunity:detail", org_slug, pk)
    form = PaymentInvoiceForm(data=request.POST or None, opportunity=opportunity)
    if request.POST and form.is_valid():
        form.save()
    return render(
        request,
        "tailwind/pages/form.html",
        dict(title=f"{request.org.slug} - {opportunity.name}", form=form),
    )


@override_settings(CRISPY_TEMPLATE_PACK="tailwind")
@org_member_required
def add_budget_existing_users(request, org_slug=None, pk=None):
    return views.add_budget_existing_users(
        request,
        org_slug,
        pk,
        form_class=TWAddBudgetExistingUsersForm,
        template_name="tailwind/pages/add_visits_existing_users.html",
    )


class OpportunityInit(OrganizationUserMemberRoleMixin, CreateView):
    template_name = "tailwind/pages/opportunity_init.html"
    form_class = OpportunityInitForm

    def get_success_url(self):
        return reverse("opportunity:add_payment_units", args=(self.request.org.slug, self.object.id))

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["domains"] = []
        kwargs["user"] = self.request.user
        kwargs["org_slug"] = self.request.org.slug
        return kwargs

    def form_valid(self, form: OpportunityInitForm):
        response = super().form_valid(form)
        create_learn_modules_and_deliver_units(self.object.id)
        return response


@org_member_required
def add_payment_units(request, org_slug=None, pk=None):
    if request.POST:
        return add_payment_unit(request, org_slug=org_slug, pk=pk)
    opportunity = get_opportunity_or_404(org_slug=org_slug, pk=pk)
    return render(request, "tailwind/pages/add_payment_units.html", dict(opportunity=opportunity))


class OpportunityFinalize(views.OpportunityFinalize):
    template_name = "tailwind/pages/opportunity_finalize.html"
    form_class = OpportunityFinalizeForm

    def dispatch(self, request, *args, **kwargs):
        self.object = self.get_object()
        if self.object.paymentunit_set.count() == 0:
            messages.warning(request, "Please configure payment units before setting budget")
            return redirect("opportunity:tw_add_payment_units", org_slug=request.org.slug, pk=self.object.id)
        return super().dispatch(request, *args, **kwargs)


class ProgramCreateOrUpdate(program_views.ProgramCreateOrUpdate):
    form_class = ProgramForm

    def get_template_names(self):
        view = ("add", "edit")[self.object is not None]
        template = f"tailwind/pages/program_{view}.html"
        return template


def invite_organization(request, org_slug=None, pk=None):
    return render(request, "tailwind/pages/invite_organization.html")


def export_user_visits(request, org_slug, pk):
    form = VisitExportForm(data=request.POST or None)
    return render(request, "tailwind/pages/form.html", context=dict(form=form))
