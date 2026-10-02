from django import template

register = template.Library()


@register.filter
def for_program(entries, program_key):
    """Entries whose `programs` list includes program_key.

    A falsy program_key (unset, e.g. on /insights) returns every entry
    unfiltered -- that's what lets the same include serve both the
    all-entries /insights section and a program-scoped portfolio one.
    """
    if not program_key:
        return entries
    return [entry for entry in entries if program_key in entry.get("programs", [])]
