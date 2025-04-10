import datetime
import json
from functools import reduce
from http import HTTPStatus

from celery.result import AsyncResult
from crispy_forms.utils import render_crispy_form
from django.conf import settings
from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.core.files.storage import storages
from django.db.models import Q, Sum
from django.forms import modelformset_factory
from django.http import FileResponse, Http404, HttpResponse
from django.middleware.csrf import get_token
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.html import format_html
from django.utils.safestring import mark_safe
from django.utils.text import slugify
from django.utils.timezone import now
from django.utils.translation import gettext as _
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_http_methods, require_POST
from django.views.generic import CreateView, DetailView, ListView, UpdateView
from django_tables2 import RequestConfig, SingleTableView
from django_tables2.export import TableExport
from geopy import distance

from commcare_connect.connect_id_client import fetch_users
from commcare_connect.form_receiver.serializers import XFormSerializer
from commcare_connect.opportunity.api.serializers import remove_opportunity_access_cache
from commcare_connect.opportunity.app_xml import AppNoBuildException
from commcare_connect.opportunity.forms import (
    AddBudgetExistingUsersForm,
    AddBudgetNewUsersForm,
    DateRanges,
    DeliverUnitFlagsForm,
    FormJsonValidationRulesForm,
    OpportunityChangeForm,
    OpportunityCreationForm,
    OpportunityFinalizeForm,
    OpportunityInitForm,
    OpportunityUserInviteForm,
    OpportunityVerificationFlagsConfigForm,
    PaymentExportForm,
    PaymentInvoiceForm,
    PaymentUnitForm,
    ReviewVisitExportForm,
    SendMessageMobileUsersForm,
    VisitExportForm,
)
from commcare_connect.opportunity.helpers import (
    get_annotated_opportunity_access,
    get_annotated_opportunity_access_deliver_status,
    get_payment_report_data,
)
from commcare_connect.opportunity.models import (
    BlobMeta,
    CatchmentArea,
    CompletedWork,
    CompletedWorkStatus,
    DeliverUnit,
    DeliverUnitFlagRules,
    FormJsonValidationRules,
    Opportunity,
    OpportunityAccess,
    OpportunityClaim,
    OpportunityClaimLimit,
    OpportunityVerificationFlags,
    Payment,
    PaymentInvoice,
    PaymentUnit,
    UserInvite,
    UserInviteStatus,
    UserVisit,
    VisitReviewStatus,
    VisitValidationStatus,
)
from commcare_connect.opportunity.tables import (
    CompletedWorkTable,
    DeliverStatusTable,
    LearnStatusTable,
    OpportunityPaymentTable,
    PaymentInvoiceTable,
    PaymentReportTable,
    PaymentUnitTable,
    SuspendedUsersTable,
    UserPaymentsTable,
    UserStatusTable,
    UserVisitFilter,
    UserVisitReviewFilter,
    UserVisitReviewTable,
    UserVisitTable,
)
from commcare_connect.opportunity.tasks import (
    add_connect_users,
    create_learn_modules_and_deliver_units,
    generate_catchment_area_export,
    generate_deliver_status_export,
    generate_payment_export,
    generate_review_visit_export,
    generate_user_status_export,
    generate_visit_export,
    generate_work_status_export,
    invite_user,
    send_push_notification_task,
    send_sms_task,
    update_user_and_send_invite,
)
from commcare_connect.opportunity.visit_import import (
    ImportException,
    bulk_update_catchments,
    bulk_update_completed_work_status,
    bulk_update_payment_status,
    bulk_update_visit_review_status,
    bulk_update_visit_status,
    get_exchange_rate,
    update_payment_accrued,
)
from commcare_connect.organization.decorators import org_admin_required, org_member_required, org_viewer_required
from commcare_connect.program.models import ManagedOpportunity, ProgramApplication
from commcare_connect.program.tables import ProgramInvitationTable
from commcare_connect.users.models import User
from commcare_connect.utils.commcarehq_api import get_applications_for_user_by_domain, get_domains_for_user


class OrganizationUserMixin(LoginRequiredMixin, UserPassesTestMixin):
    def test_func(self):
        # request.org_membership is a SimpleLazyObject object so `is not None` is always `True`
        return self.request.org_membership != None or self.request.user.is_superuser  # noqa: E711


class OrganizationUserMemberRoleMixin(LoginRequiredMixin, UserPassesTestMixin):
    def test_func(self):
        return (
            self.request.org_membership != None and not self.request.org_membership.is_viewer  # noqa: E711
        ) or self.request.user.is_superuser


def get_opportunity_or_404(pk, org_slug):
    opp = get_object_or_404(Opportunity, id=pk)

    if (opp.organization and opp.organization.slug == org_slug) or (
        opp.managed and opp.managedopportunity.program.organization.slug == org_slug
    ):
        return opp

    raise Http404("Opportunity not found.")


class OrgContextSingleTableView(SingleTableView):
    def get_table_kwargs(self):
        kwargs = super().get_table_kwargs()
        kwargs["org_slug"] = self.request.org.slug
        return kwargs


class OpportunityList(OrganizationUserMixin, ListView):
    model = Opportunity
    paginate_by = 10

    def get_queryset(self):
        ordering = self.request.GET.get("sort", "name")
        if ordering not in ["name", "-name", "start_date", "-start_date", "end_date", "-end_date"]:
            ordering = "name"

        return Opportunity.objects.filter(organization=self.request.org).order_by(ordering)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["opportunity_init_url"] = reverse("opportunity:init", kwargs={"org_slug": self.request.org.slug})

        program_invitation_table = None
        if self.request.org_membership and self.request.org_membership.is_admin or self.request.user.is_superuser:
            program_invitations = ProgramApplication.objects.filter(organization=self.request.org)
            program_invitation_table = ProgramInvitationTable(program_invitations)
        context["program_invitation_table"] = program_invitation_table
        context["base_template"] = "opportunity/base.html"
        return context


class OpportunityCreate(OrganizationUserMemberRoleMixin, CreateView):
    template_name = "opportunity/opportunity_create.html"
    form_class = OpportunityCreationForm

    def get_success_url(self):
        return reverse("opportunity:list", args=(self.request.org.slug,))

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["domains"] = get_domains_for_user(self.request.user)
        kwargs["user"] = self.request.user
        kwargs["org_slug"] = self.request.org.slug
        return kwargs

    def form_valid(self, form: OpportunityCreationForm) -> HttpResponse:
        response = super().form_valid(form)
        create_learn_modules_and_deliver_units.delay(self.object.id)
        return response


class OpportunityInit(OrganizationUserMemberRoleMixin, CreateView):
    template_name = "opportunity/opportunity_init.html"
    form_class = OpportunityInitForm

    def get_success_url(self):
        return reverse("opportunity:add_payment_units", args=(self.request.org.slug, self.object.id))

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["domains"] = get_domains_for_user(self.request.user)
        kwargs["user"] = self.request.user
        kwargs["org_slug"] = self.request.org.slug
        return kwargs

    def form_valid(self, form: OpportunityInitForm) -> HttpResponse:
        response = super().form_valid(form)
        create_learn_modules_and_deliver_units(self.object.id)
        return response


class OpportunityEdit(OrganizationUserMemberRoleMixin, UpdateView):
    model = Opportunity
    template_name = "opportunity/opportunity_edit.html"
    form_class = OpportunityChangeForm

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

        end_date = form.cleaned_data["end_date"]
        if end_date:
            opportunity.end_date = end_date
        response = super().form_valid(form)
        return response


class OpportunityFinalize(OrganizationUserMemberRoleMixin, UpdateView):
    model = Opportunity
    template_name = "opportunity/opportunity_finalize.html"
    form_class = OpportunityFinalizeForm

    def dispatch(self, request, *args, **kwargs):
        self.object = self.get_object()
        if self.object.paymentunit_set.count() == 0:
            messages.warning(request, "Please configure payment units before setting budget")
            return redirect("opportunity:add_payment_units", org_slug=request.org.slug, pk=self.object.id)
        return super().dispatch(request, *args, **kwargs)

    def get_success_url(self):
        return reverse("opportunity:detail", args=(self.request.org.slug, self.object.id))

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        opportunity = self.object
        payment_units = opportunity.paymentunit_set.all()
        budget_per_user = 0
        payment_units_max_total = 0
        for pu in payment_units:
            budget_per_user += pu.amount * pu.max_total
            payment_units_max_total += pu.max_total
        kwargs["budget_per_user"] = budget_per_user
        kwargs["current_start_date"] = opportunity.start_date
        kwargs["opportunity"] = opportunity
        kwargs["payment_units_max_total"] = payment_units_max_total
        return kwargs

    def form_valid(self, form):
        opportunity = form.instance
        opportunity.modified_by = self.request.user.email
        start_date = form.cleaned_data["start_date"]
        end_date = form.cleaned_data["end_date"]
        if end_date:
            opportunity.end_date = end_date
        if start_date:
            opportunity.start_date = start_date

        if opportunity.managed:
            ManagedOpportunity.objects.filter(id=opportunity.id).update(
                org_pay_per_visit=form.cleaned_data["org_pay_per_visit"]
            )
        response = super().form_valid(form)
        return response


class OpportunityDetail(OrganizationUserMixin, DetailView):
    model = Opportunity
    template_name = "opportunity/opportunity_detail.html"

    def get(self, request, *args, **kwargs):
        self.object = self.get_object()
        if not self.object.is_setup_complete:
            messages.warning(request, "Please complete the opportunity setup to view it")
            return redirect("opportunity:add_payment_units", org_slug=request.org.slug, pk=self.object.id)
        context = self.get_context_data(object=self.object)
        return self.render_to_response(context)

    def get_context_data(self, object, **kwargs):
        context = super().get_context_data(**kwargs)
        context["export_task_id"] = self.request.GET.get("export_task_id")
        context["visit_export_form"] = VisitExportForm()
        context["export_form"] = PaymentExportForm()
        context["review_visit_export_form"] = ReviewVisitExportForm()
        context["user_is_network_manager"] = object.managed and object.organization == self.request.org
        import_visit_helper_text = _(
            'The file must contain at least the "Visit ID"{extra} and "Status" column. The import is case-insensitive.'
        ).format(extra=_(', "Justification"') if object.managed else "")
        context["import_visit_helper_text"] = import_visit_helper_text
        return context


class OpportunityLearnStatusTableView(OrganizationUserMixin, OrgContextSingleTableView):
    model = OpportunityAccess
    paginate_by = 25
    table_class = LearnStatusTable
    template_name = "tables/single_table.html"

    def get_queryset(self):
        opportunity_id = self.kwargs["pk"]
        opportunity = get_opportunity_or_404(org_slug=self.request.org.slug, pk=opportunity_id)
        return OpportunityAccess.objects.filter(opportunity=opportunity).order_by("user__name")


class OpportunityPaymentTableView(OrganizationUserMixin, OrgContextSingleTableView):
    model = OpportunityAccess
    paginate_by = 25
    table_class = OpportunityPaymentTable
    template_name = "tables/single_table.html"

    def get_queryset(self):
        opportunity_id = self.kwargs["pk"]
        org_slug = self.kwargs["org_slug"]
        opportunity = get_opportunity_or_404(org_slug=org_slug, pk=opportunity_id)
        return OpportunityAccess.objects.filter(opportunity=opportunity, payment_accrued__gte=0).order_by(
            "-payment_accrued"
        )


class UserPaymentsTableView(OrganizationUserMixin, SingleTableView):
    model = Payment
    paginate_by = 25
    table_class = UserPaymentsTable
    template_name = "opportunity/opportunity_user_payments_list.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["latest_payment"] = self.object_list.all().first()
        context["access"] = self.access
        context["opportunity"] = self.opportunity
        return context

    def get_queryset(self):
        opportunity_id = self.kwargs["opp_id"]
        org_slug = self.kwargs["org_slug"]
        self.opportunity = get_opportunity_or_404(org_slug=org_slug, pk=opportunity_id)
        access_id = self.kwargs["pk"]
        self.access = get_object_or_404(OpportunityAccess, opportunity=self.opportunity, pk=access_id)
        return Payment.objects.filter(opportunity_access=self.access).order_by("-date_paid")


class OpportunityUserLearnProgress(OrganizationUserMixin, DetailView):
    template_name = "opportunity/user_learn_progress.html"

    def get_queryset(self):
        return OpportunityAccess.objects.filter(opportunity_id=self.kwargs.get("opp_id"))


@org_member_required
def export_user_visits(request, org_slug, pk):
    get_opportunity_or_404(org_slug=request.org.slug, pk=pk)
    form = VisitExportForm(data=request.POST)
    if not form.is_valid():
        messages.error(request, form.errors)
        return redirect("opportunity:detail", request.org.slug, pk)

    export_format = form.cleaned_data["format"]
    date_range = DateRanges(form.cleaned_data["date_range"])
    status = form.cleaned_data["status"]
    flatten = form.cleaned_data["flatten_form_data"]

    result = generate_visit_export.delay(pk, date_range, status, export_format, flatten)
    redirect_url = reverse("opportunity:detail", args=(request.org.slug, pk))
    return redirect(f"{redirect_url}?export_task_id={result.id}")


@org_member_required
def review_visit_export(request, org_slug, pk):
    get_opportunity_or_404(org_slug=request.org.slug, pk=pk)
    form = ReviewVisitExportForm(data=request.POST)
    if not form.is_valid():
        messages.error(request, form.errors)
        return redirect("opportunity:detail", request.org.slug, pk)

    export_format = form.cleaned_data["format"]
    date_range = DateRanges(form.cleaned_data["date_range"])
    status = form.cleaned_data["status"]

    result = generate_review_visit_export.delay(pk, date_range, status, export_format)
    redirect_url = reverse("opportunity:detail", args=(request.org.slug, pk))
    return redirect(f"{redirect_url}?export_task_id={result.id}")


@org_member_required
@require_GET
def export_status(request, org_slug, task_id):
    task_meta = AsyncResult(task_id)._get_task_meta()
    status = task_meta.get("status")
    progress = {
        "complete": status == "SUCCESS",
    }
    if status == "FAILURE":
        progress["error"] = task_meta.get("result")
    return render(
        request,
        "opportunity/upload_progress.html",
        {
            "task_id": task_id,
            "current_time": now().microsecond,
            "progress": progress,
        },
    )


@org_member_required
@require_GET
def download_export(request, org_slug, task_id):
    task_meta = AsyncResult(task_id)._get_task_meta()
    saved_filename = task_meta.get("result")
    opportunity_id = task_meta.get("args")[0]
    opportunity = get_opportunity_or_404(org_slug=org_slug, pk=opportunity_id)
    op_slug = slugify(opportunity.name)
    export_format = saved_filename.split(".")[-1]
    filename = f"{org_slug}_{op_slug}_export.{export_format}"

    export_file = storages["default"].open(saved_filename)
    return FileResponse(
        export_file, as_attachment=True, filename=filename, content_type=TableExport.FORMATS[export_format]
    )


@org_member_required
@require_POST
def update_visit_status_import(request, org_slug=None, pk=None):
    opportunity = get_opportunity_or_404(org_slug=org_slug, pk=pk)
    file = request.FILES.get("visits")
    try:
        status = bulk_update_visit_status(opportunity, file)
    except ImportException as e:
        messages.error(request, e.message)
    else:
        message = f"Visit status updated successfully for {len(status)} visits."
        if status.missing_visits:
            message += status.get_missing_message()
        messages.success(request, mark_safe(message))
    if opportunity.managed:
        return redirect("opportunity:user_visit_review", org_slug, pk)
    return redirect("opportunity:detail", org_slug, pk)


def review_visit_import(request, org_slug=None, pk=None):
    opportunity = get_opportunity_or_404(org_slug=org_slug, pk=pk)
    file = request.FILES.get("visits")
    try:
        status = bulk_update_visit_review_status(opportunity, file)
    except ImportException as e:
        messages.error(request, e.message)
        return redirect("opportunity:detail", org_slug, pk)
    else:
        message = f"Visit review updated successfully for {len(status)} visits."
        if status.missing_visits:
            message += status.get_missing_message()
        messages.success(request, mark_safe(message))
        return redirect("opportunity:user_visit_review", org_slug, pk)


@org_member_required
def add_budget_existing_users(request, org_slug=None, pk=None):
    opportunity = get_opportunity_or_404(org_slug=org_slug, pk=pk)
    opportunity_access = OpportunityAccess.objects.filter(opportunity=opportunity)
    opportunity_claims = OpportunityClaim.objects.filter(opportunity_access__in=opportunity_access)
    program_manager = (
        getattr(request, "org_membership", None) and request.org_membership.is_program_manager
    ) or request.user.is_superuser

    form = AddBudgetExistingUsersForm(
        opportunity_claims=opportunity_claims,
        opportunity=opportunity,
        data=request.POST or None,
    )
    if form.is_valid():
        form.save()
        return redirect("opportunity:detail", org_slug, pk)

    return render(
        request,
        "opportunity/add_visits_existing_users.html",
        {
            "form": form,
            "opportunity_claims": opportunity_claims,
            "budget_per_visit": opportunity.budget_per_visit_new,
            "opportunity": opportunity,
            "disable_add_budget_for_new_users": opportunity.managed and not program_manager,
        },
    )


@org_member_required
def add_budget_new_users(request, org_slug=None, pk=None):
    opportunity = get_opportunity_or_404(org_slug=org_slug, pk=pk)
    program_manager = (
        getattr(request, "org_membership", None) and request.org_membership.is_program_manager
    ) or request.user.is_superuser

    form = AddBudgetNewUsersForm(
        opportunity=opportunity,
        program_manager=program_manager,
        data=request.POST or None,
    )

    if form.is_valid():
        form.save()
        redirect_url = reverse("opportunity:detail", args=[org_slug, pk])
        response = HttpResponse()
        response["HX-Redirect"] = redirect_url
        return response

    csrf_token = get_token(request)
    form_html = f"""
        <form id="form-content"
              hx-post="{reverse('opportunity:add_budget_new_users', args=[org_slug, pk])}"
              hx-trigger="submit"
              hx-headers='{{"X-CSRFToken": "{csrf_token}"}}'>
            <input type="hidden" name="csrfmiddlewaretoken" value="{csrf_token}">
            {render_crispy_form(form)}
        </form>
        """

    return HttpResponse(mark_safe(form_html))


class OpportunityUserStatusTableView(OrganizationUserMixin, OrgContextSingleTableView):
    model = OpportunityAccess
    paginate_by = 25
    table_class = UserStatusTable
    template_name = "tables/single_table.html"

    def get_queryset(self):
        opportunity_id = self.kwargs["pk"]
        org_slug = self.kwargs["org_slug"]
        opportunity = get_opportunity_or_404(org_slug=org_slug, pk=opportunity_id)
        access_objects = get_annotated_opportunity_access(opportunity)
        return access_objects


@org_member_required
def export_users_for_payment(request, org_slug, pk):
    get_opportunity_or_404(org_slug=request.org.slug, pk=pk)
    form = PaymentExportForm(data=request.POST)
    if not form.is_valid():
        messages.error(request, form.errors)
        return redirect("opportunity:detail", request.org.slug, pk)

    export_format = form.cleaned_data["format"]
    result = generate_payment_export.delay(pk, export_format)
    redirect_url = reverse("opportunity:detail", args=(request.org.slug, pk))
    return redirect(f"{redirect_url}?export_task_id={result.id}")


@org_member_required
@require_POST
def payment_import(request, org_slug=None, pk=None):
    opportunity = get_opportunity_or_404(org_slug=org_slug, pk=pk)
    file = request.FILES.get("payments")
    try:
        status = bulk_update_payment_status(opportunity, file)
    except ImportException as e:
        messages.error(request, e.message)
    else:
        message = f"Payment status updated successfully for {len(status)} users."
        messages.success(request, mark_safe(message))
    return redirect("opportunity:detail", org_slug, pk)


@org_member_required
def add_payment_units(request, org_slug=None, pk=None):
    if request.POST:
        return add_payment_unit(request, org_slug=org_slug, pk=pk)
    opportunity = get_opportunity_or_404(org_slug=org_slug, pk=pk)
    return render(request, "opportunity/add_payment_units.html", dict(opportunity=opportunity))


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
        org_slug=org_slug,
        opportunity_id=opportunity.pk,
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
        "partial_form.html" if request.GET.get("partial") == "True" else "form.html",
        dict(title=f"{request.org.slug} - {opportunity.name}", form_title="Payment Unit Create", form=form),
    )


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
        org_slug=org_slug,
        opportunity_id=opportunity.pk,
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
        "form.html",
        dict(title=f"{request.org.slug} - {opportunity.name}", form_title="Payment Unit Edit", form=form),
    )


class OpportunityPaymentUnitTableView(OrganizationUserMixin, OrgContextSingleTableView):
    model = PaymentUnit
    paginate_by = 25
    table_class = PaymentUnitTable
    template_name = "tables/single_table.html"

    def get_queryset(self):
        opportunity_id = self.kwargs["pk"]
        org_slug = self.kwargs["org_slug"]
        opportunity = get_opportunity_or_404(org_slug=org_slug, pk=opportunity_id)
        return PaymentUnit.objects.filter(opportunity=opportunity).order_by("name")


@org_member_required
def export_user_status(request, org_slug, pk):
    get_opportunity_or_404(org_slug=request.org.slug, pk=pk)
    form = PaymentExportForm(data=request.POST)
    if not form.is_valid():
        messages.error(request, form.errors)
        return redirect("opportunity:detail", request.org.slug, pk)

    export_format = form.cleaned_data["format"]
    result = generate_user_status_export.delay(pk, export_format)
    redirect_url = reverse("opportunity:detail", args=(request.org.slug, pk))
    return redirect(f"{redirect_url}?export_task_id={result.id}")


class OpportunityDeliverStatusTable(OrganizationUserMixin, OrgContextSingleTableView):
    model = OpportunityAccess
    paginate_by = 25
    table_class = DeliverStatusTable
    template_name = "tables/single_table.html"

    def get_queryset(self):
        opportunity_id = self.kwargs["pk"]
        org_slug = self.kwargs["org_slug"]
        opportunity = get_opportunity_or_404(pk=opportunity_id, org_slug=org_slug)
        access_objects = get_annotated_opportunity_access_deliver_status(opportunity)
        return access_objects


@org_member_required
def export_deliver_status(request, org_slug, pk):
    get_opportunity_or_404(pk=pk, org_slug=request.org.slug)
    form = PaymentExportForm(data=request.POST)
    if not form.is_valid():
        messages.error(request, form.errors)
        return redirect("opportunity:detail", request.org.slug, pk)

    export_format = form.cleaned_data["format"]
    result = generate_deliver_status_export.delay(pk, export_format)
    redirect_url = reverse("opportunity:detail", args=(request.org.slug, pk))
    return redirect(f"{redirect_url}?export_task_id={result.id}")


@org_viewer_required
def user_visits_list(request, org_slug=None, opp_id=None, pk=None):
    opportunity = get_opportunity_or_404(pk=opp_id, org_slug=org_slug)
    opportunity_access = get_object_or_404(OpportunityAccess, pk=pk, opportunity=opportunity)
    user_visits = opportunity_access.uservisit_set.order_by("visit_date")
    visit_filter = UserVisitFilter(request.GET, queryset=user_visits, managed_opportunity=opportunity.managed)
    user_visits_table = UserVisitTable(visit_filter.qs, org_slug=request.org.slug)
    if not opportunity.managed:
        user_visits_table.exclude = ("review_status",)
    RequestConfig(request, paginate={"per_page": 15}).configure(user_visits_table)
    return render(
        request,
        "opportunity/user_visits_list.html",
        context=dict(
            opportunity=opportunity,
            table=user_visits_table,
            user_name=opportunity_access.display_name,
            visit_filter=visit_filter,
        ),
    )


@org_member_required
@require_POST
def payment_delete(request, org_slug=None, opp_id=None, access_id=None, pk=None):
    opportunity = get_opportunity_or_404(pk=opp_id, org_slug=org_slug)
    opportunity_access = get_object_or_404(OpportunityAccess, pk=access_id, opportunity=opportunity)
    payment = get_object_or_404(Payment, opportunity_access=opportunity_access, pk=pk)
    payment.delete()
    return redirect("opportunity:user_payments_table", org_slug=org_slug, opp_id=opp_id, pk=access_id)


@org_viewer_required
def user_profile(request, org_slug=None, opp_id=None, pk=None):
    access = get_object_or_404(OpportunityAccess, pk=pk, accepted=True)
    user_visits = UserVisit.objects.filter(opportunity_access=access)
    user_catchments = CatchmentArea.objects.filter(opportunity_access=access)
    user_visit_data = []
    for user_visit in user_visits:
        if not user_visit.location:
            continue
        lat, lng, elevation, precision = list(map(float, user_visit.location.split(" ")))
        user_visit_data.append(
            dict(
                entity_name=user_visit.entity_name,
                visit_date=user_visit.visit_date.date(),
                lat=lat,
                lng=lng,
                precision=precision,
            )
        )
    # user for centering the User visits map
    lat_avg = 0.0
    lng_avg = 0.0
    if user_visit_data:
        lat_avg = reduce(lambda x, y: x + float(y["lat"]), user_visit_data, 0.0) / len(user_visit_data)
        lng_avg = reduce(lambda x, y: x + float(y["lng"]), user_visit_data, 0.0) / len(user_visit_data)

    pending_completed_work_count = len(
        [
            cw
            for cw in CompletedWork.objects.filter(opportunity_access=access, status=CompletedWorkStatus.pending)
            if cw.approved_count
        ]
    )
    user_catchment_data = [
        {
            "name": catchment.name,
            "lat": float(catchment.latitude),
            "lng": float(catchment.longitude),
            "radius": catchment.radius,
            "active": catchment.active,
        }
        for catchment in user_catchments
    ]
    pending_payment = max(access.payment_accrued - access.total_paid, 0)
    return render(
        request,
        "opportunity/user_profile.html",
        context=dict(
            access=access,
            user_visits=user_visit_data,
            lat_avg=lat_avg,
            lng_avg=lng_avg,
            MAPBOX_TOKEN=settings.MAPBOX_TOKEN,
            pending_completed_work_count=pending_completed_work_count,
            pending_payment=pending_payment,
            user_catchments=user_catchment_data,
        ),
    )


@org_admin_required
def send_message_mobile_users(request, org_slug=None, pk=None):
    opportunity = get_opportunity_or_404(pk=pk, org_slug=org_slug)
    user_ids = OpportunityAccess.objects.filter(opportunity=opportunity, accepted=True).values_list(
        "user_id", flat=True
    )
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
        "opportunity/send_message.html",
        context=dict(
            title=f"{request.org.slug} - {opportunity.name}",
            form_title="Send Message",
            form=form,
            users=users,
            user_ids=list(user_ids),
        ),
    )


# used for loading learn_app and deliver_app dropdowns
@org_member_required
def get_application(request, org_slug=None):
    domain = request.GET.get("learn_app_domain") or request.GET.get("deliver_app_domain")
    applications = get_applications_for_user_by_domain(request.user, domain)
    active_opps = Opportunity.objects.filter(
        Q(learn_app__cc_domain=domain) | Q(deliver_app__cc_domain=domain),
        active=True,
        end_date__lt=datetime.date.today(),
    ).select_related("learn_app", "deliver_app")
    existing_apps = set()
    for opp in active_opps:
        if opp.learn_app.cc_domain == domain:
            existing_apps.add(opp.learn_app.cc_app_id)
        if opp.deliver_app.cc_domain == domain:
            existing_apps.add(opp.deliver_app.cc_app_id)
    options = []
    for app in applications:
        if app["id"] not in existing_apps:
            value = json.dumps(app)
            name = app["name"]
            options.append(format_html("<option value='{}'>{}</option>", value, name))
    return HttpResponse("\n".join(options))


@org_viewer_required
def visit_verification(request, org_slug=None, pk=None):
    user_visit = get_object_or_404(UserVisit, pk=pk)
    serializer = XFormSerializer(data=user_visit.form_json)
    access_id = OpportunityAccess.objects.get(user=user_visit.user, opportunity=user_visit.opportunity).id
    serializer.is_valid()
    xform = serializer.save()
    user_forms = []
    other_forms = []
    lat = None
    lon = None
    precision = None
    if user_visit.location:
        locations = UserVisit.objects.filter(opportunity=user_visit.opportunity).exclude(pk=pk).select_related("user")
        lat, lon, _, precision = user_visit.location.split(" ")
        for loc in locations:
            if loc.location is None:
                continue
            other_lat, other_lon, _, other_precision = loc.location.split(" ")
            dist = distance.distance((lat, lon), (other_lat, other_lon))
            if dist.m <= 250:
                if user_visit.user_id == loc.user_id:
                    user_forms.append((loc, dist.m, other_lat, other_lon, other_precision))
                else:
                    other_forms.append((loc, dist.m, other_lat, other_lon, other_precision))
        user_forms.sort(key=lambda x: x[1])
        other_forms.sort(key=lambda x: x[1])
    reason = user_visit.reason
    if user_visit.flag_reason and not reason:
        reason = "\n".join([flag[1] for flag in user_visit.flag_reason.get("flags", [])])
    return render(
        request,
        "opportunity/visit_verification.html",
        context={
            "visit": user_visit,
            "xform": xform,
            "access_id": access_id,
            "user_forms": user_forms[:5],
            "other_forms": other_forms[:5],
            "visit_lat": lat,
            "visit_lon": lon,
            "visit_precision": precision,
            "MAPBOX_TOKEN": settings.MAPBOX_TOKEN,
            "reason": reason,
        },
    )


@org_member_required
@require_POST
def approve_visit(request, org_slug=None, pk=None):
    user_visit = UserVisit.objects.get(pk=pk)
    opp_id = user_visit.opportunity_id
    if user_visit.status != VisitValidationStatus.approved:
        user_visit.status = VisitValidationStatus.approved
        if user_visit.opportunity.managed:
            user_visit.review_created_on = now()

            if user_visit.flagged:
                justification = request.POST.get("justification")
                if not justification:
                    messages.error(request, "Justification is mandatory for flagged visits.")
                user_visit.justification = justification

        user_visit.save()
        update_payment_accrued(opportunity=user_visit.opportunity, users=[user_visit.user])

    if user_visit.opportunity.managed:
        return redirect("opportunity:user_visit_review", org_slug, opp_id)

    return redirect(
        "opportunity:user_visits_list", org_slug=org_slug, opp_id=opp_id, pk=user_visit.opportunity_access_id
    )


@org_member_required
@require_POST
def reject_visit(request, org_slug=None, pk=None):
    user_visit = UserVisit.objects.get(pk=pk)
    reason = request.POST.get("reason")
    user_visit.status = VisitValidationStatus.rejected
    user_visit.reason = reason
    user_visit.save()
    access = OpportunityAccess.objects.get(user_id=user_visit.user_id, opportunity_id=user_visit.opportunity_id)
    update_payment_accrued(opportunity=access.opportunity, users=[access.user])
    return redirect("opportunity:user_visits_list", org_slug=org_slug, opp_id=user_visit.opportunity_id, pk=access.id)


@org_member_required
def fetch_attachment(self, org_slug, blob_id):
    blob_meta = BlobMeta.objects.get(blob_id=blob_id)
    attachment = storages["default"].open(blob_id)
    return FileResponse(attachment, filename=blob_meta.name, content_type=blob_meta.content_type)


@org_member_required
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
        "opportunity/verification_flags_config.html",
        context=dict(
            opportunity=opportunity,
            title=f"{request.org.slug} - {opportunity.name}",
            form=form,
            deliver_unit_formset=deliver_unit_formset,
            form_json_formset=form_json_formset,
        ),
    )


@org_member_required
@csrf_exempt
@require_http_methods(["DELETE"])
def delete_form_json_rule(request, org_slug=None, opp_id=None, pk=None):
    form_json_rule = FormJsonValidationRules.objects.get(opportunity=opp_id, pk=pk)
    form_json_rule.delete()
    return HttpResponse(status=200)


class OpportunityCompletedWorkTable(OrganizationUserMixin, SingleTableView):
    model = CompletedWork
    paginate_by = 25
    table_class = CompletedWorkTable
    template_name = "tables/single_table.html"

    def get_queryset(self):
        opportunity_id = self.kwargs["pk"]
        org_slug = self.kwargs["org_slug"]
        opportunity = get_opportunity_or_404(org_slug=org_slug, pk=opportunity_id)
        access_objects = OpportunityAccess.objects.filter(opportunity=opportunity)
        return list(
            filter(lambda cw: cw.completed, CompletedWork.objects.filter(opportunity_access__in=access_objects))
        )


@org_member_required
def export_completed_work(request, org_slug, pk):
    get_opportunity_or_404(org_slug=request.org.slug, pk=pk)
    form = PaymentExportForm(data=request.POST)
    if not form.is_valid():
        messages.error(request, form.errors)
        return redirect("opportunity:detail", request.org.slug, pk)

    export_format = form.cleaned_data["format"]
    result = generate_work_status_export.delay(pk, export_format)
    redirect_url = reverse("opportunity:detail", args=(request.org.slug, pk))
    return redirect(f"{redirect_url}?export_task_id={result.id}")


@org_member_required
@require_POST
def update_completed_work_status_import(request, org_slug=None, pk=None):
    opportunity = get_opportunity_or_404(org_slug=org_slug, pk=pk)
    file = request.FILES.get("visits")
    try:
        status = bulk_update_completed_work_status(opportunity, file)
    except ImportException as e:
        messages.error(request, e.message)
    else:
        message = f"Payment Verification status updated successfully for {len(status)} completed works."
        if status.missing_completed_works:
            message += status.get_missing_message()
        messages.success(request, mark_safe(message))
    return redirect("opportunity:detail", org_slug, pk)


@org_member_required
@require_POST
def suspend_user(request, org_slug=None, opp_id=None, pk=None):
    access = get_object_or_404(OpportunityAccess, opportunity_id=opp_id, id=pk)
    access.suspended = True
    access.suspension_date = now()
    access.suspension_reason = request.POST.get("reason", "")
    access.save()

    # Clear the cached opportunity access for the suspended user
    remove_opportunity_access_cache(access.user, access.opportunity)

    return redirect("opportunity:user_profile", org_slug, opp_id, pk)


@org_member_required
def revoke_user_suspension(request, org_slug=None, opp_id=None, pk=None):
    access = get_object_or_404(OpportunityAccess, opportunity_id=opp_id, id=pk)
    access.suspended = False
    access.save()
    remove_opportunity_access_cache(access.user, access.opportunity)
    next = request.GET.get("next", "/")
    return redirect(next)


@org_member_required
def suspended_users_list(request, org_slug=None, pk=None):
    opportunity = get_opportunity_or_404(org_slug=org_slug, pk=pk)
    access_objects = OpportunityAccess.objects.filter(opportunity=opportunity, suspended=True)
    table = SuspendedUsersTable(access_objects)
    return render(request, "opportunity/suspended_users.html", dict(table=table, opportunity=opportunity))


@org_member_required
def export_catchment_area(request, org_slug, pk):
    get_opportunity_or_404(org_slug=request.org.slug, pk=pk)
    form = PaymentExportForm(data=request.POST)
    if not form.is_valid():
        messages.error(request, form.errors)
        return redirect("opportunity:detail", request.org.slug, pk)

    export_format = form.cleaned_data["format"]
    result = generate_catchment_area_export.delay(pk, export_format)
    redirect_url = reverse("opportunity:detail", args=(request.org.slug, pk))
    return redirect(f"{redirect_url}?export_task_id={result.id}")


@org_member_required
@require_POST
def import_catchment_area(request, org_slug=None, pk=None):
    opportunity = get_opportunity_or_404(org_slug=org_slug, pk=pk)
    file = request.FILES.get("catchments")
    try:
        status = bulk_update_catchments(opportunity, file)
    except ImportException as e:
        messages.error(request, e.message)
    else:
        message = f"{len(status)} catchment areas were updated successfully and {status.new_catchments} were created."
        messages.success(request, mark_safe(message))
    return redirect("opportunity:detail", org_slug, pk)


@org_member_required
def opportunity_user_invite(request, org_slug=None, pk=None):
    opportunity = get_object_or_404(Opportunity, organization=request.org, id=pk)
    form = OpportunityUserInviteForm(data=request.POST or None, org_slug=request.org.slug, opportunity=opportunity)
    if form.is_valid():
        users = form.cleaned_data["users"]
        filter_country = form.cleaned_data["filter_country"]
        filter_credential = form.cleaned_data["filter_credential"]
        if users or filter_country or filter_credential:
            add_connect_users.delay(users, opportunity.id, filter_country, filter_credential)
        return redirect("opportunity:detail", request.org.slug, pk)
    return render(
        request,
        "form.html",
        dict(title=f"{request.org.slug} - {opportunity.name}", form_title="Invite Users", form=form),
    )


@org_member_required
def user_visit_review(request, org_slug, opp_id):
    opportunity = get_opportunity_or_404(opp_id, org_slug)
    if not opportunity.managed:
        return redirect("opportunity:detail", org_slug, opp_id)
    is_program_manager = (
        request.org_membership != None  # noqa: E711
        and request.org_membership.is_admin
        and request.org.program_manager
    )
    user_visit_reviews = UserVisit.objects.filter(opportunity=opportunity, review_created_on__isnull=False).order_by(
        "visit_date"
    )
    review_filter = UserVisitReviewFilter(request.GET, queryset=user_visit_reviews)
    table = UserVisitReviewTable(review_filter.qs, org_slug=request.org.slug)
    if not is_program_manager:
        table.exclude = ("pk",)
    if request.POST and is_program_manager:
        review_status = request.POST.get("review_status").lower()
        updated_reviews = request.POST.getlist("pk")
        user_visits = UserVisit.objects.filter(pk__in=updated_reviews)
        if review_status in [VisitReviewStatus.agree.value, VisitReviewStatus.disagree.value]:
            user_visits.update(review_status=review_status)
            update_payment_accrued(opportunity=opportunity, users=[visit.user for visit in user_visits])
    RequestConfig(request, paginate={"per_page": 15}).configure(table)
    return render(
        request,
        "opportunity/user_visit_review.html",
        context=dict(table=table, review_filter=review_filter, opportunity=opportunity),
    )


@org_member_required
def payment_report(request, org_slug, pk):
    opportunity = get_opportunity_or_404(pk, org_slug)
    if not opportunity.managed:
        return redirect("opportunity:detail", org_slug, pk)
    total_paid_users = (
        Payment.objects.filter(opportunity_access__opportunity=opportunity).aggregate(total=Sum("amount"))["total"]
        or 0
    )
    total_paid_nm = (
        Payment.objects.filter(organization=opportunity.organization).aggregate(total=Sum("amount"))["total"] or 0
    )
    data, total_user_payment_accrued, total_nm_payment_accrued = get_payment_report_data(opportunity)
    table = PaymentReportTable(data)
    return render(
        request,
        "opportunity/payment_report.html",
        context=dict(
            table=table,
            opportunity=opportunity,
            total_paid_users=total_paid_users,
            total_user_payment_accrued=total_user_payment_accrued,
            total_paid_nm=total_paid_nm,
            total_nm_payment_accrued=total_nm_payment_accrued,
        ),
    )


class PaymentInvoiceTableView(OrganizationUserMixin, SingleTableView):
    model = PaymentInvoice
    paginate_by = 25
    table_class = PaymentInvoiceTable
    template_name = "tables/single_table.html"

    def get_table_kwargs(self):
        kwargs = super().get_table_kwargs()
        if self.request.org_membership != None and not self.request.org_membership.is_program_manager:  # noqa: E711
            kwargs["exclude"] = ("pk",)
        return kwargs

    def get_queryset(self):
        opportunity_id = self.kwargs["pk"]
        opportunity = get_opportunity_or_404(org_slug=self.request.org.slug, pk=opportunity_id)
        filter_kwargs = dict(opportunity=opportunity)
        table_filter = self.request.GET.get("filter")
        if table_filter is not None and table_filter in ["paid", "pending"]:
            filter_kwargs["payment__isnull"] = table_filter == "pending"
        return PaymentInvoice.objects.filter(**filter_kwargs).order_by("date")


@org_member_required
def invoice_list(request, org_slug, pk):
    opportunity = get_opportunity_or_404(pk, org_slug)
    if not opportunity.managed:
        return redirect("opportunity:detail", org_slug, pk)
    form = PaymentInvoiceForm(opportunity=opportunity)
    return render(
        request,
        "opportunity/invoice_list.html",
        context=dict(opportunity=opportunity, form=form),
    )


@org_member_required
def invoice_create(request, org_slug, pk):
    opportunity = get_opportunity_or_404(pk, org_slug)
    if not opportunity.managed or request.org_membership.is_program_manager:
        return redirect("opportunity:detail", org_slug, pk)
    form = PaymentInvoiceForm(data=request.POST or None, opportunity=opportunity)
    if request.POST and form.is_valid():
        form.save()
        form = PaymentInvoiceForm(opportunity=opportunity)
        return HttpResponse(render_crispy_form(form), headers={"HX-Trigger": "newInvoice"})
    return HttpResponse(render_crispy_form(form))


@org_member_required
@require_POST
def invoice_approve(request, org_slug, pk):
    opportunity = get_opportunity_or_404(pk, org_slug)
    if not opportunity.managed or not request.org_membership.is_program_manager:
        return redirect("opportunity:detail", org_slug, pk)
    invoice_ids = request.POST.getlist("pk")
    invoices = PaymentInvoice.objects.filter(opportunity=opportunity, pk__in=invoice_ids, payment__isnull=True)
    rate = get_exchange_rate(opportunity.currency)
    for invoice in invoices:
        amount_in_usd = invoice.amount / rate
        payment = Payment(
            amount=invoice.amount,
            organization=opportunity.organization,
            amount_usd=amount_in_usd,
            invoice=invoice,
        )
        payment.save()
    return HttpResponse(headers={"HX-Trigger": "newInvoice"})


@org_member_required
@require_POST
@csrf_exempt
def user_invite_delete(request, org_slug, opp_id, pk):
    opportunity = get_opportunity_or_404(opp_id, org_slug)
    invite = get_object_or_404(UserInvite, pk=pk, opportunity=opportunity)
    if invite.status != UserInviteStatus.not_found:
        return HttpResponse(status=403, data="User Invite cannot be deleted.")
    invite.delete()
    return HttpResponse(status=200, headers={"HX-Trigger": "userStatusReload"})


@org_admin_required
@require_POST
def resend_user_invite(request, org_slug, opp_id, pk):
    user_invite = get_object_or_404(UserInvite, id=pk)

    if user_invite.notification_date and (now() - user_invite.notification_date) < datetime.timedelta(days=1):
        return HttpResponse("You can only send one invitation per user every 24 hours. Please try again later.")

    if user_invite.status == UserInviteStatus.not_found:
        found_user_list = fetch_users([user_invite.phone_number])
        if not found_user_list:
            return HttpResponse("The user is not registered on Connect ID yet. Please ask them to sign up first.")

        connect_user = found_user_list[0]
        update_user_and_send_invite(connect_user, opp_id=pk)
    else:
        user = User.objects.get(phone_number=user_invite.phone_number)
        access, _ = OpportunityAccess.objects.get_or_create(user=user, opportunity_id=opp_id)
        invite_user.delay(user.id, access.pk)

    return HttpResponse("The invitation has been successfully resent to the user.")

def worker_list(request, org_slug=None, opp_id=None):
    return render(request, "opportunity/tailwind/workers.html")


def worker_learn(request, org_slug=None, opp_id=None):
    return render(request, "opportunity/tailwind/learn.html")


def worker_deliver(request, org_slug=None, opp_id=None):
    return render(request, "opportunity/tailwind/deliver.html")


def worker_verify(request, org_slug=None, opp_id=None):
    return render(request, "opportunity/tailwind/verify.html")


def sync_deliver_units(request, org_slug, opp_id):
    status = HTTPStatus.OK
    message = "Delivery unit sync completed."
    try:
        create_learn_modules_and_deliver_units(opp_id)
    except AppNoBuildException:
        status = HTTPStatus.BAD_REQUEST
        message = "Failed to retrieve updates. No available build at the moment."

    return HttpResponse(content=message, status=status)

