from django.urls import path, re_path

from . import views

app_name = "prelogin"

# The marketing site is a single SPA template with a History-API router in
# app.js (clean, hash-free URLs). The client picks the right <section
# data-page="…"> for the current path, so every route renders the same
# home.html. Server-side we must still list each route, otherwise a direct
# load or refresh of e.g. /platform hits Django (not the router) and 404s.
#
# All routes go through views.home (HomeView) — not a bare TemplateView.
# Keep this list in sync with the data-page routes in index.html upstream
# (dimagi-internal/connect-prelogin) and with sitemap.xml.
#
# Do NOT use a blanket catch-all: this host also serves the real app
# (/accounts/…, /a/<org>/…, dashboards); a wildcard would shadow it.
MARKETING_ROUTES = [
    "",  # home
    "the-opportunity",
    "platform",
    "portfolio",
    "insights",
    "release-notes",
    "frontline-network",
]

urlpatterns = [path(route, views.home, name=route or "home") for route in MARKETING_ROUTES]

# Portfolio program detail pages: /portfolio/<slug>. Same SPA template; the
# client router resolves the slug to the right program section.
urlpatterns += [
    re_path(r"^portfolio/[\w-]+$", views.home, name="portfolio-detail"),
]

# Contact page — standalone template (not the SPA). Two URLs so both the clean
# /contact/ and the legacy /contact/index.html links resolve without a redirect.
urlpatterns += [
    path("contact/", views.contact, name="contact"),
    path("contact/index.html", views.contact, name="contact-legacy"),
]
