from urllib.parse import urlencode

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.translation import gettext
from django.views.decorators.http import require_GET
from django_tables2 import RequestConfig
from rest_framework.decorators import api_view

from commcare_connect.organization.decorators import org_admin_required
from commcare_connect.organization.forms import MembershipForm, OrganizationChangeForm, OrganizationCreationForm
from commcare_connect.organization.models import Organization, UserOrganizationMembership
from commcare_connect.organization.tables import OrgMemberTable
from commcare_connect.organization.tasks import send_org_invite
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
        form = OrganizationChangeForm(request.POST, instance=org, user=request.user)
        if form.is_valid():
            messages.success(request, gettext("Organization details saved!"))
            form.save()

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
    form = MembershipForm(request.POST or None, organization=org)

    if form.is_valid():
        form.instance.organization = org
        form.save()
        send_org_invite(membership_id=form.instance.pk, host_user_id=request.user.pk)
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
        messages.error(request, message=gettext("You cannot remove yourself from the organization."))
        return redirect(redirect_url)

    if membership_ids:
        UserOrganizationMembership.objects.filter(pk__in=membership_ids, organization__slug=org_slug).delete()
        messages.success(request, message=gettext("Selected members have been removed from the organization."))

    return redirect(redirect_url)


@login_required
def accept_invite(request, org_slug, invite_id):
    get_object_or_404(UserOrganizationMembership, invite_id=invite_id)
    messages.success(request, message=f"Accepted invite for joining {org_slug} organization.")
    return redirect("organization:home", org_slug)


@require_GET
@org_admin_required
def org_member_table(request, org_slug=None):
    members = UserOrganizationMembership.objects.filter(organization=request.org)
    table = OrgMemberTable(members)
    RequestConfig(request, paginate={"per_page": get_validated_page_size(request)}).configure(table)
    return render(request, "components/tables/table.html", {"table": table})
