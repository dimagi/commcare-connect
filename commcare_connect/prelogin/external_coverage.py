"""External coverage entries for the repeatable "In the news" card grid,
rendered via prelogin/includes/news_section.html on /insights (all entries)
and on portfolio pages (filtered to entries tagged with that program's
slug, via the `for_program` template filter in templatetags/prelogin_extras.py).

To add an entry: append a dict to ENTRIES below with the fields listed
here. Nothing else needs to change -- get_entries() sorts newest-first
automatically, and the include picks up any `programs` slug on its own.

Fields:
    slug          unique id for the entry (str)
    outlet        publication name, e.g. "Devex"
    date          datetime.date -- sort key only; pick any day of the month
                  when the source names just a month
    date_display  human-facing date string shown on the card, e.g. "June 2026"
    title         headline of the piece, as published
    authors       byline, e.g. "Jane Doe and John Smith"
    label         framing tag shown on the card -- be exact about whether
                  the piece is editorial, opinion, or sponsored, e.g.
                  "Opinion · Sponsored by Example Org". Never render this
                  as "As featured in <outlet>" when it isn't editorial coverage.
    summary       ONE original sentence we write ourselves. Most outlets
                  (Devex included) prohibit reproducing article text --
                  never copy or closely paraphrase source sentences here.
                  We link out; we don't quote.
    url           link to the article. We link out only -- never mirror the
                  article text or its images (or the outlet's hosted image)
                  into this repo.

Optional fields:
    programs      list of portfolio program slugs (e.g. ["reading-glasses"])
                  this entry should also appear on.
    featured      bool, reserved for a future "pin to top" behavior. Unused
                  today -- entries always sort newest-first by `date`.

Example -- adding a second entry:
    {
        "slug": "example-outlet-headline-slug",
        "outlet": "Example Outlet",
        "date": datetime.date(2026, 8, 1),
        "date_display": "August 2026",
        "title": "Headline as published",
        "authors": "Author Name",
        "label": "News",
        "summary": "One original sentence explaining why this matters.",
        "url": "https://example.com/article",
        "programs": ["kangaroo-mother-care"],
    },
"""

import datetime

ENTRIES = [
    {
        "slug": "devex-tech-investment-blind-spot",
        "outlet": "Devex",
        "date": datetime.date(2026, 6, 1),
        "date_display": "June 2026",
        "title": "Tech investment has a major blind spot",
        "authors": "Anne-Marie Grey and Jonathan Jackson",
        "label": "Opinion · Sponsored by RestoringVision",
        "summary": (
            "Digital tools for frontline health delivery assume every user can read a "
            "small screen clearly, but for the millions of Frontline Workers and adult "
            "beneficiaries over 35 living with uncorrected presbyopia, that assumption "
            "alone can cap how much return those tech investments ever deliver."
        ),
        "url": "https://www.devex.com/news/sponsored/tech-investment-has-a-major-blind-spot-112642",
        "programs": ["reading-glasses"],
    },
]


def get_entries():
    """All ENTRIES, sorted newest first by `date`."""
    return sorted(ENTRIES, key=lambda entry: entry["date"], reverse=True)
