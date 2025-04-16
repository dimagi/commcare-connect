from urllib.parse import parse_qs, urlencode, urlparse

from django import template
from django.utils.html import format_html, strip_tags
from django.utils.safestring import mark_safe

register = template.Library()


@register.simple_tag(takes_context=True)
def sort_link(context, field, display_text):
    request = context["request"]
    current_sort = request.GET.get("sort", "name")

    # Determine the new sorting order and icon based on the current sorting state
    if current_sort == field:
        new_sort = f"-{field}"
        icon = "bi-arrow-up-square-fill"
        page = request.GET.get("page")
    elif current_sort == f"-{field}":
        new_sort = field
        icon = "bi-arrow-down-square-fill"
        page = request.GET.get("page")
    else:
        new_sort = field
        icon = "bi-arrow-down-short"
        page = None

    # Construct the URL with the new sorting parameter
    url = f"{request.path}?sort={new_sort}"
    if page:
        url += f"&page={page}"

    # Return the HTML for the link with the optional icon
    return format_html(
        '<a style="text-decoration: none; color: inherit;" href="{}">{} <i class="bi {}"></i></a>',
        url,
        display_text,
        icon,
    )


@register.simple_tag(takes_context=True)
def update_query_params(context, **kwargs):
    request = context["request"]
    updated = request.GET.copy()

    for key, value in kwargs.items():
        updated[key] = value

    return updated.urlencode()


@register.simple_tag(takes_context=True)
def sortable_header(context, field, label, use_htmx=False):
    request = context["request"]
    current_sort = request.GET.get("sort", "name")

    if current_sort == field:
        next_sort = f"-{field}"
        icon_class = "fa-sort-asc text-brand-deep-purple"
    elif current_sort == f"-{field}":
        next_sort = field
        icon_class = "fa-sort-desc text-brand-deep-purple"
    else:
        next_sort = field
        icon_class = "fa-sort text-gray-400"

    # Build query parameters
    query_params = request.GET.copy()
    query_params["sort"] = next_sort

    # Retain page param only if sorting same field
    if current_sort not in (field, f"-{field}"):
        query_params.pop("page", None)

    query_string = query_params.urlencode()
    url = f"{request.path}?{query_string}" if query_string else request.path

    icon_element = f'<i class="fa-solid ml-1 {icon_class}"></i>'
    label_text = strip_tags(label)

    return format_html(
        '<a href="{}" class="flex items-center text-sm font-medium text-brand-deep-purple">{}</a>',
        url,
        mark_safe(f"{label_text} {icon_element}"),
    )
