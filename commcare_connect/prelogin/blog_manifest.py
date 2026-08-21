"""Runtime-extracted manifest of every native Connect blog post.

dimagi.com/blog fetches this at page load (CORS-open to that origin) and renders
Connect link-out cards, the same way it already pulls commcare.dimagi.com/blog/
manifest.json. So a Connect post published here shows up on dimagi.com's blog on
its next page load, with no cross-repo export or rebuild on either side.

Rather than keep a second hand-maintained list in sync with the cards on the
Insights page, this reads the Insights markup itself (home.html) and extracts the
``data-type="blog"`` cards — the page's own source of truth. Podcast link-out
cards (``blog-card--podcast``, which point back to the Dimagi podcast) are
excluded: dimagi.com already owns those, pulling them back would be circular.

Each card may carry ``data-origin="https://dimagi.com/blog/<slug>/"`` — the URL of
the Dimagi post this Connect post was adapted from. dimagi.com uses it to suppress
the duplicate while that original still lives natively on dimagi.com; once the
originals are moved here and redirected, the match disappears and the Connect card
surfaces automatically. A brand-new Connect post carries no ``data-origin`` and so
appears on dimagi.com immediately.
"""

import html
import re

CONNECT_ORIGIN = "https://connect.dimagi.com"

# Each <a class="blog-card" ... data-type="blog" ...> ... </a> block. The class
# is matched exactly, so "blog-card blog-card--podcast" anchors never match; the
# data-type guard is a second belt-and-braces filter. No <a> nests inside a card,
# so the first </a> closes it.
_CARD_RE = re.compile(
    r'<a class="blog-card"\s+href="(?P<href>/blog/[^"]+)"[^>]*' r'data-type="blog"(?P<attrs>[^>]*)>(?P<body>.*?)</a>',
    re.DOTALL,
)
_STATIC_RE = re.compile(r"{%\s*static\s+['\"]([^'\"]+)['\"]\s*%}")
_ATTR_RE = lambda name: re.compile(name + r'="([^"]*)"')  # noqa: E731
_TAG_RE = re.compile(r'<span class="blog-tag">(.*?)</span>', re.DOTALL)
_TIME_RE = re.compile(r'<time datetime="([^"]*)">(.*?)</time>', re.DOTALL)
_TITLE_RE = re.compile(r"<h3>(.*?)</h3>", re.DOTALL)
_EXCERPT_RE = re.compile(r'<p class="blog-card-excerpt">(.*?)</p>', re.DOTALL)
_IMG_SRC_RE = re.compile(r'<img[^>]*\ssrc="([^"]*)"')


def _clean(fragment):
    """Collapse whitespace and decode HTML entities in an inner-text fragment."""
    if fragment is None:
        return ""
    return html.unescape(re.sub(r"\s+", " ", fragment).strip())


def _iso_date(datetime_attr):
    """A ``<time datetime>`` of "2025-11" (or "2025-11-03") to a full ISO date."""
    if not datetime_attr:
        return None
    parts = datetime_attr.split("-")
    year = parts[0]
    month = parts[1] if len(parts) > 1 else "01"
    day = parts[2] if len(parts) > 2 else "01"
    return f"{year}-{month.zfill(2)}-{day.zfill(2)}"


def extract_posts(source, static_url, build_absolute_uri):
    """Parse the Insights markup into manifest post dicts.

    ``static_url`` resolves a ``{% static %}`` path to its served URL (hashed on
    prod); ``build_absolute_uri`` makes a site-relative URL absolute. Both are
    injected so this stays a pure function that unit tests can drive without a
    request.
    """
    posts = []
    for card in _CARD_RE.finditer(source):
        attrs, body = card.group("attrs"), card.group("body")
        href = card.group("href")

        origin_m = _ATTR_RE("data-origin").search(attrs)
        program_m = _ATTR_RE("data-program").search(attrs)
        tag_m = _TAG_RE.search(body)
        time_m = _TIME_RE.search(body)
        title_m = _TITLE_RE.search(body)
        excerpt_m = _EXCERPT_RE.search(body)
        img_m = _IMG_SRC_RE.search(body)

        cover_image = None
        if img_m:
            raw_src = img_m.group(1)
            static_m = _STATIC_RE.search(raw_src)
            resolved = static_url(static_m.group(1)) if static_m else raw_src
            cover_image = build_absolute_uri(resolved)

        posts.append(
            {
                "slug": href.replace("/blog/", "").strip("/"),
                "url": build_absolute_uri(href),
                "title": _clean(title_m.group(1)) if title_m else "",
                "description": _clean(excerpt_m.group(1)) if excerpt_m else "",
                "coverImage": cover_image,
                "tag": _clean(tag_m.group(1)) if tag_m else "",
                "program": program_m.group(1) if program_m else "",
                "date": _iso_date(time_m.group(1)) if time_m else None,
                "dateLabel": _clean(time_m.group(2)) if time_m else "",
                "originalUrl": origin_m.group(1) if origin_m else None,
            }
        )
    return posts
