from django.contrib import admin
from django.db.models import Q

from commcare_connect.opportunity.forms import OpportunityAccessCreationForm
from commcare_connect.opportunity.models import (
    Assessment,
    CommCareApp,
    CompletedModule,
    CompletedWork,
    DeliverUnit,
    DeliverUnitFlagRules,
    DeliveryType,
    ExchangeRate,
    FormJsonValidationRules,
    HQApiKey,
    LearnModule,
    Opportunity,
    OpportunityAccess,
    OpportunityClaim,
    OpportunityClaimLimit,
    Payment,
    PaymentInvoice,
    PaymentUnit,
    UserInvite,
    UserVisit,
)
from commcare_connect.opportunity.tasks import create_learn_modules_and_deliver_units

# Register your models here.


admin.site.register(DeliveryType)
admin.site.register(DeliverUnitFlagRules)
admin.site.register(FormJsonValidationRules)
admin.site.register(HQApiKey)


@admin.register(Opportunity)
class OpportunityAdmin(admin.ModelAdmin):
    actions = ["refresh_learn_and_deliver_modules"]

    @admin.action(description="Refresh Learn and Deliver Modules")
    def refresh_learn_and_deliver_modules(self, request, queryset):
        for opp in queryset:
            create_learn_modules_and_deliver_units.delay(opp.id)


@admin.register(OpportunityAccess)
class OpportunityAccessAdmin(admin.ModelAdmin):
    form = OpportunityAccessCreationForm
    list_display = ["get_opp_name", "get_username"]
    actions = ["clear_user_progress"]
    search_fields = ["user__username"]

    @admin.display(description="Opportunity Name")
    def get_opp_name(self, obj):
        return obj.opportunity.name

    @admin.display(description="Username")
    def get_username(self, obj):
        return obj.user.username

    @admin.action(description="Clear User Progress")
    def clear_user_progress(self, request, queryset):
        for access in queryset:
            UserVisit.objects.filter(opportunity_access=access).delete()
            Payment.objects.filter(opportunity_access=access).delete()
            OpportunityClaim.objects.filter(opportunity_access=access).delete()
            CompletedModule.objects.filter(opportunity_access=access).delete()
            Assessment.objects.filter(opportunity_access=access).delete()
            CompletedWork.objects.filter(opportunity_access=access).delete()


@admin.register(LearnModule)
@admin.register(DeliverUnit)
class LearnModuleAndDeliverUnitAdmin(admin.ModelAdmin):
    list_display = ["name", "slug", "app"]
    search_fields = ["name"]


class OpportunityClaimLimitInline(admin.TabularInline):
    list_display = ["payment_unit", "max_visit"]
    model = OpportunityClaimLimit


@admin.register(OpportunityClaim)
class OpportunityClaimAdmin(admin.ModelAdmin):
    list_display = ["get_username", "get_opp_name", "opportunity_access"]
    inlines = [OpportunityClaimLimitInline]

    @admin.display(description="Opportunity Name")
    def get_opp_name(self, obj):
        return obj.opportunity_access.opportunity.name

    @admin.display(description="Username")
    def get_username(self, obj):
        return obj.opportunity_access.user.username


@admin.register(CompletedModule)
class CompletedModuleAdmin(admin.ModelAdmin):
    list_display = ["module", "user", "opportunity", "date"]


@admin.register(UserVisit)
class UserVisitAdmin(admin.ModelAdmin):
    list_display = ["deliver_unit", "user", "opportunity", "status"]
    search_fields = ["opportunity_access__user__username", "opportunity_access__opportunity__name"]


@admin.register(Assessment)
class AssessmentAdmin(admin.ModelAdmin):
    list_display = ["app", "user", "opportunity", "date", "passed"]


class UserVisitTabularInline(admin.TabularInline):
    """Readonly tabular inline to show related User Visit
    on completed work object admin page.
    """

    fields = ["deliver_unit", "status", "visit_date", "status_modified_date", "date_created"]
    readonly_fields = ["date_created"]
    model = UserVisit
    show_change_link = True
    extra = 0
    can_delete = False

    def has_change_permission(self, *args, **kwargs) -> bool:
        return False

    def has_add_permission(self, *args, **kwargs) -> bool:
        return False

    def has_delete_permission(self, *args, **kwargs) -> bool:
        return False


@admin.register(CompletedWork)
class CompletedWorkAdmin(admin.ModelAdmin):
    list_display = ["get_username", "get_opp_name", "opportunity_access", "payment_unit", "status"]
    search_fields = ["opportunity_access__user__username", "opportunity_access__opportunity__name"]
    inlines = [UserVisitTabularInline]
    readonly_fields = ["date_created"]

    @admin.display(description="Opportunity Name")
    def get_opp_name(self, obj):
        return obj.opportunity_access.opportunity.name

    @admin.display(description="Username")
    def get_username(self, obj):
        return obj.opportunity_access.user.username


@admin.register(Payment)
class PaymentAdmin(admin.ModelAdmin):
    list_display = ["amount", "date_paid", "opportunity_access", "payment_unit", "confirmed"]
    search_fields = ["opportunity_access__user__username", "opportunity_access__opportunity__name"]


@admin.register(PaymentInvoice)
class PaymentInvoiceAdmin(admin.ModelAdmin):
    list_display = ["invoice_number", "opportunity", "amount", "date"]
    search_fields = ["opportunity__name", "invoice_number"]


@admin.register(PaymentUnit)
class PaymentUnitAdmin(admin.ModelAdmin):
    list_display = ["name", "get_opp_name"]
    search_fields = ["name", "opportunity__name"]

    @admin.display(description="Opportunity Name")
    def get_opp_name(self, obj):
        return obj.opportunity.name


@admin.register(ExchangeRate)
class ExchangeRateAdmin(admin.ModelAdmin):
    list_display = ("currency_code", "rate", "rate_date", "fetched_at")
    list_filter = ("currency_code", "rate_date")
    search_fields = ("currency_code",)
    ordering = ("-rate_date",)


@admin.register(CommCareApp)
class CommCareAppAdmin(admin.ModelAdmin):
    list_display = ["name", "cc_app_id", "cc_domain", "get_opportunity_ids"]
    search_fields = ["cc_domain", "name", "cc_app_id"]

    @admin.display(description="Linked Opportunity IDs")
    def get_opportunity_ids(self, obj):
        opp_ids = Opportunity.objects.filter(Q(learn_app=obj) | Q(deliver_app=obj)).values_list("id", flat=True)
        return ", ".join(str(i) for i in opp_ids)


@admin.register(UserInvite)
class UserInviteAdmin(admin.ModelAdmin):
    list_display = ["phone_number", "opportunity"]
    search_fields = ["phone_number", "opportunity__name"]
