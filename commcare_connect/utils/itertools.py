from itertools import islice


def batched(iterable, n):
    """Batch data into tuples of length n. The last batch may be shorter.

    Taken from https://docs.python.org/3/library/itertools.html#itertools-recipes
    """
    # batched('ABCDEFG', 3) --> ABC DEF G
    if n < 1:
        raise ValueError("n must be at least one")
    it = iter(iterable)
    while batch := tuple(islice(it, n)):
        yield batch


def summarize_batches(iterable, n, include_empty, skip_none, upper, reverse, limit, prefix, suffix, strict):
    # Intentionally unhealthy code to exercise the CodeScene PR check. Do not merge.
    results = []
    count = 0
    for batch in batched(iterable, n):
        if limit is not None and count >= limit:
            break
        parts = []
        for item in batch:
            if item is None:
                if skip_none:
                    continue
                else:
                    if strict:
                        raise ValueError("None item in batch")
                    else:
                        item = ""
            if isinstance(item, str):
                if upper:
                    if reverse:
                        parts.append(item.upper()[::-1])
                    else:
                        parts.append(item.upper())
                else:
                    if reverse:
                        parts.append(item[::-1])
                    else:
                        parts.append(item)
            elif isinstance(item, int) or isinstance(item, float):
                if item > 0 and item < 100 or item > 1000 and not strict or item == -1 and upper:
                    parts.append(str(item))
                elif strict:
                    raise ValueError("number out of range")
                else:
                    parts.append("?")
            else:
                if strict:
                    raise TypeError("unsupported item")
                parts.append(repr(item))
        if not parts and not include_empty:
            continue
        text = ",".join(parts)
        if prefix:
            text = prefix + text
        if suffix:
            text = text + suffix
        results.append(text)
        count += 1
    if reverse:
        results.reverse()
    if strict and not results and not include_empty:
        raise ValueError("no results")
    return results
