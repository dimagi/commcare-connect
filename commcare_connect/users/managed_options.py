"""Base views for the Internal Features pages that manage a model's name/slug/description options.

Each page is a list with an Add/Edit modal and a guarded Delete. A model plugs in by subclassing these views with a
shared mixin that sets `model`, `form_class` (a `ManagedOptionForm`), `url_prefix`, `in_use_message` and
`in_use_hint`, and by
giving the model an `is_in_use()` method. Its list view annotates `in_use` onto the queryset and its list template
extends `users/managed_options/list.html`. The URLs are named `<url_prefix>_list`, `_create`, `_update` and `_delete`.
"""

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin, PermissionRequiredMixin
from django.contrib.messages.views import SuccessMessageMixin
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.utils.text import capfirst
from django.utils.translation import gettext_lazy as _
from django.views.generic import CreateView, ListView, UpdateView, View

from commcare_connect.utils.permission_const import ORG_MANAGEMENT_SETTINGS_ACCESS


class ManagedOptionMixin(LoginRequiredMixin, PermissionRequiredMixin):
    permission_required = ORG_MANAGEMENT_SETTINGS_ACCESS
    raise_exception = True
    model = None
    url_prefix = None
    # Shown when a delete is refused; formatted with the option's %(name)s.
    in_use_message = None
    # Tooltip on the list's disabled Delete button.
    in_use_hint = None

    def url_name(self, action):
        return f"{self.url_prefix}_{action}"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["option"] = {
            "verbose_name": self.model._meta.verbose_name,
            "verbose_name_plural": self.model._meta.verbose_name_plural,
            "in_use_hint": self.in_use_hint,
            **{f"{action}_url_name": self.url_name(action) for action in ("create", "update", "delete")},
        }
        return context


class ManagedOptionListView(ManagedOptionMixin, ListView):
    """Subclasses set the template and annotate `in_use` in `get_queryset`, so the list can disable Delete
    without a query per row."""


class ManagedOptionFormMixin(ManagedOptionMixin, SuccessMessageMixin):
    """Serves the list page's Add/Edit modal: errors swap the form back in, success reloads the list."""

    template_name = "users/managed_options/form.html"

    def get(self, request, *args, **kwargs):
        # The form is only a modal fragment; a direct visit gets the list page that hosts it.
        if not request.htmx:
            return redirect(self.get_success_url())
        return super().get(request, *args, **kwargs)

    def form_valid(self, form):
        response = super().form_valid(form)
        if self.request.htmx:
            return HttpResponse(headers={"HX-Redirect": self.get_success_url()})
        return response

    def get_success_message(self, cleaned_data):
        return _("%(option)s '%(name)s' saved.") % {
            "option": capfirst(self.model._meta.verbose_name),
            "name": cleaned_data["name"],
        }

    def get_success_url(self):
        return reverse(self.url_name("list"))


class ManagedOptionCreateView(ManagedOptionFormMixin, CreateView):
    pass


class ManagedOptionUpdateView(ManagedOptionFormMixin, UpdateView):
    pass


class ManagedOptionDeleteView(ManagedOptionMixin, View):
    http_method_names = ["post"]

    def post(self, request, pk):
        # Locked until the request commits. A concurrent save referencing this option needs a key-share lock on the
        # row for its foreign-key check, so it can't commit between the in-use check and the delete.
        option = get_object_or_404(self.model.objects.select_for_update(), pk=pk)
        if option.is_in_use():
            messages.error(request, self.in_use_message % {"name": option.name})
        else:
            option.delete()
            messages.success(
                request,
                _("%(option)s '%(name)s' deleted.")
                % {"option": capfirst(self.model._meta.verbose_name), "name": option.name},
            )
        return redirect(self.url_name("list"))
