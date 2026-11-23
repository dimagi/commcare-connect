"""What a page's form posts, for the parts every page builds the same way."""


def management_form(prefix, *, total, max_num):
    """The hidden fields Django renders with a formset, as a browser posts them for ``total`` rows."""
    return {
        f"{prefix}-TOTAL_FORMS": total,
        f"{prefix}-INITIAL_FORMS": 0,
        f"{prefix}-MIN_NUM_FORMS": 0,
        f"{prefix}-MAX_NUM_FORMS": max_num,
    }
