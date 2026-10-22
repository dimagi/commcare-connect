from pathlib import Path

from django.conf import settings
from django.contrib.staticfiles import finders
from django.http import Http404, HttpResponse
from django.templatetags.static import static
from django.urls import reverse
from django.views.decorators.http import require_GET
from django.views.generic import RedirectView, TemplateView

from .external_coverage import get_entries as get_news_entries
from .route_meta import meta_for, routes_for_client

# dimagi.com fetches the blog manifest cross-origin from this exact host.
DIMAGI_ORIGIN = "https://dimagi.com"


class MarketingRedirectView(RedirectView):
    """Permanently redirect an old marketing URL to its /public/ home, or to the login
    page when the marketing site isn't deployed (``MARKETING_SITE_AVAILABLE``)."""

    permanent = True
    query_string = True

    def get_redirect_url(self, *args, **kwargs):
        if settings.MARKETING_SITE_AVAILABLE:
            return super().get_redirect_url(*args, **kwargs)
        # Temporary, so browsers don't cache it past the marketing site being switched on
        self.permanent = False
        return reverse(settings.LOGIN_URL)


class MissingMarketingSiteView(RedirectView):
    """/public/* only reaches this app when the marketing site isn't deployed."""

    def get_redirect_url(self, *args, **kwargs):
        if settings.MARKETING_SITE_AVAILABLE:
            raise Http404
        return reverse(settings.LOGIN_URL)


class HomeView(TemplateView):
    template_name = "prelogin/home.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        # Every marketing URL renders this same template, so the head has to be
        # built per request. Link unfurlers and non-rendering crawlers never run
        # app.js, and until this was here they all saw the home page's title,
        # description, and image no matter which page was shared.
        ctx["page_meta"] = meta_for(self.request.path, static)
        ctx["route_meta"] = routes_for_client(static)
        # External "In the news" coverage entries -- see external_coverage.py.
        # Every route renders this same template, so this is cheap to always
        # compute; prelogin/includes/news_section.html decides per-page
        # whether/which of them to show.
        ctx["news_entries"] = get_news_entries()
        return ctx


class ContactView(HomeView):
    template_name = "prelogin/contact.html"


class InActionView(HomeView):
    """ "Connect in action" story pages.

    Standalone templates rather than SPA sections: each one carries its own
    stylesheet and scroll script, and none of it belongs in home.html's bundle.
    """

    template_name = "prelogin/in-action.html"


home = HomeView.as_view()
contact = ContactView.as_view()
in_action_nigeria_cholera = InActionView.as_view()


@require_GET
def blog_manifest(request):
    """Serve the hardcoded feed of native Connect blog posts dimagi.com renders
    as link-out cards on its own blog.

    ``static/prelogin/blog/manifest.json`` is maintained by hand — update it
    whenever a post is added, edited, or removed from the Insights page.
    """
    path = finders.find("prelogin/blog/manifest.json")
    response = HttpResponse(Path(path).read_bytes(), content_type="application/json")
    response["Access-Control-Allow-Origin"] = DIMAGI_ORIGIN
    response["Cache-Control"] = "public, max-age=300, stale-while-revalidate=86400"
    return response
