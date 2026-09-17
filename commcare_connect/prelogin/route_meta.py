"""Per-route title, description, and social image for the marketing SPA.

Every marketing URL renders the SAME home.html, so without this the whole site
would ship one <head>: a reader sharing a blog post got the home page's title,
description, and image. app.js fixed that in the browser, but link unfurlers
(LinkedIn, Slack, X, iMessage) and non-rendering crawlers never run JavaScript,
so they only ever saw the home page.

This module is the single source of truth. views.HomeView reads it to render the
head server-side, and home.html serializes it into a <script type="application/
json"> block that app.js parses for client-side route changes, so the two can no
longer drift.

Adding a page means adding an entry here, a data-page section in home.html, a
route in urls.py, and a sitemap.xml entry.
"""

SITE_ORIGIN = "https://connect.dimagi.com"

# Static path, resolved through {% static %} / django.templatetags.static so it
# survives the hashed-static storage used in production. The old hardcoded
# "/images/field-photos/join-hero.jpg" in home.html was missing the /static/
# prefix and 404d, so every share of this site had a broken preview image.
DEFAULT_IMAGE = "prelogin/images/field-photos/join-hero.jpg"
DEFAULT_IMAGE_ALT = "A Connect Frontline Worker delivering community health services"

ROUTES = {
    "/": {
        "title": "Connect by Dimagi | Pay for verified service delivery",
        "desc": "Connect is a pay-for-results service delivery platform. Pick a program, country, and budget. Verified Frontline Workers deliver and get paid the moment each service is confirmed.",
    },
    "/the-opportunity": {
        "title": "The opportunity | Connect by Dimagi",
        "desc": "Funders can’t verify what reaches communities, and Frontline Workers deliver results no one tracks. Connect is the missing link between them.",
    },
    "/platform": {
        "title": "Platform | Connect by Dimagi",
        "desc": "Pick the program, geography, and amount. Connect activates local organizations, verifies every service, and pays only for what’s confirmed.",
    },
    "/portfolio": {
        "title": "Portfolio | Connect by Dimagi",
        "desc": "Explore the growing portfolio of high-impact health and development programs delivered and verified through Connect, with new programs and countries added all the time.",
    },
    "/frontline-network": {
        "title": "Frontline Network | Connect by Dimagi",
        "desc": "Hundreds of frontline organizations already deliver services and get paid for verified work through Connect. See why they join and run programs on the network.",
    },
    "/insights": {
        "title": "Connect Insights | Connect by Dimagi",
        "desc": "Stories, results, conversations, and evidence from the organizations and Frontline Workers delivering verified services through Connect.",
    },
    "/release-notes": {
        "title": "Release notes | Connect by Dimagi",
        "desc": "Release notes for the Connect platform, published with each major update to the mobile app and web tools.",
    },
    "/blog/closing-the-post-discharge-gap-kmc": {
        "title": "Closing the post-discharge gap with Connect Kangaroo Mother Care | Connect by Dimagi",
        "desc": "Trained Frontline Workers carry Kangaroo Mother Care into the home, closing the dangerous gap that opens when families of small and vulnerable newborns leave the hospital.",
        "image": "prelogin/images/blog/kmc-post-discharge.jpg",
        "image_alt": "Closing the post-discharge gap with Connect Kangaroo Mother Care",
    },
    "/blog/usaid-div-child-health-nigeria": {
        "title": "USAID Development Innovation Ventures and Dimagi partner on child health in northern Nigeria | Connect by Dimagi",
        "desc": "A new grant will scale and rigorously evaluate Connect, delivering Vitamin A and oral rehydration solution to children where coverage is lowest.",
        "image": "prelogin/images/blog/usaid-div-child-health.jpg",
        "image_alt": "USAID Development Innovation Ventures and Dimagi partner on child health in northern Nigeria",
    },
    "/blog/givewell-child-health-nigeria": {
        "title": "GiveWell awards Dimagi funding to scale child health delivery in Nigeria | Connect by Dimagi",
        "desc": "GiveWell has awarded Dimagi more than $1 million to scale verified child health delivery across northern Nigeria, funding over 300,000 visits in 18 months.",
        "image": "prelogin/images/blog/givewell-child-health.jpg",
        "image_alt": "GiveWell awards Dimagi funding to scale child health delivery in Nigeria",
    },
    "/blog/connect-100000-service-deliveries": {
        "title": "Connect reaches 100,000 service deliveries | Connect by Dimagi",
        "desc": "Across seven countries, Frontline Workers delivered more than 100,000 verified services, a foundation for scaling to millions of visits a year.",
        "image": "prelogin/images/blog/connect-100000-deliveries.jpg",
        "image_alt": "Connect reaches 100,000 service deliveries",
    },
    "/blog/connect-2023-year-in-review": {
        "title": "Connect: a year in review | Connect by Dimagi",
        "desc": "The first year of designing and testing Connect: 77,000 clients reached, $161,000 paid to Frontline Workers, and the learn, deliver, verify, pay model proven in the field.",
        "image": "prelogin/images/blog/connect-2023-year-in-review.jpg",
        "image_alt": "Connect: a year in review",
    },
    "/portfolio/child-health-campaign": {
        "title": "Child Health Campaigns | Connect by Dimagi",
        "desc": "Door-to-door delivery of high-impact health services to every child under five, verified visit by visit and paid only for what’s confirmed.",
    },
    "/portfolio/kangaroo-mother-care": {
        "title": "Kangaroo Mother Care | Connect by Dimagi",
        "desc": "Structured home visits for small and vulnerable newborns in their first 60 days, verified and paid only when confirmed, closing the post-discharge gap.",
    },
    "/portfolio/early-childhood-development": {
        "title": "Early Childhood Development | Connect by Dimagi",
        "desc": "Home visits supporting responsive caregiving and early child development, building caregiver knowledge, observable teaching behavior, and child autonomy.",
    },
    "/portfolio/reading-glasses": {
        "title": "Reading Glasses | Connect by Dimagi",
        "desc": "Door-to-door near-vision screening and presbyopia correction across northeast Nigeria.",
    },
    "/portfolio/mother-baby-wellness": {
        "title": "Mother Baby Wellness | Connect by Dimagi",
        "desc": "Frontline coaches support families with breastfeeding support and maternal mental health care. Six structured home visits per family, paid on verified outcomes.",
    },
    "/portfolio/chlorine-dispenser": {
        "title": "Chlorine Dispenser | Connect by Dimagi",
        "desc": "Chlorine dispensers at communal water points, paired with door-to-door household education on safe water treatment, one of the highest-evidence, lowest-cost ways to prevent diarrhea.",
    },
    "/portfolio/mental-health": {
        "title": "Group Therapy for Depression | Connect by Dimagi",
        "desc": "Connect trains local facilitators to run structured weekly group therapy for depression, with every session app-guided, verified, and paid only when confirmed.",
    },
    "/portfolio/survey-data-collection": {
        "title": "Connect Interview | Connect by Dimagi",
        "desc": "Connect Interview turns Frontline Workers into a rapid research network. Stakeholders submit questions, an AI chatbot interviews workers in-app, and Dimagi delivers transcripts within two weeks.",
    },
    "/portfolio/therapeutic-food": {
        "title": "Therapeutic Food | Connect by Dimagi",
        "desc": "Frontline Workers deliver home-based malnutrition treatment with Ready-to-Use Therapeutic Food (RUTF). Every visit is verified with GPS, timestamps, and photos.",
    },
    "/portfolio/rooftop-sampling": {
        "title": "Rooftop Sampling | Connect by Dimagi",
        "desc": "A GPS-navigated household survey method that uses satellite building footprints as the sampling frame, no household list required. Developed by IDinsight.",
    },
    "/support-kmc": {
        "title": "Fund a Frontline Worker | Connect by Dimagi",
        "desc": "Your gift funds a trained Frontline Worker to deliver verified Kangaroo Mother Care home visits to small and vulnerable newborns. $60 covers one complete, verified intervention.",
    },
}


def normalize(path):
    """Map a request path onto a ROUTES key.

    Routes are registered without trailing slashes, but a stray one should not
    silently drop the page back to the home entry.
    """
    path = "/" + (path or "").strip("/")
    return path


def meta_for(path, static_url):
    """Head metadata for one request path.

    ``static_url`` is django.templatetags.static.static (injected so this stays
    importable without app config in tests).
    """
    route = normalize(path)
    meta = ROUTES.get(route)
    if meta is None:
        # The client router shows the home page for anything it does not know,
        # so the canonical has to say "/" too. Pointing it at the unknown path
        # would claim a URL that serves home content as a page of its own.
        route, meta = "/", ROUTES["/"]
    image = meta.get("image", DEFAULT_IMAGE)
    return {
        "title": meta["title"],
        "description": meta["desc"],
        "canonical": SITE_ORIGIN + route,
        "image": SITE_ORIGIN + static_url(image),
        "image_alt": meta.get("image_alt", DEFAULT_IMAGE_ALT),
        # A blog post is an article; everything else is a site page. Unfurlers
        # and schema consumers treat the two differently.
        "og_type": "article" if route.startswith("/blog/") else "website",
    }


def routes_for_client(static_url):
    """The same table, shaped for app.js.

    home.html renders this as JSON so the client router can retitle the document
    on in-page navigation. Serving it from here is what keeps the browser and the
    server from drifting apart, which is exactly what happened when app.js
    carried its own copy of this table.
    """
    return {
        route: {
            "title": meta["title"],
            "desc": meta["desc"],
            "image": SITE_ORIGIN + static_url(meta.get("image", DEFAULT_IMAGE)),
            "imageAlt": meta.get("image_alt", DEFAULT_IMAGE_ALT),
        }
        for route, meta in ROUTES.items()
    }
