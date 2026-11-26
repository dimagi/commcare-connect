from django.shortcuts import redirect
from django_htmx.http import HttpResponseClientRedirect, push_url


class HtmxModalFormMixin:
    """For a form view that htmx loads into a modal on the page at `get_success_url()`.
    Errors swap the form back in; success reloads that page.
    """

    def get(self, request, *args, **kwargs):
        # The form is only a modal fragment; a direct visit gets the page that hosts it.
        if not request.htmx:
            return redirect(self.get_success_url())
        # A boosted link would otherwise put the fragment's URL in the address bar; since htmx 2.0.10 only
        # this header stops that, as hx-push-url="false" no longer overrides hx-boost.
        return push_url(super().get(request, *args, **kwargs), False)

    def form_valid(self, form):
        response = super().form_valid(form)
        if self.request.htmx:
            return HttpResponseClientRedirect(response.url)
        return response
