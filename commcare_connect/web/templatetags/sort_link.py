from django import template
from django.utils.html import format_html

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
