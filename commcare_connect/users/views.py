import datetime
import json

from allauth.account.models import transaction
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required, user_passes_test
from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib.messages.views import SuccessMessageMixin
from django.db.models import Q
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, render
from django.urls import reverse
from django.utils.decorators import method_decorator
from django.utils.html import format_html
from django.utils.timezone import now
from django.utils.translation import gettext_lazy as _
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET
from django.views.generic import RedirectView, UpdateView, View
from oauth2_provider.contrib.rest_framework import OAuth2Authentication
from oauth2_provider.views.mixins import ClientProtectedResourceMixin
from rest_framework.decorators import api_view, authentication_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from commcare_connect.connect_id_client.main import fetch_demo_user_tokens
from commcare_connect.connect_id_client.models import ConnectIdUser
from commcare_connect.opportunity.models import HQApiKey, Opportunity, OpportunityAccess, UserInvite, UserInviteStatus
from commcare_connect.opportunity.tasks import update_user_and_send_invite
from commcare_connect.organization.decorators import org_member_required
from commcare_connect.utils.commcarehq_api import get_applications_for_user_by_domain, get_domains_for_user

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
        return HttpResponse("opportunity required", status=400)
    opportunity = Opportunity.objects.get(pk=opportunity_id)
    app = opportunity.learn_app
    domain = app.cc_domain
    user_created = create_hq_user_and_link(request.user, domain, opportunity)
    if not user_created:
        return HttpResponse("Failed to create user", status=400)
    try:
        access_object = OpportunityAccess.objects.get(user=request.user, opportunity=opportunity)
    except OpportunityAccess.DoesNotExist:
        return HttpResponse("user has no access to opportunity", status=400)
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
    return HttpResponse(status=200)


@require_GET
def accept_invite(request, invite_id):
    try:
        o = OpportunityAccess.objects.get(invite_id=invite_id)
    except OpportunityAccess.DoesNotExist:
        return HttpResponse("This link is invalid. Please try again", status=404)
    with transaction.atomic():
        o.accepted = True
        o.save()
        user_invite = UserInvite.objects.get(opportunity_access=o)
        user_invite.status = UserInviteStatus.accepted
        user_invite.save()
    return HttpResponse(
        "Thank you for accepting the invitation. Open your CommCare Connect App to "
        "see more information about the opportunity and begin learning"
    )


@login_required
@user_passes_test(lambda user: user.is_superuser)
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
@org_member_required
def get_api_keys(request, org_slug=None):
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


# used for loading domain dropdown
@org_member_required
def get_domains(request, org_slug=None):
    hq_server = request.GET.get("hq_server")
    api_key_id = request.GET.get("api_key")
    if not hq_server or not api_key_id:
        return HttpResponse(format_html("<option value='{}'>{}</option>", None, "Select an API Key to load domains."))

    options = []
    api_key = HQApiKey.objects.get(id=api_key_id, hq_server=hq_server, user=request.user)
    domains = get_domains_for_user(api_key)
    options.append(format_html("<option value='{}'>{}</option>", None, "Select a Domain."))
    for domain in domains:
        options.append(format_html("<option value='{}'>{}</option>", domain, domain))
    return HttpResponse("\n".join(options))


# used for loading learn_app and deliver_app dropdowns
@org_member_required
def get_application(request, org_slug=None):
    hq_server = request.GET.get("hq_server")
    api_key_id = request.GET.get("api_key")
    domain = request.GET.get("learn_app_domain") or request.GET.get("deliver_app_domain")
    if not hq_server or not api_key_id or not domain:
        return HttpResponse(
            format_html("<option value='{}'>{}</option>", None, "Select a Domain to load applications.")
        )
    api_key = HQApiKey.objects.get(id=api_key_id, hq_server=hq_server, user=request.user)
    applications = get_applications_for_user_by_domain(api_key, domain)
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
    options.append(format_html("<option value='{}'>{}</option>", None, "Select an Application"))
    for app in applications:
        if app["id"] not in existing_apps:
            value = json.dumps(app)
            name = app["name"]
            options.append(format_html("<option value='{}'>{}</option>", value, name))
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
