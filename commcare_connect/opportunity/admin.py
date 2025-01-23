from django.contrib import admin

from commcare_connect.opportunity.forms import OpportunityAccessCreationForm
from commcare_connect.opportunity.models import (
    Assessment,
    CommCareApp,
    CompletedModule,
    CompletedWork,
    DeliverUnit,
    DeliverUnitFlagRules,
    DeliveryType,
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


admin.site.register(CommCareApp)
admin.site.register(UserInvite)
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


@admin.register(CompletedWork)
class CompletedWorkAdmin(admin.ModelAdmin):
    list_display = ["get_username", "get_opp_name", "opportunity_access", "payment_unit", "status"]
    search_fields = ["opportunity_access__user__username", "opportunity_access__opportunity__name"]

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
