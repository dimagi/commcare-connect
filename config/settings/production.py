from .deployed import *  # noqa
from .deployed import env

# GENERAL
# ------------------------------------------------------------------------------
# Hardcoded instead of read from DJANGO_DEBUG (as base.py does), so debug pages can never be switched on here
DEBUG = False

# SECURITY
# ------------------------------------------------------------------------------
# Defaults to 1 day
SECURE_HSTS_SECONDS = env.int("DJANGO_SECURE_HSTS_SECONDS", default=86400)

# django-rest-framework
# -------------------------------------------------------------------------------
# Tools that generate code samples can use SERVERS to point to the correct domain
SPECTACULAR_SETTINGS["SERVERS"] = [  # noqa: F405
    {"url": "https://connect.dimagi.com", "description": "Production server"},
]
