"""Invoice pages: the list, creating and reviewing invoices, status changes, payment, exports and Finance requests."""

import datetime
import json
from decimal import Decimal, InvalidOperation
from functools import partial
from urllib.parse import urlencode

import pghistory
from celery.result import AsyncResult
from django.contrib import messages
from django.db import transaction
from django.db.models import Case, IntegerField, Max, Value, When
from django.http import Http404, HttpResponse, HttpResponseBadRequest, JsonResponse
from django.middleware.csrf import get_token
from django.shortcuts import get_object_or_404, redirect, render
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils.decorators import method_decorator
from django.utils.html import format_html
from django.utils.translation import gettext as _
from django.views import View
from django.views.decorators.http import require_GET, require_POST
from django.views.generic import CreateView, DetailView
from django_tables2 import RequestConfig
from django_tables2.export import TableExport
from django_weasyprint.views import WeasyTemplateResponse

from commcare_connect.jira_service_desk import is_configured as is_jira_service_desk_configured
from commcare_connect.opportunity.forms.invoices import (
    AutomatedPaymentInvoiceForm,
    InvoiceExportForm,
    InvoiceFinanceRequestForm,
    PaymentInvoiceInvoiceTicketLinkForm,
)
from commcare_connect.opportunity.models import (
    ExchangeRate,
    InvoiceFinanceRequestStatus,
    InvoiceStatus,
    Payment,
    PaymentInvoice,
)
from commcare_connect.opportunity.tables.invoices import (
    InvoiceDeliveriesTable,
    InvoiceLineItemsTable,
    PaymentInvoiceTable,
)
from commcare_connect.opportunity.tasks import (
    generate_invoice_pdf_zip_export,
    generate_invoice_summary_export,
    send_invoice_paid_mail,
    submit_invoice_finance_request,
)
from commcare_connect.opportunity.utils.finance_request import (
    can_submit_finance_request,
    fail_stuck_finance_request,
    get_previous_finance_request,
    suggest_new_vendor,
)
from commcare_connect.opportunity.utils.invoice import (
    InvoiceWorkflow,
    filter_invoices_by_month,
    get_invoice_month_options,
    invoice_month_param,
    resolve_invoice_month,
    split_month_options,
    with_invoice_month,
)
from commcare_connect.opportunity.utils.invoice_export import (
    get_exportable_invoices,
    get_invoice_pdf_context,
    invoice_pdf_filename,
)
from commcare_connect.opportunity.utils.invoice_line_items import (
    Money,
    get_billable_delivery_rows_for_export,
    get_billable_line_items,
    get_invoice_delivery_rows_for_export,
    get_invoice_line_items,
    rollback_invoice_line_items,
    total_late_delta_units,
)
from commcare_connect.opportunity.views import OpportunityObjectMixin
from commcare_connect.organization.decorators import (
    opp_standard_access_required,
    opp_view_access_required,
    opportunity_pm_required,
    opportunity_required,
)
from commcare_connect.utils.tables import get_validated_page_size

INVOICE_EXPORT_TASKS = {
    InvoiceExportForm.PDF_ZIP: generate_invoice_pdf_zip_export,
    InvoiceExportForm.CSV_SUMMARY: generate_invoice_summary_export,
}
# Task id of the invoice export this user started whose outcome has not been shown yet.
INVOICE_EXPORT_SESSION_KEY = "pending_invoice_export"


@opp_standard_access_required
@opportunity_required
def invoice_list(request, org_slug, opp_id):
    filter_kwargs = dict(opportunity=request.opportunity)

    highlight_invoice_number = request.GET.get("highlight")
    export_task_id = request.GET.get("export_task_id")
    # The id stays in the URL, so only trust it while it is the export this user started. A
    # running export keeps showing its progress; a finished one is shown once, then forgotten.
    if not export_task_id or export_task_id != request.session.get(INVOICE_EXPORT_SESSION_KEY):
        export_task_id = None
    elif AsyncResult(export_task_id).ready():
        del request.session[INVOICE_EXPORT_SESSION_KEY]

    all_invoices = PaymentInvoice.objects.filter(**filter_kwargs)
    month_options = get_invoice_month_options(all_invoices)
    selected_month = resolve_invoice_month(request.GET.get("month"), month_options, highlight_invoice_number)
    month_chips, older_months = split_month_options(month_options, selected_month)

    queryset = all_invoices.select_related("exchange_rate").annotate(
        last_status_modified_at=Max("status_events__pgh_created_at")
    )
    if selected_month:
        queryset = filter_invoices_by_month(queryset, selected_month)
    queryset = queryset.order_by("date")

    if highlight_invoice_number:  # make sure highlighted invoice is on page 1
        queryset = queryset.annotate(
            _highlight_order=Case(
                When(invoice_number=highlight_invoice_number, then=Value(0)),
                default=Value(1),
                output_field=IntegerField(),
            )
        ).order_by("_highlight_order", "date")

    csrf_token = get_token(request)

    table = PaymentInvoiceTable(
        queryset,
        org_slug=org_slug,
        opportunity=request.opportunity,
        csrf_token=csrf_token,
        highlight_invoice_number=highlight_invoice_number,
        is_pm=request.is_opportunity_pm,
        month_param=invoice_month_param(selected_month),
    )

    RequestConfig(request, paginate={"per_page": get_validated_page_size(request)}).configure(table)
    return render(
        request,
        "opportunity/invoice_list.html",
        {
            "opportunity": request.opportunity,
            "table": table,
            "month_chips": month_chips,
            "older_months": older_months,
            "selected_month": selected_month,
            "invoice_count": table.paginator.count,
            "export_task_id": export_task_id,
            # The month the page is actually showing, not the raw query value: an export
            # started from here must cover exactly what the user is looking at.
            "month_param": invoice_month_param(selected_month),
            "exportable_count": get_exportable_invoices(request.opportunity, selected_month).count(),
            "export_url": reverse("opportunity:export_invoices", args=(org_slug, opp_id)),
            "new_invoice_url": reverse(
                "opportunity:invoice_create",
                args=(org_slug, request.opportunity.opportunity_id),
            ),
            # Same rule as InvoiceCreateView: only the opportunity's own (NM) org creates invoices.
            "can_create_invoice": request.org.id == request.opportunity.organization_id,
            "path": [
                {"title": "Opportunities", "url": reverse("opportunity:list", args=(org_slug,))},
                {
                    "title": request.opportunity.name,
                    "url": reverse("opportunity:detail", args=(org_slug, request.opportunity.opportunity_id)),
                },
                {
                    "title": "Invoices",
                    "url": reverse("opportunity:invoice_list", args=(org_slug, request.opportunity.opportunity_id)),
                },
            ],
        },
    )


@method_decorator(opp_standard_access_required, name="dispatch")
class InvoiceCreateView(OpportunityObjectMixin, CreateView):
    model = PaymentInvoice
    template_name = "opportunity/invoice_create.html"
    form_class = AutomatedPaymentInvoiceForm

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        opportunity = self.get_opportunity()
        org_slug = self.request.org.slug

        context.update(
            {
                "opportunity": opportunity,
                "is_service_delivery": self.request.GET.get("invoice_type")
                == PaymentInvoice.InvoiceType.service_delivery,
                "path": [
                    {"title": "Opportunities", "url": reverse("opportunity:list", args=(org_slug,))},
                    {
                        "title": opportunity.name,
                        "url": reverse("opportunity:detail", args=(org_slug, opportunity.opportunity_id)),
                    },
                    {
                        "title": "Invoices",
                        "url": reverse("opportunity:invoice_list", args=(org_slug, opportunity.opportunity_id)),
                    },
                    {
                        "title": self.breadcrumb_title,
                        "url": reverse("opportunity:invoice_create", args=(org_slug, opportunity.opportunity_id)),
                    },
                ],
            }
        )
        return context

    @property
    def breadcrumb_title(self):
        service_delivery = PaymentInvoice.InvoiceType.service_delivery
        if self.request.GET.get("invoice_type", service_delivery) == service_delivery:
            return "New Service Delivery Invoice"
        return "New Custom Invoice"

    def get(self, request, org_slug, opp_id, **kwargs):
        self._check_opportunity_org(request)
        return super().get(request, org_slug, opp_id, **kwargs)

    def _check_opportunity_org(self, request):
        # Only NM org user is allowed to create invoice
        if request.org != request.opportunity.organization:
            raise Http404()

    def post(self, request, org_slug, opp_id, **kwargs):
        self._check_opportunity_org(request)

        form = self.get_form()
        if not form.is_valid():
            # Re-render the bound form to preserve validation errors.
            self.object = None
            return self.form_invalid(form)

        form.save()
        return redirect(reverse("opportunity:invoice_list", args=[org_slug, opp_id]))

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["opportunity"] = self.get_opportunity()
        kwargs["invoice_type"] = self.request.GET.get("invoice_type", PaymentInvoice.InvoiceType.service_delivery)
        kwargs["status"] = InvoiceStatus.PENDING_NM_REVIEW
        kwargs["is_opportunity_pm"] = self.request.is_opportunity_pm
        return kwargs

    def get_success_url(self):
        return reverse("opportunity:invoice_list", args=(self.request.org.slug, self.get_opportunity().opportunity_id))


@method_decorator(opp_view_access_required, name="dispatch")
class InvoiceReviewView(OpportunityObjectMixin, DetailView):
    model = PaymentInvoice
    template_name = "opportunity/invoice_detail.html"

    def get_object(self, queryset=None):
        opportunity = self.get_opportunity()
        return get_object_or_404(
            PaymentInvoice,
            payment_invoice_id=self.kwargs.get("pk"),
            opportunity_id=opportunity.id,
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        invoice = self.object
        opportunity = invoice.opportunity
        org_slug = self.request.org.slug
        form = self.get_form()
        month_param = self.request.GET.get("month")
        invoice_list_url = with_invoice_month(
            reverse("opportunity:invoice_list", args=(org_slug, opportunity.opportunity_id)), month_param
        )
        context.update(
            {
                "opportunity": opportunity,
                "form": form,
                "month_param": month_param,
                "invoice_list_url": invoice_list_url,
                "is_service_delivery": invoice.service_delivery,
                "invoice_status": invoice.status,
                "line_item_count": len(form.line_items_table.rows) if form.line_items_table else None,
                "path": [
                    {"title": "Opportunities", "url": reverse("opportunity:list", args=(org_slug,))},
                    {
                        "title": opportunity.name,
                        "url": reverse("opportunity:detail", args=(org_slug, opportunity.opportunity_id)),
                    },
                    {"title": "Invoices", "url": invoice_list_url},
                    {
                        "title": self.breadcrumb_title,
                        "url": reverse(
                            "opportunity:invoice_review",
                            args=(org_slug, opportunity.opportunity_id, invoice.payment_invoice_id),
                        ),
                    },
                ],
            }
        )
        context["show_payment_invoice_invoice_ticket_link_form"] = self.request.is_opportunity_pm
        context["show_finance_request"] = self.request.is_opportunity_pm and (
            invoice.status == InvoiceStatus.READY_TO_PAY or hasattr(invoice, "finance_request")
        )
        if context["show_finance_request"]:
            context.update(get_finance_request_context(invoice))
        return context

    def get_form(self):
        invoice = self.object
        opportunity = invoice.opportunity
        invoice_type = (
            PaymentInvoice.InvoiceType.service_delivery
            if invoice.service_delivery
            else PaymentInvoice.InvoiceType.custom
        )

        line_items_table = None
        late_delta_units = 0
        if invoice.service_delivery:
            line_items = get_invoice_line_items(invoice)
            late_delta_units = total_late_delta_units(line_items)
            show_org = any(item.org_pay.local for item in line_items)
            line_items_table = InvoiceLineItemsTable(opportunity.currency_code, line_items, show_org=show_org)
        return AutomatedPaymentInvoiceForm(
            instance=invoice,
            opportunity=opportunity,
            invoice_type=invoice_type,
            line_items_table=line_items_table,
            late_delta_units=late_delta_units,
            read_only=True,
            is_opportunity_pm=self.request.is_opportunity_pm,
        )

    @property
    def breadcrumb_title(self):
        if self.object.service_delivery:
            return _("Review Service Delivery Invoice")
        return _("Review Custom Invoice")


@opportunity_pm_required
@opportunity_required
@require_POST
def update_invoice_invoice_ticket_link(request, org_slug, opp_id, invoice_id):
    invoice = get_object_or_404(
        PaymentInvoice,
        payment_invoice_id=invoice_id,
        opportunity=request.opportunity,
    )

    form = PaymentInvoiceInvoiceTicketLinkForm(request.POST)
    if form.is_valid():
        link = form.cleaned_data["invoice_ticket_link"]
        if link:
            invoice.invoice_ticket_link = link
            invoice.save(update_fields=["invoice_ticket_link"])
            messages.success(request, _("Invoice ticket link saved!"))
        elif invoice.invoice_ticket_link:
            invoice.invoice_ticket_link = link
            invoice.save(update_fields=["invoice_ticket_link"])
            messages.success(request, _("Invoice ticket link removed!"))
        else:
            messages.error(request, _("Enter a ticket link."))
    else:
        messages.error(request, _("Error: {errors}").format(errors=form.errors.as_text()))
    return redirect("opportunity:invoice_review", org_slug, opp_id, invoice_id)


@method_decorator(opportunity_pm_required, name="dispatch")
class InvoiceFinanceRequestView(OpportunityObjectMixin, View):
    """Send an approved invoice to Finance as a Jira service desk request, and show how that is going.

    Both methods return the invoice page's Finance card, which htmx swaps in place.
    """

    template_name = "opportunity/partials/invoice_finance_request.html"

    def get(self, request, *args, **kwargs):
        return render(request, self.template_name, get_finance_request_context(self.get_invoice()))

    def post(self, request, *args, **kwargs):
        # Locking the invoice stops a double click from raising two tickets.
        invoice = self.get_invoice(for_update=True)
        fail_stuck_finance_request(invoice)
        if not is_jira_service_desk_configured() or not can_submit_finance_request(invoice):
            return render(request, self.template_name, get_finance_request_context(invoice))

        form = InvoiceFinanceRequestForm(request.POST, instance=getattr(invoice, "finance_request", None))
        if not form.is_valid():
            return render(request, self.template_name, get_finance_request_context(invoice, form=form))

        finance_request = form.save(commit=False)
        finance_request.invoice = invoice
        finance_request.status = InvoiceFinanceRequestStatus.PENDING
        finance_request.error = ""
        finance_request.submitted_by = request.user
        finance_request.created_by = finance_request.created_by or request.user.email
        finance_request.modified_by = request.user.email
        finance_request.save()
        invoice.finance_request = finance_request
        transaction.on_commit(partial(submit_invoice_finance_request.delay, finance_request.id))
        return render(request, self.template_name, get_finance_request_context(invoice))

    def get_invoice(self, for_update=False):
        queryset = PaymentInvoice.objects.select_related("opportunity__organization")
        if for_update:
            # The finance request is left to a query of its own, run once the lock is held: a join
            # here would read it as it was before a concurrent submission committed.
            queryset = queryset.select_for_update(of=("self",))
        else:
            queryset = queryset.select_related("finance_request")
        return get_object_or_404(
            queryset, opportunity=self.get_opportunity(), payment_invoice_id=self.kwargs["invoice_id"]
        )


def get_finance_request_context(invoice, form=None):
    fail_stuck_finance_request(invoice)
    finance_request = getattr(invoice, "finance_request", None)
    configured = is_jira_service_desk_configured()
    can_submit = configured and can_submit_finance_request(invoice)
    if can_submit and form is None:
        # A failed request is resent with the answers it was given; `initial` would override them.
        initial = None if finance_request else _finance_request_initial(invoice)
        form = InvoiceFinanceRequestForm(instance=finance_request, initial=initial)
    return {
        "invoice": invoice,
        "opportunity": invoice.opportunity,
        "finance_request": finance_request,
        "finance_request_configured": configured,
        "finance_request_form": form if can_submit else None,
    }


def _finance_request_initial(invoice):
    """Answers from the opportunity's last request, which usually stay the same from invoice to invoice."""
    initial = {"new_vendor": suggest_new_vendor(invoice.opportunity.organization)}
    if previous := get_previous_finance_request(invoice.opportunity):
        initial.update(
            contracted_entity=previous.contracted_entity,
            gl_account=previous.gl_account,
            project_name=previous.project_name,
        )
    return initial


@opp_standard_access_required
@opportunity_required
def download_invoice(request, org_slug, opp_id, invoice_id):
    invoice = get_object_or_404(
        PaymentInvoice.objects.select_related("opportunity", "exchange_rate", "payment"),
        opportunity=request.opportunity,
        payment_invoice_id=invoice_id,
    )
    return WeasyTemplateResponse(
        request=request,
        template="opportunity/invoice_download.html",
        context=get_invoice_pdf_context(invoice),
        content_type="application/pdf",
        filename=invoice_pdf_filename(invoice),
    )


@opp_standard_access_required
@opportunity_required
@require_POST
def export_invoices(request, org_slug, opp_id):
    """Start a bulk invoice export and send the user back to the list to watch it finish."""
    form = InvoiceExportForm(data=request.POST, opportunity=request.opportunity)
    redirect_url = reverse("opportunity:invoice_list", args=(org_slug, opp_id))

    if not form.is_valid():
        messages.error(request, _("That export request was not valid. Please try again."))
        return redirect(redirect_url)

    invoices = form.get_invoices()
    if not invoices:
        messages.error(request, _("There are no invoices to export."))
        return redirect(redirect_url)

    invoice_ids = [invoice.pk for invoice in invoices]
    # A bare delay() is safe here: the export only reads rows that were committed long ago, so
    # there is nothing for the worker to race with.
    task = INVOICE_EXPORT_TASKS[form.cleaned_data["export_type"]].delay(request.opportunity.pk, invoice_ids)
    request.session[INVOICE_EXPORT_SESSION_KEY] = task.id

    query = {"export_task_id": task.id}
    if form.cleaned_data["month"]:
        query["month"] = form.cleaned_data["month"]
    return redirect(f"{redirect_url}?{urlencode(query)}")


@opp_standard_access_required
@opportunity_required
@require_POST
def invoice_update_status(request, org_slug, opp_id):
    """
    Update invoice status. Handles multiple status transitions based on user role:
    - Network Manager: PENDING_NM_REVIEW -> PENDING_PM_REVIEW (submit to PM) or CANCELLED_BY_NM (cancel)
    - Program Manager: PENDING_PM_REVIEW -> READY_TO_PAY (approve for payment) or REJECTED_BY_PM (reject)
    """
    if not request.org_membership:
        return HttpResponse(
            status=302,
            headers={"HX-Redirect": reverse("opportunity:detail", args=[org_slug, opp_id])},
        )

    invoice_id = request.POST.get("invoice_id")
    description = request.POST.get("description")
    new_status = request.POST.get("new_status")

    if new_status not in InvoiceStatus.values:
        return HttpResponseBadRequest(_("Invalid invoice status."))

    invoice = get_object_or_404(PaymentInvoice, opportunity=request.opportunity, payment_invoice_id=invoice_id)

    role = "program_manager" if request.is_opportunity_pm else "network_manager"
    valid, error = InvoiceWorkflow.validate_transition(invoice.status, new_status, role)
    if error:
        return HttpResponseBadRequest(error)

    if new_status == InvoiceStatus.PENDING_PM_REVIEW and request.POST.get("attestation") != "true":
        return HttpResponseBadRequest(_("You must certify the invoice before submitting."))

    invoice.status = new_status
    update_fields = ["status", "description"] if invoice.service_delivery else ["status"]
    if invoice.service_delivery:
        invoice.description = description

    if new_status == InvoiceStatus.PENDING_PM_REVIEW:
        with pghistory.context(
            username=request.user.username,
            user_email=request.user.email,
            attestation_certified=True,
        ):
            invoice.save(update_fields=update_fields)
    else:
        invoice.save(update_fields=update_fields)

    if invoice.service_delivery and new_status in [InvoiceStatus.CANCELLED_BY_NM, InvoiceStatus.REJECTED_BY_PM]:
        rollback_invoice_line_items(invoice)

    messages.success(request, InvoiceWorkflow.get_status_update_message(new_status, invoice.invoice_number))

    return HttpResponse(
        status=204,
        headers={"HX-Redirect": _invoice_list_redirect_url(request, org_slug, opp_id)},
    )


@opp_standard_access_required
@opportunity_required
@require_POST
def invoice_pay(request, org_slug, opp_id):
    if not request.is_opportunity_pm:
        return HttpResponse(
            status=302,
            headers={"HX-Redirect": reverse("opportunity:detail", args=[org_slug, opp_id])},
        )
    invoice_ids = request.POST.getlist("pk")
    invoices = PaymentInvoice.objects.filter(
        opportunity=request.opportunity, payment_invoice_id__in=invoice_ids, payment__isnull=True
    )

    paid_invoice_ids = []
    payments = []
    required_status = InvoiceStatus.READY_TO_PAY
    for inv in invoices:
        if inv.status != required_status:
            label = InvoiceStatus.get_label(required_status)
            return HttpResponseBadRequest(_("Only {} invoice can be approved.").format(label))
        paid_invoice_ids.append(inv.id)
        payments.append(
            Payment(
                amount=inv.amount,
                organization=request.opportunity.organization,
                amount_usd=inv.amount_usd,
                invoice=inv,
            )
        )
        inv.status = InvoiceStatus.PAID
        inv.save(update_fields=["status"])

    Payment.objects.bulk_create(payments)

    transaction.on_commit(partial(send_invoice_paid_mail.delay, request.opportunity.pk, paid_invoice_ids))
    if paid_invoice_ids:
        messages.success(request, _("Invoice(s) successfully marked as paid."))
    return HttpResponse(headers={"HX-Redirect": _invoice_list_redirect_url(request, org_slug, opp_id)})


def _invoice_list_redirect_url(request, org_slug, opp_id):
    """Back to the invoice list, on the month the action was started from."""
    return with_invoice_month(reverse("opportunity:invoice_list", args=(org_slug, opp_id)), request.GET.get("month"))


@opp_view_access_required
@require_POST
@opportunity_required
def exchange_rate_preview(request, org_slug, opp_id):
    rate_date = request.POST.get("date")
    usd_currency = request.POST.get("usd_currency", False) == "true"
    replace_amount = request.POST.get("should_replace_amount", False) == "true"  # condition when user toggles
    amount = None

    rate_date = datetime.datetime.strptime(rate_date, "%Y-%m-%d").date()
    try:
        amount = Decimal(request.POST.get("amount") or 0)
    except InvalidOperation:
        amount = Decimal(0)

    converted_amount = amount

    if not rate_date:
        exchange_info = "Please select a date for exchange rate."
        converted_amount_display = ""
    else:
        exchange_rate = ExchangeRate.latest_exchange_rate(request.opportunity.currency_code, rate_date)
        if exchange_rate:
            exchange_info = format_html(
                "Exchange Rate on {}: <b>{}</b>",
                rate_date.strftime("%d-%m-%Y"),
                exchange_rate.rate,
            )
            other_currency_amount = None
            currency = request.opportunity.currency_code

            if usd_currency:
                if replace_amount:
                    converted_amount = amount / exchange_rate.rate
                other_currency_amount = converted_amount * exchange_rate.rate
            else:
                if replace_amount:
                    converted_amount = amount * exchange_rate.rate
                other_currency_amount = converted_amount / exchange_rate.rate
                currency = "USD"

            converted_amount = round(converted_amount, 2)
            other_currency_amount = round(other_currency_amount, 2)

            converted_amount_display = format_html("Amount in {}: <b>{}</b>", currency, other_currency_amount)
        else:
            exchange_info = "Exchange rate not available for selected date."
            converted_amount_display = ""

    html = format_html(
        """
            <div id="exchange-rate-display" data-converted-amount="{converted_amount}">{exchange_info}</div>
            <div id="converted-amount">{converted_amount_display}</div>
        """,
        exchange_info=exchange_info,
        converted_amount_display=converted_amount_display,
        converted_amount=converted_amount,
    )
    return HttpResponse(html)


@require_POST
@opportunity_required
@opp_standard_access_required
def invoice_items(request, *args, **kwargs):
    body = json.loads(request.body)
    start_date_str = body.get("start_date", None)
    end_date_str = body.get("end_date", None)

    if not start_date_str or not end_date_str:
        return JsonResponse({"error": _("Start date and end date are required.")})

    start_date = datetime.datetime.strptime(start_date_str, "%Y-%m-%d").date()
    end_date = datetime.datetime.strptime(end_date_str, "%Y-%m-%d").date()

    line_items = get_billable_line_items(request.opportunity, start_date, end_date)
    # An empty window has nothing to sum, so seed with zero rather than sum()'s int 0.
    total = sum((item.total_pay for item in line_items), Money.zero())
    show_org = any(item.org_pay.local for item in line_items)

    html = render_to_string(
        "opportunity/partials/invoice_line_items.html",
        {"table": InvoiceLineItemsTable(request.opportunity.currency_code, line_items, show_org=show_org)},
        request=request,
    )

    return JsonResponse(
        {
            "line_items_table_html": html,
            "total_amount": total.local,
            "total_usd_amount": total.usd,
            "late_delta_units": total_late_delta_units(line_items),
        }
    )


@require_GET
@opp_standard_access_required
@opportunity_required
def download_invoice_line_items(request, org_slug, opp_id):
    start_date_str = request.GET.get("start_date", None)
    end_date_str = request.GET.get("end_date", None)
    invoice_id = request.GET.get("invoice_id", None)

    if not start_date_str or not end_date_str:
        return HttpResponseBadRequest("Start date and end date are required.")

    try:
        start_date = datetime.datetime.strptime(start_date_str, "%Y-%m-%d").date()
        end_date = datetime.datetime.strptime(end_date_str, "%Y-%m-%d").date()
    except ValueError:
        return HttpResponseBadRequest("Invalid date format. Use YYYY-MM-DD.")
    if invoice_id:
        invoice = get_object_or_404(PaymentInvoice, payment_invoice_id=invoice_id, opportunity=request.opportunity)
        deliveries = get_invoice_delivery_rows_for_export(invoice)
    else:
        deliveries = get_billable_delivery_rows_for_export(request.opportunity, start_date, end_date)

    show_org = any(delivery.org_pay.local for delivery in deliveries)
    table = InvoiceDeliveriesTable(request.opportunity.currency_code, deliveries, show_org=show_org)
    export_format = "csv"
    exporter = TableExport(export_format, table)
    filename = f"invoice_line_items_{start_date}_{end_date}.csv"

    return exporter.response(filename=filename)
