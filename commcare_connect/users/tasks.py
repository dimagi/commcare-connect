from django.contrib.auth import get_user_model
from django.core.management import call_command

from config import celery_app

User = get_user_model()


@celery_app.task()
def get_users_count():
    """A pointless Celery task to demonstrate usage."""
    return User.objects.count()


@celery_app.task()
def clear_expired_oauth_tokens():
    # https://django-oauth-toolkit.readthedocs.io/en/latest/management_commands.html
    call_command("cleartokens")
