from django import template
from django.urls import reverse
from django.utils.translation import gettext as _

register = template.Library()


@register.filter
def account_for_provider(accounts, provider_id):
    """Return the SocialAccount for ``provider_id``, or None.
    Django template dot-lookup only supports literal keys, so a variable-keyed
    lookup like this needs a filter rather than `{% get_social_accounts %}` +
    dict access (which would also mean a second, redundant query).
    """
    return next((account for account in accounts if account.provider == provider_id), None)


@register.simple_tag(takes_context=True)
def connections_breadcrumb_path(context):
    user = context["user"]
    return [
        {"title": user.name or user.email, "url": reverse("account_email")},
        {"title": _("Connected Accounts")},
    ]
