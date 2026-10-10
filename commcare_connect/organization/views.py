from urllib.parse import urlencode

from allauth.account import app_settings as allauth_account_settings
from allauth.account.adapter import get_adapter
from allauth.account.utils import complete_signup, setup_user_email
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils.translation import gettext, gettext_lazy
from django.views.decorators.http import require_GET
from django.views.generic import CreateView, UpdateView, View
from django.views.generic.detail import SingleObjectMixin
from django_filters.views import FilterView
from django_tables2 import RequestConfig, SingleTableMixin
from rest_framework.decorators import api_view

from commcare_connect.organization.decorators import org_admin_access_required, org_profile_edit_access_required
from commcare_connect.organization.filters import ContactFilterSet, OrganizationFilterSet
from commcare_connect.organization.forms import (
    ContactForm,
    InviteAcceptForm,
    OrganizationChangeForm,
    OrganizationCreateForm,
    OrganizationDirectoryForm,
    OrganizationInviteForm,
)
from commcare_connect.organization.models import Contact, Organization, OrganizationInvite, UserOrganizationMembership
from commcare_connect.organization.tables import (
    ContactDirectoryTable,
    OrganizationDirectoryTable,
    OrgMemberTable,
    PendingInviteTable,
)
from commcare_connect.organization.tasks import send_org_invite
from commcare_connect.program.utils import AccessLevel, org_access_level_from_request
from commcare_connect.users.models import User
from commcare_connect.utils.tables import get_validated_page_size


@login_required
def organization_create(request):
    form = OrganizationCreateForm(data=request.POST or None, user=request.user)

    if form.is_valid():
        form.instance.created_by = request.user.email
        org = form.save()
        if form.cleaned_data.get("skip_membership"):
            return redirect("organization:home", org.slug)
        org.members.add(request.user, through_defaults={"role": UserOrganizationMembership.Role.ADMIN})
        return redirect("opportunity:list", org.slug)

    return render(request, "organization/organization_create.html", context={"form": form})


@login_required
def no_organization(request):
    """Landing page for users who don't belong to any organization yet."""
    if request.user.memberships.exists():
        return redirect("users:redirect")

    return render(request, "organization/no_organization.html")


@org_profile_edit_access_required
def organization_home(request, org_slug):
    org = get_object_or_404(Organization, slug=org_slug)

    form = None
    invite_form = OrganizationInviteForm(organization=org)
    if request.method == "POST":
        form = OrganizationChangeForm(request.POST, instance=org, user=request.user)
        if form.is_valid():
            messages.success(request, gettext("Organization details saved!"))
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
            "member_count": org.memberships.count(),
            # Profile-edit permission holders reach this page without admin access to the member endpoints.
            "can_manage_members": org_access_level_from_request(request) >= AccessLevel.ADMIN,
        },
    )


@api_view(["POST"])
@org_admin_access_required
def add_members_form(request, org_slug):
    org = get_object_or_404(Organization, slug=org_slug)
    form = OrganizationInviteForm(request.POST or None, organization=org)

    if form.is_valid():
        invite = OrganizationInvite.send_invite(
            organization=org,
            email=form.cleaned_data["email"],
            role=form.cleaned_data["role"],
            invited_by=request.user,
        )
        if invite is None:
            messages.warning(
                request,
                gettext("An invite was just sent to {email}. Try again in a few minutes.").format(
                    email=form.cleaned_data["email"]
                ),
            )
        else:
            send_org_invite(invite_id=invite.pk)
            messages.success(request, gettext("Invite sent to {email}.").format(email=form.cleaned_data["email"]))
    else:
        error = next(iter(form.errors.values()))[0] if form.errors else gettext("Unable to send invite.")
        messages.error(request, error)
    url = reverse("organization:home", args=(org_slug,)) + "?active_tab=members"
    return redirect(url)


@api_view(["POST"])
@org_admin_access_required
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


def accept_invite(request, org_slug, token):
    invite = get_object_or_404(OrganizationInvite, token=token, organization__slug=org_slug)

    if invite.status != OrganizationInvite.Status.INVITED or invite.is_expired:
        return _reject_invalid_invite(request, invite)

    if request.user.is_authenticated:
        return _accept_invite_for_authenticated_user(request, invite, org_slug)

    if User.objects.filter(email__iexact=invite.email).exists():
        return _redirect_existing_user_to_login(request, invite)

    return _accept_invite_for_new_user(request, invite, org_slug)


def _reject_invalid_invite(request, invite):
    if invite.status == OrganizationInvite.Status.REVOKED:
        messages.error(
            request, gettext("This invitation has been revoked. Contact an admin if you believe this is an error.")
        )
    elif invite.status == OrganizationInvite.Status.ACCEPTED:
        messages.error(
            request, gettext("This invitation has already been accepted. Log in to access the organization.")
        )
    else:
        messages.error(request, gettext("This invitation has expired. Ask an admin to send you a new one."))
    return redirect("account_login")


def _accept_invite_for_authenticated_user(request, invite, org_slug):
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
    return redirect("opportunity:list", org_slug)


def _redirect_existing_user_to_login(request, invite):
    messages.info(
        request, gettext("You've been invited to join {org}. Log in to accept.").format(org=invite.organization.name)
    )
    login_url = reverse("account_login") + "?" + urlencode({"next": request.path})
    return redirect(login_url)


def _accept_invite_for_new_user(request, invite, org_slug):
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
@org_admin_access_required
def reinvite(request, org_slug, invite_id):
    invite = get_object_or_404(
        OrganizationInvite, pk=invite_id, organization__slug=org_slug, status=OrganizationInvite.Status.INVITED
    )
    new_invite = OrganizationInvite.send_invite(
        organization=invite.organization,
        email=invite.email,
        role=invite.role,
        invited_by=request.user,
    )
    if new_invite is None:
        messages.warning(
            request,
            gettext("An invite was just sent to {email}. Try again in a few minutes.").format(email=invite.email),
        )
    else:
        send_org_invite(invite_id=new_invite.pk)
        messages.success(request, gettext("New invite sent to {email}.").format(email=new_invite.email))
    return _render_pending_invites(request)


@api_view(["POST"])
@org_admin_access_required
def revoke_invite(request, org_slug, invite_id):
    invite = get_object_or_404(
        OrganizationInvite, pk=invite_id, organization__slug=org_slug, status=OrganizationInvite.Status.INVITED
    )
    invite.status = OrganizationInvite.Status.REVOKED
    invite.modified_by = request.user.email
    invite.save(update_fields=["status", "modified_by", "date_modified"])
    messages.success(request, gettext("Invite to {email} revoked.").format(email=invite.email))
    return _render_pending_invites(request)


@require_GET
@org_admin_access_required
def org_member_table(request, org_slug=None):
    members = UserOrganizationMembership.objects.filter(organization=request.org)
    table = OrgMemberTable(members)
    RequestConfig(request, paginate={"per_page": get_validated_page_size(request)}).configure(table)
    return render(request, "components/tables/table.html", {"table": table})


@require_GET
@org_admin_access_required
def org_pending_invites_table(request, org_slug=None):
    return _render_pending_invites(request)


def _render_pending_invites(request):
    invites = [
        invite
        for invite in OrganizationInvite.objects.filter(
            organization=request.org, status=OrganizationInvite.Status.INVITED
        ).order_by("-date_modified")
        if not invite.is_expired
    ]
    table = PendingInviteTable(invites)
    RequestConfig(request, paginate={"per_page": get_validated_page_size(request)}).configure(table)
    return render(request, "organization/pending_invites_table.html", {"table": table})


class DirectoryAccessMixin(LoginRequiredMixin, UserPassesTestMixin):
    raise_exception = True

    def test_func(self):
        return self.request.user.can_access_organization_directory


class DirectoryListView(DirectoryAccessMixin, SingleTableMixin, FilterView):
    """A filtered, paginated directory table under the directory's tab bar."""

    def get_paginate_by(self, table_data):
        return get_validated_page_size(self.request)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update(
            {
                "organizations_count": Organization.objects.count(),
                "contacts_count": Contact.objects.filter(is_archived=False).count(),
                "result_count": context["table"].paginator.count,
            }
        )
        return context


class OrganizationListView(DirectoryListView):
    table_class = OrganizationDirectoryTable
    filterset_class = OrganizationFilterSet
    template_name = "organization/directory/organization_list.html"

    def get_queryset(self):
        return Organization.objects.prefetch_related("primary_sectors").order_by("-date_created")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["path"] = [
            {"title": gettext("Admin"), "url": reverse("users:internal_features")},
            {"title": gettext("Organizations"), "url": reverse("organization_directory:list")},
        ]
        return context


class ContactListView(DirectoryListView):
    table_class = ContactDirectoryTable
    filterset_class = ContactFilterSet
    template_name = "organization/directory/contact_list.html"

    def get_queryset(self):
        return Contact.objects.filter(is_archived=False).select_related("organization").order_by("name")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["path"] = [
            {"title": gettext("Admin"), "url": reverse("users:internal_features")},
            {"title": gettext("Contacts"), "url": reverse("organization_directory:contacts")},
        ]
        return context


class DirectoryFormMixin(DirectoryAccessMixin):
    """Serves an add/edit form as a modal fragment to htmx; other requests are sent to `list_url_name`."""

    list_url_name = None
    success_message = None

    def get(self, request, *args, **kwargs):
        if not request.htmx:
            return redirect(self.get_return_url())
        return super().get(request, *args, **kwargs)

    def get_return_url(self):
        """The `next` URL when it is a directory URL on this host, otherwise the `list_url_name` listing."""
        next_url = self.request.GET.get("next", "")
        if next_url.startswith(reverse("organization_directory:list")) and url_has_allowed_host_and_scheme(
            next_url, allowed_hosts={self.request.get_host()}
        ):
            return next_url
        return reverse(self.list_url_name)

    def form_valid(self, form):
        self.object = form.save()
        messages.success(self.request, self.success_message.format(name=self.object.name))
        return HttpResponse(headers={"HX-Redirect": self.get_return_url()})


class OrganizationDirectoryFormMixin(DirectoryFormMixin):
    model = Organization
    form_class = OrganizationDirectoryForm
    template_name = "organization/directory/organization_form.html"
    slug_field = "slug"
    list_url_name = "organization_directory:list"


class OrganizationCreateView(OrganizationDirectoryFormMixin, CreateView):
    # Does not make the requesting user a member of the new organization.
    success_message = gettext_lazy("Organization {name} added.")


class OrganizationUpdateView(OrganizationDirectoryFormMixin, UpdateView):
    success_message = gettext_lazy("Organization {name} updated.")


class ContactFormMixin(DirectoryFormMixin):
    model = Contact
    form_class = ContactForm
    template_name = "organization/directory/contact_form.html"
    list_url_name = "organization_directory:contacts"


class ContactCreateView(ContactFormMixin, CreateView):
    success_message = gettext_lazy("Contact {name} added.")


class ContactUpdateView(ContactFormMixin, UpdateView):
    success_message = gettext_lazy("Contact {name} updated.")


class ContactArchiveView(DirectoryAccessMixin, SingleObjectMixin, View):
    model = Contact
    http_method_names = ["post"]

    def post(self, request, *args, **kwargs):
        contact = self.get_object()
        contact.is_archived = True
        contact.save(update_fields=["is_archived", "date_modified"])
        messages.success(request, gettext("Contact {name} archived.").format(name=contact.name))
        return redirect(f"{reverse('organization_directory:contacts')}?{request.GET.urlencode()}")
