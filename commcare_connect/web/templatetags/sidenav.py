from django import template

register = template.Library()


@register.simple_tag(takes_context=True)
def sidenav_group_open(context: template.Context, *namespaces: str) -> str:
    """Whether a sidenav group starts expanded, as the Alpine literal "true" or "false": it does when the
    current page is in one of ``namespaces``."""
    resolver_match = getattr(context.get("request"), "resolver_match", None)
    current = resolver_match.namespaces if resolver_match else []
    return "true" if any(namespace in current for namespace in namespaces) else "false"
