import pickle
from datetime import datetime

from django.db import transaction
from django_redis import get_redis_connection

from commcare_connect.events.models import Event
from config import celery_app

REDIS_EVENTS_QUEUE = "events_queue"


class EventQueue:
    def __init__(self):
        self.redis_conn = get_redis_connection("default")

    def push(self, event_obj):
        serialized_event = pickle.dumps(event_obj)
        self.redis_conn.rpush(REDIS_EVENTS_QUEUE, serialized_event)

    def pop(self):
        events = [pickle.loads(event) for event in self.redis_conn.lrange(REDIS_EVENTS_QUEUE, 0, -1)]
        self.redis_conn.ltrim(REDIS_EVENTS_QUEUE, len(events), -1)
        return events


@celery_app.task
def process_events_batch():
    event_queue = EventQueue()
    events = event_queue.pop()
    if not events:
        return
    try:
        with transaction.atomic():
            Event.objects.bulk_create(events)
    except Exception as e:
        for event in events:
            event_queue.push(event)
        raise e


def track_event(event_obj, use_async=True):
    event_obj.date_created = datetime.utcnow()
    if use_async:
        EventQueue().push(event_obj)
    else:
        event_obj.save()
