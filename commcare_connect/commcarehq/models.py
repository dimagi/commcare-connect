from django.conf import settings
from django.db import models


class HQServer(models.Model):
    name = models.CharField(max_length=255)
    url = models.URLField(unique=True)
    date_created = models.DateTimeField(auto_now_add=True)
    date_modified = models.DateTimeField(auto_now=True)
    oauth_application = models.ForeignKey(settings.OAUTH2_PROVIDER_APPLICATION_MODEL, on_delete=models.DO_NOTHING)

    def __str__(self):
        return f"{self.name} ({self.url})"
