from django.utils.translation import gettext_lazy as _

# Placeholders for the HQ domain and app selects on the opportunity setup form. The form renders them first, and the
# views that refill the selects over htmx return them again, so the text stays the same after a swap.
DOMAIN_PLACEHOLDER_CHOICE = ("", _("Select an API key to load domains."))
APP_PLACEHOLDER_CHOICE = ("", _("Select an Application"))
APP_DOMAIN_PLACEHOLDER_CHOICE = ("", _("Select a Domain to load applications."))
