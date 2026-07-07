from urllib.parse import urlencode

from allauth.account.adapter import get_adapter
from allauth.account.models import EmailAddress
from django.contrib import messages
from django.contrib.auth import login as auth_login
from django.contrib.auth.decorators import login_required, permission_required
from django.db import DataError, IntegrityError, transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.translation import gettext
from django.views.decorators.http import require_GET
from django_tables2 import RequestConfig
from rest_framework.decorators import api_view

from commcare_connect.organization.decorators import org_admin_required
from commcare_connect.organization.forms import (
    InviteSignupForm,
    OrganizationChangeForm,
    OrganizationInviteForm,
    OrganizationSelectOrCreateForm,
)
from commcare_connect.organization.models import Organization, OrganizationInvite, UserOrganizationMembership
from commcare_connect.organization.tables import OrgMemberTable
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
    membership_form = OrganizationInviteForm(organization=org)
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
            "membership_form": membership_form,
        },
    )


@api_view(["POST"])
@org_admin_required
def add_members_form(request, org_slug):
    org = get_object_or_404(Organization, slug=org_slug)
    form = OrganizationInviteForm(request.POST or None, organization=org)

    if form.is_valid():
        invite = form.save(commit=False)
        invite.organization = org
        invite.invited_by = request.user
        invite.created_by = request.user.email or ""
        invite.modified_by = request.user.email or ""
        # An expired-but-still-"invited" row for this email would block re-inviting via the
        # unique_pending_org_invite constraint; flip it to expired so a fresh invite can be sent.
        OrganizationInvite.objects.filter(
            organization=org,
            email=invite.email,
            status=OrganizationInvite.Status.invited,
            date_created__lt=OrganizationInvite.expiry_cutoff(),
        ).update(status=OrganizationInvite.Status.expired)
        try:
            # transaction.atomic so a unique-constraint race rolls back only this
            # savepoint (ATOMIC_REQUESTS wraps the whole request) and we can recover.
            with transaction.atomic():
                invite.save()
        except IntegrityError:
            messages.error(request, gettext("This email already has a pending invite."))
        else:
            # Send only after the request transaction commits, so a later rollback
            # doesn't email an invite link for a row that never persisted.
            transaction.on_commit(lambda: send_org_invite(invite_id=invite.pk, host_user_id=request.user.pk))
            messages.success(request, gettext("Invitation sent to {email}.").format(email=invite.email))
    else:
        for errors in form.errors.values():
            for error in errors:
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


def accept_invite(request, org_slug, invite_id):
    """Landing page for an invite link. Onboards the invitee in one hop.

    - already logged in           -> accept immediately
    - email has an account        -> send to login, then this link finishes it
    - brand-new person            -> set a password, create a verified account,
                                     log in, and join, all on this page
    """
    invite = get_object_or_404(OrganizationInvite, token=invite_id, organization__slug=org_slug)

    if invite.status == OrganizationInvite.Status.accepted:
        messages.info(request, gettext("This invitation has already been accepted."))
        return redirect("home")

    if invite.status == OrganizationInvite.Status.expired or invite.is_expired:
        if invite.status == OrganizationInvite.Status.invited:  # past its window but not yet flagged
            invite.status = OrganizationInvite.Status.expired
            invite.save(update_fields=["status", "date_modified"])
        messages.error(request, gettext("This invitation has expired. Ask an admin to send you a new one."))
        return redirect("home")

    if request.user.is_authenticated:
        return _accept_invite_and_redirect(request, invite, request.user)

    if User.objects.filter(email__iexact=invite.email).exists():
        # They already have an account; sign in, and the same link (via ?next) finishes acceptance.
        return redirect(_login_url_for(request))

    # Brand-new person: create the account and join in one step. Clicking a link sent to
    # this address proves ownership, so we skip the separate email-confirmation step.
    form = InviteSignupForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            # savepoint so a username collision / oversized email / concurrent submit rolls
            # back cleanly instead of surfacing as a 500 under ATOMIC_REQUESTS.
            with transaction.atomic():
                user = _create_invited_user(invite, form.cleaned_data["password1"])
        except (IntegrityError, DataError):
            messages.error(
                request,
                gettext(
                    "We couldn't create an account for {email}. If you already have one, sign in to accept."
                ).format(email=invite.email),
            )
            return redirect(_login_url_for(request))
        auth_login(request, user, backend="allauth.account.auth_backends.AuthenticationBackend")
        return _accept_invite_and_redirect(request, invite, user)

    return render(request, "organization/accept_invite.html", {"invite": invite, "form": form})


def _login_url_for(request):
    return f"{reverse('account_login')}?{urlencode({'next': request.get_full_path()})}"


def _create_invited_user(invite, password):
    # Don't copy the (up to 254-char) email into the 150-char username; let allauth
    # generate a bounded, unique username the same way normal signup does.
    username = get_adapter().generate_unique_username([invite.email])
    user = User.objects.create_user(username=username, email=invite.email, password=password)
    EmailAddress.objects.update_or_create(user=user, email=invite.email, defaults={"verified": True, "primary": True})
    return user


def _accept_invite_and_redirect(request, invite, user):
    if not user.email or user.email.lower() != invite.email.lower():
        messages.error(
            request,
            gettext("This invitation was sent to {email}. Sign in with that email to accept it.").format(
                email=invite.email
            ),
        )
        return redirect("home")

    UserOrganizationMembership.objects.get_or_create(
        organization=invite.organization,
        user=user,
        defaults={"role": invite.role},
    )
    invite.status = OrganizationInvite.Status.accepted
    invite.modified_by = user.email or ""
    invite.save()
    messages.success(request, gettext("You have joined the {org} workspace.").format(org=invite.organization.name))
    # opportunity:list is org_viewer_required, so members/viewers/admins can all reach it.
    return redirect("opportunity:list", invite.organization.slug)


@api_view(["POST"])
@org_admin_required
def revoke_invite(request, org_slug):
    invite_id = request.POST.get("invite_id")
    deleted = 0
    if invite_id and str(invite_id).isdigit():
        try:
            deleted, _ = OrganizationInvite.objects.filter(
                pk=invite_id, organization=request.org, status=OrganizationInvite.Status.invited
            ).delete()
        except DataError:  # e.g. an integer beyond the PK column range
            deleted = 0
    if deleted:
        messages.success(request, gettext("Invitation revoked."))
    else:
        messages.error(request, gettext("That invitation could not be found."))
    base_url = reverse("organization:home", args=(org_slug,))
    return redirect(f"{base_url}?{urlencode({'active_tab': 'members'})}")


@require_GET
@org_admin_required
def org_member_table(request, org_slug=None):
    members = UserOrganizationMembership.objects.filter(organization=request.org)
    table = OrgMemberTable(members)
    RequestConfig(request, paginate={"per_page": get_validated_page_size(request)}).configure(table)
    pending_invites = OrganizationInvite.objects.filter(
        organization=request.org,
        status=OrganizationInvite.Status.invited,
        date_created__gte=OrganizationInvite.expiry_cutoff(),
    ).order_by("email")
    return render(
        request,
        "components/organization_home_page/members_table.html",
        {"table": table, "pending_invites": pending_invites},
    )
