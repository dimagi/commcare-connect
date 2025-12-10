from allauth.account.models import transaction
from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required, permission_required
from django.contrib.auth.mixins import LoginRequiredMixin, PermissionRequiredMixin
from django.contrib.messages.views import SuccessMessageMixin
from django.http import HttpResponse, JsonResponse
from django.middleware.csrf import get_token
from django.shortcuts import get_object_or_404, render
from django.urls import reverse
from django.utils.decorators import method_decorator
from django.utils.html import format_html
from django.utils.timezone import now
from django.utils.translation import gettext_lazy as _
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET
from django.views.generic import FormView, RedirectView, UpdateView, View
from oauth2_provider.contrib.rest_framework import OAuth2Authentication
from oauth2_provider.views.mixins import ClientProtectedResourceMixin
from rest_framework.decorators import api_view, authentication_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from commcare_connect.connect_id_client.main import fetch_demo_user_tokens, get_user_otp
from commcare_connect.connect_id_client.models import ConnectIdUser
from commcare_connect.opportunity.models import HQApiKey, Opportunity, OpportunityAccess, UserInvite, UserInviteStatus
from commcare_connect.opportunity.tasks import update_user_and_send_invite
from commcare_connect.users.forms import ManualUserOTPForm
from commcare_connect.utils.error_codes import ErrorCodes
from commcare_connect.utils.permission_const import DEMO_USER_ACCESS, OTP_ACCESS

from .helpers import create_hq_user_and_link
from .models import ConnectIDUserLink

User = get_user_model()


class UserUpdateView(LoginRequiredMixin, SuccessMessageMixin, UpdateView):
    model = User
    fields = ["name"]
    success_message = _("Information successfully updated")
    template_name = "users/user_form.html"

    def get_success_url(self):
        assert self.request.user.is_authenticated  # for mypy to know that the user is authenticated
        return reverse("account_email")

    def get_object(self):
        return self.request.user


user_update_view = UserUpdateView.as_view()


class UserRedirectView(LoginRequiredMixin, RedirectView):
    permanent = False

    def get_redirect_url(self):
        if not self.request.user.memberships.exists():
            return reverse("home")
        organization = self.request.org
        if organization:
            return reverse("opportunity:list", kwargs={"org_slug": organization.slug})
        return reverse("account_email")


user_redirect_view = UserRedirectView.as_view()


@method_decorator(csrf_exempt, name="dispatch")
class CreateUserLinkView(ClientProtectedResourceMixin, View):
    def post(self, request):
        commcare_username = request.POST.get("commcare_username")
        connect_username = request.POST.get("connect_username")
        if not commcare_username or not connect_username:
            return HttpResponse("commcare_username and connect_username required", status=400)
        try:
            user = User.objects.get(username=connect_username)
        except User.DoesNotExist:
            return HttpResponse("connect user does not exist", status=400)
        user_link, new = ConnectIDUserLink.objects.get_or_create(commcare_username=commcare_username, user=user)
        if new:
            return HttpResponse(status=201)
        else:
            return HttpResponse(status=200)


create_user_link_view = CreateUserLinkView.as_view()


@csrf_exempt
@api_view(["POST"])
@authentication_classes([OAuth2Authentication])
def start_learn_app(request):
    opportunity_id = request.POST.get("opportunity")
    if opportunity_id is None:
        return Response({"error_code": ErrorCodes.OPPORTUNITY_REQUIRED}, status=400)
    opportunity = Opportunity.objects.get(pk=opportunity_id)
    app = opportunity.learn_app
    domain = app.cc_domain
    user_created = create_hq_user_and_link(request.user, domain, opportunity)
    if not user_created:
        return Response({"error_code": ErrorCodes.FAILED_USER_CREATE}, status=400)
    try:
        access_object = OpportunityAccess.objects.get(user=request.user, opportunity=opportunity)
    except OpportunityAccess.DoesNotExist:
        return Response({"error_code": ErrorCodes.NO_OPPORTUNITY_ACCESS}, status=400)
    with transaction.atomic():
        if access_object.date_learn_started is None:
            access_object.date_learn_started = now()

            if not access_object.last_active or access_object.last_active < access_object.date_learn_started:
                access_object.last_active = access_object.date_learn_started

        access_object.accepted = True
        access_object.save()
        user_invite = UserInvite.objects.get(opportunity_access=access_object)
        user_invite.status = UserInviteStatus.accepted
        user_invite.save()
    return Response()


class AcceptInviteView(View):
    def get(self, request, invite_id):
        try:
            access = OpportunityAccess.objects.get(invite_id=invite_id)
        except OpportunityAccess.DoesNotExist:
            return HttpResponse("This link is invalid. Please try again", status=404)
        get_token(request)
        return render(
            request,
            "users/accept_invite_confirm.html",
            context={
                "opportunity_name": access.opportunity.name,
            },
        )

    def post(self, request, invite_id):
        try:
            o = OpportunityAccess.objects.get(invite_id=invite_id)
        except OpportunityAccess.DoesNotExist:
            return HttpResponse("This link is invalid. Please try again", status=404)

        if o.accepted:
            return HttpResponse(
                _(
                    "This invitation has already been accepted. Open your Connect App to "
                    "see more information about the opportunity and begin learning"
                )
            )

        o.accepted = True
        o.save()
        user_invite = UserInvite.objects.get(opportunity_access=o)
        user_invite.status = UserInviteStatus.accepted
        user_invite.save()
        return HttpResponse(
            _(
                "Thank you for accepting the invitation. Open your Connect App to "
                "see more information about the opportunity and begin learning"
            )
        )


@login_required
@permission_required(DEMO_USER_ACCESS)
@require_GET
def demo_user_tokens(request):
    users = fetch_demo_user_tokens()
    return render(request, "users/demo_tokens.html", {"demo_users": users})


class SMSStatusCallbackView(APIView):
    permission_classes = [AllowAny]

    def post(self, *args, **kwargs):
        message_sid = self.request.data.get("MessageSid", None)
        message_status = self.request.data.get("MessageStatus", None)
        user_invite = get_object_or_404(UserInvite, message_sid=message_sid)
        if not user_invite.status == UserInviteStatus.accepted:
            if message_status == "delivered":
                user_invite.status = UserInviteStatus.sms_delivered
                user_invite.notification_date = now()
            if message_status == "undelivered":
                user_invite.status = UserInviteStatus.sms_not_delivered
            user_invite.save()
        return Response(status=200)


# used for loading api key dropdown
@require_GET
@login_required
def get_api_keys(request):
    hq_server = request.GET.get("hq_server")
    if not hq_server:
        return HttpResponse(
            format_html("<option value='{}'>{}</option>", None, "Select a HQ Server to load API Keys.")
        )

    api_keys = HQApiKey.objects.filter(hq_server=hq_server, user=request.user).order_by("-date_created")
    if not api_keys:
        return HttpResponse(headers={"HX-Trigger": "no-api-keys-found"})

    options = []
    options.append(format_html("<option value='{}'>{}</option>", None, "Select an API key"))
    for api_key in api_keys:
        api_key_hidden = f"{api_key.api_key[:4]}...{api_key.api_key[-4:]}"
        options.append(
            format_html(
                "<option value='{}'>{}</option>",
                api_key.id,
                api_key_hidden,
            )
        )
    return HttpResponse("\n".join(options))


@method_decorator(csrf_exempt, name="dispatch")
class CheckInvitedUserView(ClientProtectedResourceMixin, View):
    def get(self, request, *args, **kwargs):
        phone_number = request.GET.get("phone_number")
        invited = False
        if phone_number:
            invited = UserInvite.objects.filter(phone_number=phone_number).exists()
        return JsonResponse({"invited": invited})


@method_decorator(csrf_exempt, name="dispatch")
class ResendInvitesView(ClientProtectedResourceMixin, View):
    def post(self, request, *args, **kwargs):
        username = request.POST.get("username")
        name = request.POST.get("name")
        phone_number = request.POST.get("phone_number")
        user = ConnectIdUser(username=username, name=name, phone_number=phone_number)
        opps = UserInvite.objects.filter(
            phone_number=user.phone_number, status=UserInviteStatus.not_found
        ).values_list("opportunity_id", flat=True)
        for opp_id in opps:
            update_user_and_send_invite(user, opp_id)
        return HttpResponse(status=200)


class RetrieveUserOTPView(LoginRequiredMixin, PermissionRequiredMixin, FormView):
    template_name = "pages/connect_user_otp.html"
    form_class = ManualUserOTPForm
    permission_required = OTP_ACCESS

    @property
    def success_url(self):
        return reverse("users:connect_user_otp")

    def form_valid(self, form):
        otp = get_user_otp(form.cleaned_data["phone_number"])
        if otp is None:
            messages.error(
                self.request,
                "Failed to fetch OTP. Please make sure the number is correct and "
                "that the user has started their device seating process.",
            )
        else:
            messages.success(self.request, f"The user's OTP is: {otp}")

        return super().form_valid(form)

    def form_invalid(self, form):
        errors = ", ".join(form.errors["phone_number"])
        messages.error(self.request, f"{errors}")
        return super().form_invalid(form)
