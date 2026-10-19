import re

from django.urls import path, re_path
from django.views.generic import RedirectView

from . import views

app_name = "prelogin"

# The marketing site now lives in its own repo (connect-marketing) and the
# load balancer routes only /public/* and /blog/* to it. Every other path on
# connect.dimagi.com lands here, so the old marketing URLs (live, indexed,
# bookmarked) are permanently redirected to their new /public/ locations.
#
# Matched with an optional trailing slash; query strings (e.g. the
# /insights?program=kmc filters) are carried through to the new URL.
MARKETING_REDIRECTS = {
    "": "/public",
    # Marketing URLs as they were served from here before the move
    "the-opportunity": "/public/the-opportunity",
    "platform": "/public/platform",
    "portfolio": "/public/portfolio",
    "insights": "/public/insights",
    "release-notes": "/public/release-notes",
    "frontline-network": "/public/frontline-network",
    "support-kmc": "/public/support-kmc",
    "contact": "/public/contact",
    "contact/index.html": "/public/contact",
    # Old blog listing page, merged into Insights. Individual /blog/<slug>
    # posts keep their URLs and are served by the marketing site. Listed here
    # in case the load balancer's /blog/* rule doesn't match bare /blog.
    "blog": "/public/insights",
    # Older aliases from the old client router's LEGACY_ROUTES map
    "how-it-works": "/public/platform",
    "the-why": "/public/the-opportunity",
    "join": "/public/frontline-network",
    "product-updates": "/public/release-notes",
    "programs": "/public/portfolio",
}

urlpatterns = [
    re_path(
        rf"^{re.escape(route)}/?$" if route else r"^$",
        RedirectView.as_view(url=target, permanent=True, query_string=True),
        name=f"redirect-{route or 'home'}",
    )
    for route, target in MARKETING_REDIRECTS.items()
]

# Program detail pages: /portfolio/<slug>, plus the legacy /programs/<slug>.
urlpatterns += [
    re_path(
        r"^(?:portfolio|programs)/(?P<slug>[\w-]+)/?$",
        RedirectView.as_view(url="/public/portfolio/%(slug)s", permanent=True, query_string=True),
        name="redirect-portfolio-detail",
    ),
]

# "Connect in action" story pages — standalone templates, not yet migrated to
# the marketing site, so still served here.
urlpatterns += [
    path(
        "in-action/nigeria-cholera-response",
        views.in_action_nigeria_cholera,
        name="in-action-nigeria-cholera-response",
    ),
]
