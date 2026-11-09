from django.contrib.staticfiles.apps import StaticFilesConfig as BaseStaticFilesConfig


class StaticFilesConfig(BaseStaticFilesConfig):
    # Third-party static we never serve, kept out of collectstatic so it isn't published in deployed builds.
    # DRF's is the Browsable API's Bootstrap 3 and jQuery (that renderer is local-only);
    # django-tables2's is Bootstrap and theme CSS (our tables use our own templates).
    ignore_patterns = BaseStaticFilesConfig.ignore_patterns + ["rest_framework/*", "django_tables2/*"]
