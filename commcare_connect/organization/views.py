from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.text import slugify
from django.utils.translation import gettext
from django.views.decorators.http import require_GET, require_POST
from django_tables2 import RequestConfig
from rest_framework.decorators import api_view

from commcare_connect import connect_id_client
from commcare_connect.connect_id_client.models import Credential
from commcare_connect.organization.decorators import org_admin_required
from commcare_connect.organization.forms import (
    AddCredentialForm,
    MembershipForm,
    OrganizationChangeForm,
    OrganizationCreationForm,
)
from commcare_connect.organization.models import Organization, UserOrganizationMembership
from commcare_connect.organization.tables import OrgMemberTable
from commcare_connect.organization.tasks import add_credential_task, send_org_invite
from commcare_connect.utils.tables import get_validated_page_size


@login_required
def organization_create(request):
    form = OrganizationCreationForm(data=request.POST or None)

    if form.is_valid():
        org = form.save(commit=False)
        org.save()
        org.members.add(request.user, through_defaults={"role": UserOrganizationMembership.Role.ADMIN})
        return redirect("opportunity:list", org.slug)

    return render(request, "organization/organization_create.html", context={"form": form})


@org_admin_required
def organization_home(request, org_slug):
    org = get_object_or_404(Organization, slug=org_slug)

    form = None
    membership_form = MembershipForm(organization=org)
    if request.method == "POST":
        form = OrganizationChangeForm(request.POST, instance=org)
        if form.is_valid():
            messages.success(request, gettext("Organization details saved!"))
            form.save()

    if not form:
        form = OrganizationChangeForm(instance=org)

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
    form = MembershipForm(request.POST or None, organization=org)

    if form.is_valid():
        form.instance.organization = org
        form.save()
        send_org_invite.delay(membership_id=form.instance.pk, host_user_id=request.user.pk)
    url = reverse("organization:home", args=(org_slug,)) + "?active_tab=members"
    return redirect(url)


@login_required
def accept_invite(request, org_slug, invite_id):
    get_object_or_404(UserOrganizationMembership, invite_id=invite_id)
    messages.success(request, message=f"Accepted invite for joining {org_slug} organization.")
    return redirect("organization:home", org_slug)


@org_admin_required
@require_POST
def add_credential_view(request, org_slug):
    org = get_object_or_404(Organization, slug=org_slug)
    credentials = connect_id_client.fetch_credentials(org_slug=request.org.slug)
    credential_name = f"Worked for {org.name}"
    if not any(c.name == credential_name for c in credentials):
        credentials.append(Credential(name=credential_name, slug=slugify(credential_name)))
    form = AddCredentialForm(data=request.POST, credentials=credentials)

    if form.is_valid():
        users = form.cleaned_data["users"]
        credential = form.cleaned_data["credential"]
        add_credential_task.delay(org.pk, credential, users)
    return redirect("organization:home", org_slug)


@require_GET
@org_admin_required
def org_member_table(request, org_slug=None):
    members = UserOrganizationMembership.objects.filter(organization=request.org)
    table = OrgMemberTable(members)
    RequestConfig(request, paginate={"per_page": get_validated_page_size(request)}).configure(table)
    return render(request, "components/tables/table.html", {"table": table})
