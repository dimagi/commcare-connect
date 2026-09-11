from pathlib import Path

from django.contrib.staticfiles import finders
from django.http import HttpResponse
from django.templatetags.static import static
from django.views.decorators.http import require_GET
from django.views.generic import TemplateView

from .route_meta import meta_for, routes_for_client

# dimagi.com fetches the blog manifest cross-origin from this exact host.
DIMAGI_ORIGIN = "https://dimagi.com"


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
        return ctx


class ContactView(HomeView):
    template_name = "prelogin/contact.html"


home = HomeView.as_view()
contact = ContactView.as_view()


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
