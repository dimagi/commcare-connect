import json
from functools import reduce

from celery.result import AsyncResult
from django.conf import settings
from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.core.files.storage import storages
from django.db.models import F
from django.http import FileResponse, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.html import format_html
from django.utils.safestring import mark_safe
from django.utils.text import slugify
from django.utils.timezone import now
from django.views.decorators.http import require_GET, require_POST
from django.views.generic import CreateView, DetailView, ListView, UpdateView
from django_tables2 import SingleTableView
from django_tables2.export import TableExport
from geopy import distance

from commcare_connect.form_receiver.serializers import XFormSerializer
from commcare_connect.opportunity.forms import (
    AddBudgetExistingUsersForm,
    DateRanges,
    OpportunityChangeForm,
    OpportunityCreationForm,
    PaymentExportForm,
    PaymentUnitForm,
    SendMessageMobileUsersForm,
    VisitExportForm,
)
from commcare_connect.opportunity.helpers import (
    get_annotated_opportunity_access,
    get_annotated_opportunity_access_deliver_status,
)
from commcare_connect.opportunity.models import (
    BlobMeta,
    CompletedModule,
    DeliverUnit,
    Opportunity,
    OpportunityAccess,
    OpportunityClaim,
    Payment,
    PaymentUnit,
    UserVisit,
    VisitValidationStatus,
)
from commcare_connect.opportunity.tables import (
    DeliverStatusTable,
    LearnStatusTable,
    OpportunityPaymentTable,
    PaymentUnitTable,
    UserPaymentsTable,
    UserStatusTable,
    UserVisitTable,
)
from commcare_connect.opportunity.tasks import (
    add_connect_users,
    create_learn_modules_and_deliver_units,
    generate_deliver_status_export,
    generate_payment_export,
    generate_user_status_export,
    generate_visit_export,
    send_push_notification_task,
    send_sms_task,
)
from commcare_connect.opportunity.visit_import import (
    ImportException,
    bulk_update_payment_status,
    bulk_update_visit_status,
    update_payment_accrued,
)
from commcare_connect.organization.decorators import org_admin_required, org_member_required
from commcare_connect.users.models import User
from commcare_connect.utils.commcarehq_api import get_applications_for_user_by_domain, get_domains_for_user


class OrganizationUserMixin(LoginRequiredMixin, UserPassesTestMixin):
    def test_func(self):
        # request.org_membership is a SimpleLazyObject object so `is not None` is always `True`
        return self.request.org_membership != None  # noqa: E711


class OpportunityList(OrganizationUserMixin, ListView):
    model = Opportunity
    paginate_by = 10

    def get_queryset(self):
        return Opportunity.objects.filter(organization=self.request.org).order_by("name")


class OpportunityCreate(OrganizationUserMixin, CreateView):
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


class OpportunityEdit(OrganizationUserMixin, UpdateView):
    model = Opportunity
    template_name = "opportunity/opportunity_edit.html"
    form_class = OpportunityChangeForm

    def get_success_url(self):
        return reverse("opportunity:detail", args=(self.request.org.slug, self.object.id))

    def form_valid(self, form):
        opportunity = form.instance
        opportunity.modified_by = self.request.user.email
        users = form.cleaned_data["users"]
        if users:
            add_connect_users.delay(users, form.instance.id)
        additional_users = form.cleaned_data["additional_users"]
        if additional_users:
            opportunity.total_budget += (
                opportunity.budget_per_visit * opportunity.max_visits_per_user * additional_users
            )
        end_date = form.cleaned_data["end_date"]
        if end_date:
            opportunity.end_date = end_date
        response = super().form_valid(form)
        return response


class OpportunityDetail(OrganizationUserMixin, DetailView):
    model = Opportunity
    template_name = "opportunity/opportunity_detail.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["visit_export_form"] = VisitExportForm()
        context["export_task_id"] = self.request.GET.get("export_task_id")
        context["export_form"] = PaymentExportForm()
        return context


class OpportunityLearnStatusTableView(OrganizationUserMixin, SingleTableView):
    model = OpportunityAccess
    paginate_by = 25
    table_class = LearnStatusTable
    template_name = "tables/single_table.html"

    def get_queryset(self):
        opportunity_id = self.kwargs["pk"]
        opportunity = get_object_or_404(Opportunity, organization=self.request.org, id=opportunity_id)
        return OpportunityAccess.objects.filter(opportunity=opportunity).order_by("user__name")


class OpportunityPaymentTableView(OrganizationUserMixin, SingleTableView):
    model = OpportunityAccess
    paginate_by = 25
    table_class = OpportunityPaymentTable
    template_name = "tables/single_table.html"

    def get_queryset(self):
        opportunity_id = self.kwargs["pk"]
        opportunity = get_object_or_404(Opportunity, organization=self.request.org, id=opportunity_id)
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
        self.opportunity = get_object_or_404(Opportunity, organization=self.request.org, id=opportunity_id)
        access_id = self.kwargs["pk"]
        self.access = get_object_or_404(OpportunityAccess, opportunity=self.opportunity, pk=access_id)
        return Payment.objects.filter(opportunity_access=self.access).order_by("-date_paid")


class OpportunityUserLearnProgress(OrganizationUserMixin, DetailView):
    template_name = "opportunity/user_learn_progress.html"

    def get_queryset(self):
        return OpportunityAccess.objects.filter(opportunity_id=self.kwargs.get("opp_id"))

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["completed_modules"] = CompletedModule.objects.filter(
            user=self.object.user,
            opportunity_id=self.kwargs.get("opp_id"),
        )
        return context


@org_member_required
def export_user_visits(request, **kwargs):
    opportunity_id = kwargs["pk"]
    get_object_or_404(Opportunity, organization=request.org, id=opportunity_id)
    form = VisitExportForm(data=request.POST)
    if not form.is_valid():
        messages.error(request, form.errors)
        return redirect("opportunity:detail", request.org.slug, opportunity_id)

    export_format = form.cleaned_data["format"]
    date_range = DateRanges(form.cleaned_data["date_range"])
    status = form.cleaned_data["status"]

    result = generate_visit_export.delay(opportunity_id, date_range, status, export_format)
    redirect_url = reverse("opportunity:detail", args=(request.org.slug, opportunity_id))
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
    opportunity = get_object_or_404(Opportunity, organization=request.org, id=opportunity_id)
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
    opportunity = get_object_or_404(Opportunity, organization=request.org, id=pk)
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
    return redirect("opportunity:detail", org_slug, pk)


@org_member_required
def add_budget_existing_users(request, org_slug=None, pk=None):
    opportunity = get_object_or_404(Opportunity, organization=request.org, id=pk)
    opportunity_access = OpportunityAccess.objects.filter(opportunity=opportunity)
    opportunity_claims = OpportunityClaim.objects.filter(opportunity_access__in=opportunity_access)
    form = AddBudgetExistingUsersForm(opportunity_claims=opportunity_claims)

    if request.method == "POST":
        form = AddBudgetExistingUsersForm(opportunity_claims=opportunity_claims, data=request.POST)
        if form.is_valid():
            selected_users = form.cleaned_data["selected_users"]
            additional_visits = form.cleaned_data["additional_visits"]
            update_kwargs = {"max_payments": F("max_payments") + additional_visits}
            if form.cleaned_data["end_date"]:
                update_kwargs.update({"end_date": form.cleaned_data["end_date"]})
            OpportunityClaim.objects.filter(pk__in=selected_users).update(**update_kwargs)
            opportunity.total_budget += additional_visits * opportunity.budget_per_visit * len(selected_users)
            opportunity.save()
            return redirect("opportunity:detail", org_slug, pk)

    return render(
        request,
        "opportunity/add_visits_existing_users.html",
        {
            "form": form,
            "opportunity_claims": opportunity_claims,
            "budget_per_visit": opportunity.budget_per_visit,
            "opportunity": opportunity,
        },
    )


class OpportunityUserStatusTableView(OrganizationUserMixin, SingleTableView):
    model = OpportunityAccess
    paginate_by = 25
    table_class = UserStatusTable
    template_name = "tables/single_table.html"

    def get_queryset(self):
        opportunity_id = self.kwargs["pk"]
        opportunity = get_object_or_404(Opportunity, organization=self.request.org, id=opportunity_id)
        access_objects = get_annotated_opportunity_access(opportunity)
        return access_objects


@org_member_required
def export_users_for_payment(request, **kwargs):
    opportunity_id = kwargs["pk"]
    get_object_or_404(Opportunity, organization=request.org, id=opportunity_id)
    form = PaymentExportForm(data=request.POST)
    if not form.is_valid():
        messages.error(request, form.errors)
        return redirect("opportunity:detail", request.org.slug, opportunity_id)

    export_format = form.cleaned_data["format"]
    result = generate_payment_export.delay(opportunity_id, export_format)
    redirect_url = reverse("opportunity:detail", args=(request.org.slug, opportunity_id))
    return redirect(f"{redirect_url}?export_task_id={result.id}")


@org_member_required
@require_POST
def payment_import(request, org_slug=None, pk=None):
    opportunity = get_object_or_404(Opportunity, organization=request.org, id=pk)
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
def add_payment_unit(request, org_slug=None, pk=None):
    opportunity = get_object_or_404(Opportunity, organization=request.org, id=pk)
    deliver_units = DeliverUnit.objects.filter(app=opportunity.deliver_app, payment_unit__isnull=True)
    form = PaymentUnitForm(deliver_units=deliver_units, data=request.POST or None)
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
        messages.success(request, f"Payment unit {form.instance.name} created.")
        return redirect("opportunity:detail", org_slug=request.org.slug, pk=opportunity.id)
    return render(
        request,
        "form.html",
        dict(title=f"{request.org.slug} - {opportunity.name}", form_title="Payment Unit Create", form=form),
    )


@org_member_required
def edit_payment_unit(request, org_slug=None, opp_id=None, pk=None):
    opportunity = get_object_or_404(Opportunity, organization=request.org, id=opp_id)
    payment_unit = get_object_or_404(PaymentUnit, id=pk, opportunity=opportunity)
    deliver_units = DeliverUnit.objects.filter(app=opportunity.deliver_app)
    payment_unit_deliver_units = {deliver_unit.pk for deliver_unit in payment_unit.deliver_units.all()}
    form = PaymentUnitForm(deliver_units=deliver_units, instance=payment_unit, data=request.POST or None)
    if form.is_valid():
        form.save()
        required_deliver_units = form.cleaned_data["required_deliver_units"]
        DeliverUnit.objects.filter(id__in=required_deliver_units).update(payment_unit=form.instance.id, optional=False)
        optional_deliver_units = form.cleaned_data["optional_deliver_units"]
        DeliverUnit.objects.filter(id__in=optional_deliver_units).update(payment_unit=form.instance.id, optional=True)
        # Remove deliver units which are not selected anymore
        deliver_units = required_deliver_units + optional_deliver_units
        removed_deliver_units = payment_unit_deliver_units - {int(deliver_unit) for deliver_unit in deliver_units}
        DeliverUnit.objects.filter(id__in=removed_deliver_units).update(payment_unit=None, optional=False)
        messages.success(request, f"Payment unit {form.instance.name} updated.")
        return redirect("opportunity:detail", org_slug=request.org.slug, pk=opportunity.id)
    return render(
        request,
        "form.html",
        dict(title=f"{request.org.slug} - {opportunity.name}", form_title="Payment Unit Edit", form=form),
    )


class OpportunityPaymentUnitTableView(OrganizationUserMixin, SingleTableView):
    model = PaymentUnit
    paginate_by = 25
    table_class = PaymentUnitTable
    template_name = "tables/single_table.html"

    def get_queryset(self):
        opportunity_id = self.kwargs["pk"]
        opportunity = get_object_or_404(Opportunity, organization=self.request.org, id=opportunity_id)
        return PaymentUnit.objects.filter(opportunity=opportunity).order_by("name")


@org_member_required
def export_user_status(request, **kwargs):
    opportunity_id = kwargs["pk"]
    get_object_or_404(Opportunity, organization=request.org, id=opportunity_id)
    form = PaymentExportForm(data=request.POST)
    if not form.is_valid():
        messages.error(request, form.errors)
        return redirect("opportunity:detail", request.org.slug, opportunity_id)

    export_format = form.cleaned_data["format"]
    result = generate_user_status_export.delay(opportunity_id, export_format)
    redirect_url = reverse("opportunity:detail", args=(request.org.slug, opportunity_id))
    return redirect(f"{redirect_url}?export_task_id={result.id}")


class OpportunityDeliverStatusTable(OrganizationUserMixin, SingleTableView):
    model = OpportunityAccess
    paginate_by = 25
    table_class = DeliverStatusTable
    template_name = "tables/single_table.html"

    def get_queryset(self):
        opportunity_id = self.kwargs["pk"]
        opportunity = get_object_or_404(Opportunity, organization=self.request.org, id=opportunity_id)
        access_objects = get_annotated_opportunity_access_deliver_status(opportunity)
        return access_objects


@org_member_required
def export_deliver_status(request, **kwargs):
    opportunity_id = kwargs["pk"]
    get_object_or_404(Opportunity, organization=request.org, id=opportunity_id)
    form = PaymentExportForm(data=request.POST)
    if not form.is_valid():
        messages.error(request, form.errors)
        return redirect("opportunity:detail", request.org.slug, opportunity_id)

    export_format = form.cleaned_data["format"]
    result = generate_deliver_status_export.delay(opportunity_id, export_format)
    redirect_url = reverse("opportunity:detail", args=(request.org.slug, opportunity_id))
    return redirect(f"{redirect_url}?export_task_id={result.id}")


@org_member_required
def user_visits_list(request, org_slug=None, opp_id=None, pk=None):
    opportunity = get_object_or_404(Opportunity, organization=request.org, id=opp_id)
    opportunity_access = get_object_or_404(OpportunityAccess, pk=pk, opportunity=opportunity)
    user_visits = UserVisit.objects.filter(user=opportunity_access.user, opportunity=opportunity).order_by(
        "visit_date"
    )
    user_visits_table = UserVisitTable(user_visits)
    return render(
        request,
        "opportunity/user_visits_list.html",
        context=dict(opportunity=opportunity, table=user_visits_table, user_name=opportunity_access.display_name),
    )


@org_member_required
@require_POST
def payment_delete(request, org_slug=None, opp_id=None, access_id=None, pk=None):
    opportunity = get_object_or_404(Opportunity, organization=request.org, pk=opp_id)
    opportunity_access = get_object_or_404(OpportunityAccess, pk=access_id, opportunity=opportunity)
    payment = get_object_or_404(Payment, opportunity_access=opportunity_access, pk=pk)
    payment.delete()
    return redirect("opportunity:user_payments_table", org_slug=org_slug, opp_id=opp_id, pk=access_id)


@org_member_required
def user_profile(request, org_slug=None, opp_id=None, pk=None):
    access = get_object_or_404(OpportunityAccess, pk=pk, accepted=True)
    user_visits = UserVisit.objects.filter(user=access.user, opportunity=access.opportunity)
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
    return render(
        request,
        "opportunity/user_profile.html",
        context=dict(
            access=access,
            user_visits=user_visit_data,
            lat_avg=lat_avg,
            lng_avg=lng_avg,
            MAPBOX_TOKEN=settings.MAPBOX_TOKEN,
        ),
    )


@org_admin_required
def send_message_mobile_users(request, org_slug=None, pk=None):
    opportunity = get_object_or_404(Opportunity, pk=pk, organization=request.org)
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
    options = []
    for app in applications:
        value = json.dumps(app)
        name = app["name"]
        options.append(format_html("<option value='{}'>{}</option>", value, name))
    return HttpResponse("\n".join(options))


@org_member_required
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
        },
    )


@org_member_required
def approve_visit(request, org_slug=None, pk=None):
    user_visit = UserVisit.objects.get(pk=pk)
    user_visit.status = VisitValidationStatus.approved
    user_visit.save()
    opp_id = user_visit.opportunity_id
    access = OpportunityAccess.objects.get(user_id=user_visit.user_id, opportunity_id=opp_id)
    update_payment_accrued(opportunity=access.opportunity, users=[access.user])
    return redirect("opportunity:user_visits_list", org_slug=org_slug, opp_id=user_visit.opportunity.id, pk=access.id)


@org_member_required
def reject_visit(request, org_slug=None, pk=None):
    user_visit = UserVisit.objects.get(pk=pk)
    user_visit.status = VisitValidationStatus.rejected
    user_visit.save()
    access = OpportunityAccess.objects.get(user_id=user_visit.user_id, opportunity_id=user_visit.opportunity_id)
    update_payment_accrued(opportunity=access.opportunity, users=[access.user])
    return redirect("opportunity:user_visits_list", org_slug=org_slug, opp_id=user_visit.opportunity_id, pk=access.id)


@org_member_required
def fetch_attachment(self, org_slug, blob_id):
    blob_meta = BlobMeta.objects.get(blob_id=blob_id)
    attachment = storages["default"].open(blob_id)
    return FileResponse(attachment, filename=blob_meta.name, content_type=blob_meta.content_type)
