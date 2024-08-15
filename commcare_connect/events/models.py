from datetime import datetime

from django.db import models

from commcare_connect.cache import quickcache
from commcare_connect.users.models import User

from . import types


class Event(models.Model):
    from commcare_connect.opportunity.models import Opportunity

    # this allows referring to event types in this style: Event.Type.INVITE_SENT
    Type = types

    date_created = models.DateTimeField(db_index=True)
    event_type = models.CharField(max_length=40, db_index=True)
    user = models.ForeignKey(User, on_delete=models.CASCADE, null=True)
    opportunity = models.ForeignKey(Opportunity, on_delete=models.PROTECT, null=True)
    metadata = models.JSONField(default=dict)

    @classmethod
    @quickcache([], timeout=60 * 60)
    def get_all_event_types(cls):
        return set(cls.objects.values_list("event_type", flat=True).distinct()) | set(types.EVENT_TYPES)

    def save(self, *args, **kwargs):
        if not self.date_created:
            self.date_created = datetime.utcnow()
        super().save(*args, **kwargs)
