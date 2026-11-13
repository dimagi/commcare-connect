"""CommCare HQ apps and API keys that opportunities are built on."""

from django.db import models

from commcare_connect.commcarehq.models import HQServer
from commcare_connect.organization.models import Organization
from commcare_connect.users.models import User
from commcare_connect.utils.db import BaseModel


class CommCareApp(BaseModel):
    organization = models.ForeignKey(
        Organization,
        on_delete=models.CASCADE,
        related_name="apps",
        related_query_name="app",
    )
    cc_domain = models.CharField(max_length=255)
    cc_app_id = models.CharField(max_length=50)
    name = models.CharField(max_length=255)
    description = models.TextField()
    passing_score = models.IntegerField(null=True)
    hq_server = models.ForeignKey(HQServer, on_delete=models.DO_NOTHING, null=True)

    def __str__(self):
        return self.name

    @property
    def url(self):
        return f"{self.hq_server.url}/a/{self.cc_domain}/apps/view/{self.cc_app_id}"


class HQApiKey(models.Model):
    api_key = models.CharField(max_length=50, unique=True)
    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
    )
    hq_server = models.ForeignKey(HQServer, on_delete=models.DO_NOTHING, null=True)
    date_created = models.DateTimeField(auto_now_add=True)
