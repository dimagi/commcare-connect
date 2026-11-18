"""Django settings for demo recordings, layered on local.py.
Loaded via PYTHONPATH + DJANGO_SETTINGS_MODULE=record_settings.

- DEBUG off → no Debug Toolbar in frame (local.py hardcodes DEBUG = True, so the env var alone does nothing).
- Email always goes to the console (output/server.log), even if .env points DJANGO_EMAIL_BACKEND at a real SMTP
  server — a demo must never send mail to the seeded addresses. Demos do not show email on camera.
- Celery eager even when .env sets CELERY_TASK_ALWAYS_EAGER=False, so emails sent via tasks fire inline
  without a worker.
- Static files come straight from the source folders, as they do under DEBUG: no collectstatic, and pages always
  get the current webpack build. Manifest storage would 500 on every page without staticfiles/staticfiles.json
  (a fresh checkout or worktree) and serve stale bundles from an old one. `runserver --insecure` can't do this:
  `whitenoise.runserver_nostatic` turns runserver's static serving off.
"""

from config.settings.local import *  # noqa: F403

DEBUG = False

EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"

CELERY_TASK_ALWAYS_EAGER = True

STORAGES = {**STORAGES, "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"}}  # noqa: F405
WHITENOISE_USE_FINDERS = True
WHITENOISE_AUTOREFRESH = True
