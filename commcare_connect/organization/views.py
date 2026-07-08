from urllib.parse import urlencode

from allauth.account import app_settings as allauth_account_settings
from allauth.account.adapter import get_adapter
from allauth.account.utils import complete_signup, setup_user_email
from django.contrib import messages
from django.contrib.auth.decorators import login_required, permission_required
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.translation import gettext
from django.views.decorators.http import require_GET
from django_tables2 import RequestConfig
from rest_framework.decorators import api_view

from commcare_connect.organization.decorators import org_admin_required
from commcare_connect.organization.forms import (
    InviteAcceptForm,
    OrganizationChangeForm,
    OrganizationInviteForm,
    OrganizationSelectOrCreateForm,
)
from commcare_connect.organization.models import Organization, OrganizationInvite, UserOrganizationMembership
from commcare_connect.organization.tables import OrgMemberTable, PendingInviteTable
from commcare_connect.organization.tasks import send_org_invite
from commcare_connect.users.models import User
from commcare_connect.utils.permission_const import WORKSPACE_ENTITY_MANAGEMENT_ACCESS
from commcare_connect.utils.tables import get_validated_page_size


@login_required
@permission_required(WORKSPACE_ENTITY_MANAGEMENT_ACCESS, raise_exception=True)
def organization_create(request):
    form = OrganizationSelectOrCreateForm(data=request.POST or None)

    if form.is_valid():
        org, is_new_org = form.save()
        if is_new_org:
            org.members.add(request.user, through_defaults={"role": UserOrganizationMembership.Role.ADMIN})
        return redirect("opportunity:list", org.slug)

    return render(request, "organization/organization_create.html", context={"form": form})


@org_admin_required
def organization_home(request, org_slug):
    org = get_object_or_404(Organization, slug=org_slug)

    form = None
    invite_form = OrganizationInviteForm(organization=org)
    if request.method == "POST":
        form = OrganizationChangeForm(request.POST, instance=org, user=request.user)
        if form.is_valid():
            messages.success(request, gettext("Workspace details saved!"))
            form.save()
            return redirect("organization:home", org_slug)

    if not form:
        form = OrganizationChangeForm(instance=org, user=request.user)

    return render(
        request,
        "organization/organization_home.html",
        {
            "organization": org,
            "form": form,
            "invite_form": invite_form,
        },
    )


@api_view(["POST"])
@org_admin_required
def add_members_form(request, org_slug):
    org = get_object_or_404(Organization, slug=org_slug)
    form = OrganizationInviteForm(request.POST or None, organization=org)

    if form.is_valid():
        form.instance.organization = org
        form.instance.invited_by = request.user
        form.instance.created_by = request.user.email
        form.instance.modified_by = request.user.email
        form.save()
        send_org_invite(invite_id=form.instance.pk)
        messages.success(request, gettext("Invite sent to {email}.").format(email=form.cleaned_data["email"]))
    else:
        error = next(iter(form.errors.values()))[0] if form.errors else gettext("Unable to send invite.")
        messages.error(request, error)
    url = reverse("organization:home", args=(org_slug,)) + "?active_tab=members"
    return redirect(url)


@api_view(["POST"])
@org_admin_required
def remove_members(request, org_slug):
    membership_ids = request.POST.getlist("membership_ids")
    base_url = reverse("organization:home", args=(org_slug,))
    query_params = urlencode({"active_tab": "members"})
    redirect_url = f"{base_url}?{query_params}"

    if str(request.org_membership.id) in membership_ids:
        messages.error(request, message=gettext("You cannot remove yourself from the workspace."))
        return redirect(redirect_url)

    if membership_ids:
        UserOrganizationMembership.objects.filter(pk__in=membership_ids, organization__slug=org_slug).delete()
        messages.success(request, message=gettext("Selected members have been removed from the workspace."))

    return redirect(redirect_url)


def accept_invite(request, org_slug, token):
    invite = get_object_or_404(OrganizationInvite, token=token, organization__slug=org_slug)

    if invite.status != OrganizationInvite.Status.INVITED or invite.is_expired:
        if invite.status == OrganizationInvite.Status.INVITED and invite.is_expired:
            invite.status = OrganizationInvite.Status.EXPIRED
            invite.save(update_fields=["status", "date_modified"])
        if invite.status == OrganizationInvite.Status.REVOKED:
            messages.error(
                request, gettext("This invitation has been revoked. Contact an admin if you believe this is an error.")
            )
        elif invite.status == OrganizationInvite.Status.ACCEPTED:
            messages.error(
                request, gettext("This invitation has already been accepted. Log in to access the workspace.")
            )
        else:
            messages.error(request, gettext("This invitation has expired. Ask an admin to send you a new one."))
        return redirect("account_login")

    if request.user.is_authenticated:
        if request.user.email and request.user.email.lower() == invite.email.lower():
            invite.accept(request.user)
            messages.success(request, gettext("You've joined {org}.").format(org=invite.organization.name))
            return redirect("opportunity:list", org_slug)
        messages.error(
            request,
            gettext("This invitation was sent to {email}. Log in with that email to accept it.").format(
                email=invite.email
            ),
        )
        return redirect("organization:home", org_slug)

    if User.objects.filter(email__iexact=invite.email).exists():
        messages.info(
            request,
            gettext("You've been invited to join {org}. Log in to accept.").format(org=invite.organization.name),
        )
        login_url = reverse("account_login") + "?" + urlencode({"next": request.path})
        return redirect(login_url)

    adapter = get_adapter(request)
    new_user = User(email=invite.email)
    adapter.populate_username(request, new_user)
    form = InviteAcceptForm(user=new_user, data=request.POST or None)

    if request.method == "POST" and form.is_valid():
        adapter.stash_verified_email(request, invite.email)
        form.save()
        setup_user_email(request, new_user, [])
        invite.accept(new_user)
        return complete_signup(
            request,
            new_user,
            allauth_account_settings.EMAIL_VERIFICATION,
            reverse("opportunity:list", args=(org_slug,)),
        )

    return render(request, "organization/accept_invite.html", {"form": form, "invite": invite})


@api_view(["POST"])
@org_admin_required
def revoke_invite(request, org_slug, invite_id):
    invite = get_object_or_404(
        OrganizationInvite, pk=invite_id, organization__slug=org_slug, status=OrganizationInvite.Status.INVITED
    )
    invite.status = OrganizationInvite.Status.REVOKED
    invite.modified_by = request.user.email
    invite.save(update_fields=["status", "modified_by", "date_modified"])
    return _render_pending_invites(request)


@require_GET
@org_admin_required
def org_member_table(request, org_slug=None):
    members = UserOrganizationMembership.objects.filter(organization=request.org)
    table = OrgMemberTable(members)
    RequestConfig(request, paginate={"per_page": get_validated_page_size(request)}).configure(table)
    return render(request, "components/tables/table.html", {"table": table})


@require_GET
@org_admin_required
def org_pending_invites_table(request, org_slug=None):
    return _render_pending_invites(request)


def _render_pending_invites(request):
    invites = [
        invite
        for invite in OrganizationInvite.objects.filter(
            organization=request.org, status=OrganizationInvite.Status.INVITED
        ).order_by("-date_created")
        if not invite.is_expired
    ]
    table = PendingInviteTable(invites)
    RequestConfig(request, paginate={"per_page": get_validated_page_size(request)}).configure(table)
    return render(request, "organization/pending_invites_table.html", {"table": table})
