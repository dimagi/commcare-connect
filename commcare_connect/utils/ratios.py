def pct(numerator: float | None, denominator: float | None, ndigits: int | None = 2) -> float | int | None:
    """Percentage of ``numerator`` over ``denominator``, rounded to ``ndigits``.

    Returns None for a zero/falsy denominator or a None numerator. Pass ``ndigits=None`` for an
    integer result (``round(x, None)`` returns an int).
    """
    if not denominator or numerator is None:
        return None
    return round(numerator / denominator * 100, ndigits)


def ratio(numerator: float | None, denominator: float | None, ndigits: int | None = 2) -> float | int | None:
    """Plain ratio of ``numerator`` over ``denominator`` (no ×100), rounded to ``ndigits``.

    Returns None for a zero/falsy denominator or a None numerator.
    """
    if not denominator or numerator is None:
        return None
    return round(numerator / denominator, ndigits)
