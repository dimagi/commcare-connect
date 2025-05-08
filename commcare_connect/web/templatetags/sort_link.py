from urllib.parse import parse_qs, urlencode, urlparse

from django import template
from django.utils.html import format_html
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
def sortable_header(context, field, label, use_view_url=True):
    request = context["request"]
    current_sort = next_sort = None
    icon_element = '<i class="fa-solid ml-1 {}"></i>'

    if use_view_url:
        referer = request.headers.get("referer", request.get_full_path())
        parsed_url = urlparse(referer)
        query_params = parse_qs(parsed_url.query)
        path = parsed_url.path
        current_sort = query_params.get("sort", [""])[0]

    else:
        path = request.path
        query_params = request.GET.copy()
        current_sort = query_params.get("sort", "")

    if current_sort == field:
        next_sort = f"-{field}"
        icon_element = icon_element.format("fa-sort-asc text-brand-deep-purple")
    elif current_sort == f"-{field}":
        next_sort = ""
        icon_element = icon_element.format("fa-sort-desc text-brand-deep-purple")
    else:
        next_sort = field
        icon_element = icon_element.format("fa-sort text-gray-400")

    if next_sort:
        query_params["sort"] = next_sort
    else:
        query_params.pop("sort", None)

    query_string = urlencode(query_params, doseq=True)
    url = f"{path}?{query_string}" if query_string else path

    return format_html(
        '<a href="{}" class="flex items-center text-sm font-medium text-brand-deep-purple">{}</a>',
        url,
        mark_safe(f"{label} {icon_element}"),
    )
