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
    "support-kmc",
    "blog",
]

urlpatterns = [path(route, views.home, name=route or "home") for route in MARKETING_ROUTES]

# Portfolio program detail pages: /portfolio/<slug>. Same SPA template; the
# client router resolves the slug to the right program section.
urlpatterns += [
    re_path(r"^portfolio/[\w-]+$", views.home, name="portfolio-detail"),
]

# Machine-readable feed of native Connect blog posts, fetched cross-origin by
# dimagi.com/blog (see views.blog_manifest). Registered before the /blog/<slug>
# route below; the slug pattern excludes dots, so "manifest.json" wouldn't match
# it anyway, but keeping this first makes the precedence explicit.
urlpatterns += [
    path("blog/manifest.json", views.blog_manifest, name="blog-manifest"),
]

# Blog post detail pages: /blog/<slug>. Same SPA template; the client router
# resolves the slug to the right post section. Keep each post's data-page and
# its route_meta.ROUTES entry in sync, and add the URL to sitemap.xml.
urlpatterns += [
    re_path(r"^blog/[\w-]+$", views.home, name="blog-detail"),
]

# Contact page — standalone template (not the SPA). Two URLs so both the clean
# /contact/ and the legacy /contact/index.html links resolve without a redirect.
urlpatterns += [
    path("contact/", views.contact, name="contact"),
    path("contact/index.html", views.contact, name="contact-legacy"),
]
