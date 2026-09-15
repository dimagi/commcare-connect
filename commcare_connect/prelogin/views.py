from pathlib import Path

from django.contrib.staticfiles import finders
from django.http import HttpResponse
from django.views.decorators.http import require_GET
from django.views.generic import TemplateView

# dimagi.com fetches the blog manifest cross-origin from this exact host.
DIMAGI_ORIGIN = "https://dimagi.com"


class HomeView(TemplateView):
    template_name = "prelogin/home.html"


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
