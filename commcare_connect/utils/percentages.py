def pct(numerator, denominator, ndigits=2):
    """Percentage of ``numerator`` over ``denominator``, rounded to ``ndigits``.

    Returns None for a zero/falsy denominator or a None numerator. Pass ``ndigits=None`` for an
    integer result (``round(x, None)`` returns an int).
    """
    if not denominator or numerator is None:
        return None
    return round(numerator / denominator * 100, ndigits)
