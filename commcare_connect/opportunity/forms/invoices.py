"""Forms for creating invoices, exporting them and sending them to Finance."""

import datetime

from crispy_forms.helper import FormHelper
from crispy_forms.layout import HTML, Div, Field, Fieldset, Layout, Submit
from django import forms
from django.core.exceptions import ValidationError
from django.template.loader import render_to_string
from django.utils.html import format_html
from django.utils.timezone import localdate
from django.utils.translation import gettext
from django.utils.translation import gettext_lazy as _

from commcare_connect.opportunity.forms import CHECKBOX_CLASS
from commcare_connect.opportunity.models import ExchangeRate, InvoiceFinanceRequest, InvoiceStatus, PaymentInvoice
from commcare_connect.opportunity.tables import header_with_tooltip, value_with_icon_tooltip
from commcare_connect.opportunity.utils import finance_request as finance_request_utils
from commcare_connect.opportunity.utils.invoice import (
    generate_invoice_number,
    get_end_date_for_invoice,
    get_start_date_for_invoice,
)
from commcare_connect.opportunity.utils.invoice_export import get_exportable_invoices
from commcare_connect.opportunity.utils.invoice_line_items import bill_invoice, get_billable_line_items
from commcare_connect.utils.datetime import parse_year_month


class PaymentInvoiceInvoiceTicketLinkForm(forms.Form):
    invoice_ticket_link = forms.URLField(label=_("Invoice Ticket"), required=False)


class InvoiceFinanceRequestForm(forms.ModelForm):
    """The answers a Program Manager gives when sending an approved invoice to Finance."""

    contracted_entity = forms.ChoiceField(
        label=_("Contracted entity"),
        choices=[("", "---------"), *finance_request_utils.CONTRACTED_ENTITY_CHOICES],
        help_text=_("The Dimagi entity contracted to pay this invoice."),
    )
    gl_account = forms.ChoiceField(
        label=_("GL account (expense category)"),
        choices=[("", "---------"), *finance_request_utils.GL_ACCOUNT_CHOICES],
        help_text=_("Direct accounts are for project expenses; Overhead accounts for costs Dimagi bears."),
        widget=forms.Select(attrs={"data-tomselect": "1", "data-tomselect:no-remove-button": "1"}),
    )
    project_name = forms.CharField(
        label=_("Project name"),
        required=False,
        max_length=255,
        help_text=_("As it appears on the Salesforce contract. Required for Direct accounts."),
    )
    new_vendor = forms.TypedChoiceField(
        label=_("Is this the first payment to this organization?"),
        choices=[(True, _("Yes")), (False, _("No"))],
        coerce=lambda value: value in (True, "True"),
        widget=forms.RadioSelect,
    )

    class Meta:
        model = InvoiceFinanceRequest
        fields = ["contracted_entity", "gl_account", "project_name", "new_vendor"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.helper = FormHelper(self)
        self.helper.form_tag = False

    def clean(self):
        cleaned_data = super().clean()
        gl_account = cleaned_data.get("gl_account")
        if gl_account and finance_request_utils.requires_project_name(gl_account):
            if not cleaned_data.get("project_name", "").strip():
                self.add_error("project_name", _("A project name is required for Direct accounts."))
        else:
            cleaned_data["project_name"] = ""
        return cleaned_data


class InvoiceExportForm(forms.Form):
    """Which invoices a bulk export covers, and in what shape.

    Selected invoices win; with nothing selected the export covers the whole month, or every
    exportable invoice when the list is showing all months.
    """

    PDF_ZIP = "pdf_zip"
    CSV_SUMMARY = "csv_summary"

    export_type = forms.ChoiceField(
        choices=[(PDF_ZIP, _("Invoice PDFs (.zip)")), (CSV_SUMMARY, _("Amounts due (.csv)"))]
    )
    month = forms.CharField(required=False)
    invoice_ids = forms.ModelMultipleChoiceField(queryset=PaymentInvoice.objects.none(), required=False)

    def __init__(self, *args, **kwargs):
        self.opportunity = kwargs.pop("opportunity")
        super().__init__(*args, **kwargs)
        # Scoping the queryset to the opportunity is what stops another workspace's invoice ids
        # being exported through this form.
        self.fields["invoice_ids"].queryset = PaymentInvoice.objects.filter(opportunity=self.opportunity)

    def get_invoices(self):
        selected = self.cleaned_data["invoice_ids"]
        if selected:
            return selected
        return get_exportable_invoices(self.opportunity, parse_year_month(self.cleaned_data["month"]))


class AutomatedPaymentInvoiceForm(forms.ModelForm):
    """
    Form used for creating new invoices or to show details by passing read_only=True.
    Invoices are not allowed to be edited once created.
    """

    amount = forms.DecimalField(
        label=_("Amount"),
        decimal_places=2,
    )
    amount_usd = forms.DecimalField(
        label=_("Amount (USD)"),
        required=False,
        decimal_places=2,
    )
    invoice_number = forms.CharField(
        label=_("Invoice ID"),
        required=False,
        widget=forms.TextInput(attrs={"placeholder": _("Auto-generated on save")}),
        help_text=_("This value is system-generated and unique."),
    )
    usd_currency = forms.BooleanField(
        required=False,
        initial=False,
        label=_("Specify in USD"),
        widget=forms.CheckboxInput(),
    )
    date_of_expense = forms.DateField(
        label=_("Date of expense incurred"),
        widget=forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
        required=False,
    )
    # Derived for display only, so `disabled` rather than `readonly`: Django then ignores whatever
    # is posted, and a stale figure can never add a field error that blocks the invoice.
    late_delta_units = forms.IntegerField(
        required=False,
        disabled=True,
        widget=forms.NumberInput(),
    )
    description = forms.CharField(
        label="",
        widget=forms.Textarea(attrs={"rows": 3}),
        required=False,
    )

    class Meta:
        model = PaymentInvoice
        fields = (
            "title",
            "date",
            "invoice_number",
            "start_date",
            "end_date",
            "description",
            "amount",
            "amount_usd",
            "date_of_expense",
        )
        widgets = {
            "date": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
            "start_date": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
            "end_date": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
            "title": forms.TextInput(attrs={"placeholder": _("e.g. October Services")}),
        }
        labels = {
            "title": _("Invoice title"),
            "date": _("Generation date"),
        }

    def __init__(self, *args, **kwargs):
        self.opportunity = kwargs.pop("opportunity")
        self.invoice_type = kwargs.pop("invoice_type", PaymentInvoice.InvoiceType.service_delivery)
        self.read_only = kwargs.pop("read_only", False)
        self.line_items_table = kwargs.pop("line_items_table", None)
        self.late_delta_units = kwargs.pop("late_delta_units", 0)
        self.status = kwargs.pop("status", InvoiceStatus.PENDING_NM_REVIEW)
        self.is_opportunity_pm = kwargs.pop("is_opportunity_pm")

        super().__init__(*args, **kwargs)
        self.fields["end_date"].widget.attrs["max"] = localdate() - datetime.timedelta(days=1)

        self.prepare_fields()

        self.helper = FormHelper(self)
        self.helper.layout = self.get_form_layout()
        self.helper.form_tag = False

    def prepare_fields(self):
        if self.read_only:
            self.fields["status"] = forms.CharField(required=False, label=gettext("Invoice Status"))
            for field in self.fields.values():
                field.widget.attrs["readonly"] = "readonly"
        else:
            self.fields["date"].widget.attrs.update({"readonly": "readonly"})

        if not self.instance.pk:
            self.fields["invoice_number"].initial = generate_invoice_number()
            self.fields["date"].initial = str(datetime.date.today())
        else:
            self.status = self.instance.status
            self.fields["status"].initial = self.instance.get_status_display()

        if self.is_service_delivery:
            self.fields["amount"].label = gettext("Amount ({currency_code})").format(
                currency_code=self.opportunity.currency_code or "Local Currency"
            )
            self.fields["amount"].help_text = gettext("Local currency is determined by the opportunity.")

            self.fields["late_delta_units"].label = header_with_tooltip(
                format_html(
                    '{} <i class="fa-solid fa-circle-info text-gray-400"></i>', gettext("Additional Deliveries")
                ),
                gettext(
                    "Additional deliveries for previously billed work. These were delivered or approved "
                    "after the previous invoice was issued and are included on this invoice."
                ),
            )
            self.fields["late_delta_units"].initial = self.late_delta_units

            if (
                self.read_only
                and self.instance.pk
                and self.instance.exchange_rate_id
                and self.instance.amount_usd is not None
            ):
                self.fields["amount_usd"].label = value_with_icon_tooltip(
                    self.fields["amount_usd"].label, self._amount_usd_tooltip_html(), theme="dark"
                )

            self.fields["description"].widget.attrs.update(
                {
                    "placeholder": gettext("Describe service delivery details, references, or notes..."),
                }
            )

            if self.instance.pk:
                self.fields["start_date"].initial = str(self.instance.start_date)
                self.fields["end_date"].initial = str(self.instance.end_date)
            else:
                start_date = get_start_date_for_invoice(self.opportunity)
                self.fields["start_date"].initial = str(start_date)
                self.fields["end_date"].initial = str(get_end_date_for_invoice(start_date))

            if self.read_only and self.status == InvoiceStatus.PENDING_NM_REVIEW and not self.is_opportunity_pm:
                self.fields["description"].widget.attrs.pop("readonly", None)
        else:
            self.fields["usd_currency"].widget.attrs.update(
                {
                    "x-ref": "currencyToggle",
                    "x-on:change": "currency = $event.target.checked; convert(true)",
                }
            )
            self.fields["description"].required = True
            self.fields["description"].label = gettext("Justification")
            self.fields["description"].widget.attrs.update(
                {
                    "placeholder": gettext("Provide a justification for this expense..."),
                }
            )
            self.fields["date_of_expense"].required = True

    def _amount_usd_tooltip_html(self):
        return render_to_string("opportunity/partials/amount_usd_tooltip.html")

    def get_form_layout(self):
        if self.is_service_delivery:
            invoice_form_fields = self.service_delivery_invoice_fields
        else:
            invoice_form_fields = self.custom_invoice_fields

        if not self.read_only:
            invoice_form_fields.append(
                Div(
                    Submit(
                        "submit",
                        gettext("Submit"),
                        css_class="button button-md primary-dark",
                        # Disable when there is an error while fetching the line items
                        **{":disabled": "isServiceDelivery && lineItemsError"},
                    ),
                    css_class="flex justify-end mt-4",
                )
            )
        else:
            invoice_form_fields.insert(0, Field("status"))

        return Layout(*invoice_form_fields)

    @property
    def service_delivery_invoice_fields(self):
        first_row = [
            Field("invoice_number", **{"readonly": "readonly"}),
            Field("title"),
        ]

        start_date_attrs = {} if self.read_only else {"x-model": "startDate", "x-on:change": "fetchInvoiceLineItems()"}
        end_date_attrs = {} if self.read_only else {"x-model": "endDate", "x-on:change": "fetchInvoiceLineItems()"}
        second_row = [
            Field("date", **{"x-ref": "date"}),
            Field("start_date", **start_date_attrs),
            Field("end_date", **end_date_attrs),
        ]

        third_row = [
            Field(
                "amount",
                **{
                    "x-ref": "amount",
                    "x-model": "amount",
                    "readonly": "readonly",
                },
            ),
            Field(
                "amount_usd",
                **{
                    "x-model": "usdAmount",
                    "readonly": "readonly",
                },
            ),
        ]

        if not self.read_only:
            third_row.append(
                Div(
                    Field("late_delta_units", **{"x-model": "lateDeltaUnits"}),
                    **{"x-show": "lateDeltaUnits > 0", "x-cloak": ""},
                )
            )
        elif self.late_delta_units:
            third_row.append(Field("late_delta_units"))

        return [
            Div(
                Div(*first_row, css_class="grid grid-cols-3 gap-6"),
                Div(*second_row, css_class="grid grid-cols-3 gap-6"),
                Div(*third_row, css_class="grid grid-cols-3 gap-6"),
                css_class="flex flex-col gap-4",
            ),
            self.line_items,
            Fieldset(
                gettext("Service Delivery Notes"),
                Field("description", **{"x-ref": "description"}),
            ),
        ]

    @property
    def custom_invoice_fields(self):
        first_row = [
            Field("invoice_number", **{"readonly": "readonly"}),
            Field("date", **{"x-ref": "date"}),
            Field("date_of_expense"),
        ]

        second_row = [
            Field(
                "amount",
                label=gettext("Amount"),
                **{
                    "x-ref": "amount",
                    "x-model": "amount",
                    "x-on:input.debounce.300ms": "convert()",
                },
            ),
            Field(
                "usd_currency",
                css_class=CHECKBOX_CLASS,
                wrapper_class="flex p-4 justify-between rounded-lg bg-gray-100",
            ),
            Div(css_id="converted-amount-wrapper", css_class="space-y-1 text-sm text-gray-500 mb-4"),
        ]

        third_row = [
            Field("description"),
        ]

        return [
            Div(
                Div(*first_row, css_class="grid grid-cols-3 gap-6"),
                Div(*second_row, css_class="grid grid-cols-3 gap-6"),
                Div(*third_row),
                css_class="flex flex-col gap-4",
            ),
        ]

    def clean_invoice_number(self):
        invoice_number = self.cleaned_data["invoice_number"]

        if not invoice_number:
            invoice_number = generate_invoice_number()

        if PaymentInvoice.objects.filter(invoice_number=invoice_number).exists():
            raise ValidationError(
                "Please use a different invoice number",
                code="invoice_number_reused",
            )
        return invoice_number

    def clean_date_of_expense(self):
        date_of_expense = self.cleaned_data.get("date_of_expense")
        if self.is_service_delivery:
            return date_of_expense

        if not date_of_expense:
            raise ValidationError("Date of expense is required for custom invoices.")

        if date_of_expense > datetime.date.today():
            raise ValidationError("Date of expense cannot be in the future.")

        return date_of_expense

    def clean(self):
        cleaned_data = super().clean()
        amount = cleaned_data.get("amount")
        date = cleaned_data.get("date")

        if amount is None or date is None:
            return cleaned_data  # Let individual field errors handle missing values

        if not self.is_service_delivery:
            exchange_rate = ExchangeRate.latest_exchange_rate(self.opportunity.currency_code, date)
            if not exchange_rate:
                raise ValidationError("Exchange rate not available for selected date.")

            cleaned_data["exchange_rate"] = exchange_rate
            cleaned_data["title"] = None
            cleaned_data["start_date"] = None
            cleaned_data["end_date"] = None

            if cleaned_data.get("usd_currency"):
                cleaned_data["amount_usd"] = amount
                cleaned_data["amount"] = round(amount * exchange_rate.rate, 2)
            else:
                cleaned_data["amount"] = amount
                cleaned_data["amount_usd"] = round(amount / exchange_rate.rate, 2)
        else:
            start_date = cleaned_data.get("start_date")
            end_date = cleaned_data.get("end_date")

            if not start_date:
                raise ValidationError({"start_date": "Start date is required for service delivery invoices."})
            if not end_date:
                raise ValidationError({"end_date": "End date is required for service delivery invoices."})

            if end_date < start_date:
                raise ValidationError({"end_date": "End date cannot be earlier than start date."})

            if end_date >= localdate():
                raise ValidationError({"end_date": _("End date must be before today.")})

            self._reject_stale_total(amount, start_date, end_date)

        return cleaned_data

    def _reject_stale_total(self, amount, start_date, end_date):
        """Reject a submit if the posted total no longer matches the current billable total.

        This provides a user-facing error when the preview has gone stale. `save()` still
        recomputes the total under a lock and never trusts the posted amount.
        """

        # Use the same calculation as the preview, so a mismatch reflects a real state change.
        billable_total = sum(
            item.total_pay.local for item in get_billable_line_items(self.opportunity, start_date, end_date)
        )
        if amount != billable_total:
            raise ValidationError(
                _(
                    "The billable total for this period has changed since this page was loaded. "
                    "Please refresh the page to see the latest line items and total, then submit again."
                )
            )

    def save(self, commit=True):
        instance = super().save(commit=False)
        instance.opportunity = self.opportunity
        if not self.is_service_delivery:
            instance.amount = self.cleaned_data["amount"]
            instance.amount_usd = self.cleaned_data["amount_usd"]
        instance.exchange_rate = self.cleaned_data.get("exchange_rate")
        instance.service_delivery = self.is_service_delivery
        instance.date_of_expense = self.cleaned_data.get("date_of_expense")
        instance.status = self.status

        if not commit:
            return instance

        if self.is_service_delivery:
            # Save the invoice totals from the rows just frozen (or 0 if nothing was billable).
            # Preview totals are only for display and may be stale; persisted totals must come
            # from the same read that created the invoice line items so they always match.
            rows = bill_invoice(instance, start_date=instance.start_date, end_date=instance.end_date)
            if not rows:
                instance.amount = 0
                instance.amount_usd = 0
                instance.save()
        else:
            instance.save()

        return instance

    @property
    def is_service_delivery(self):
        return self.invoice_type == PaymentInvoice.InvoiceType.service_delivery

    @property
    def line_items(self):
        return Fieldset(
            "Line Items",
            HTML('{% include "opportunity/partials/invoice_line_items_fieldset.html" %}'),
        )

    @property
    def line_items_released(self):
        """True when line items were released due to cancellation or rejection."""
        return self.instance.pk is not None and self.instance.status in (
            InvoiceStatus.CANCELLED_BY_NM,
            InvoiceStatus.REJECTED_BY_PM,
        )
