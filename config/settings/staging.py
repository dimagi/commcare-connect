from .deploy_base import *  # noqa
from .deploy_base import env

# EMAIL
# ------------------------------------------------------------------------------
# Tells staging's error emails to ADMINS apart from production's
EMAIL_SUBJECT_PREFIX = env("DJANGO_EMAIL_SUBJECT_PREFIX", default="[Connect Staging] ")

# django-rest-framework
# -------------------------------------------------------------------------------
# Tools that generate code samples can use SERVERS to point to the correct domain
SPECTACULAR_SETTINGS["SERVERS"] = [  # noqa: F405
    {"url": "https://connect-staging.dimagi.com", "description": "Staging server"},
]
