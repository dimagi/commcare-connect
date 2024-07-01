import pickle
from datetime import datetime

from django.db import transaction
from django_redis import get_redis_connection

from commcare_connect.events.models import Event
from config import celery_app

REDIS_EVENTS_QUEUE = "events_queue"


@celery_app.task
def process_events_batch():
    redis_conn = get_redis_connection("default")
    events = redis_conn.lrange(REDIS_EVENTS_QUEUE, 0, -1)
    if not events:
        return

    with transaction.atomic():
        event_objs = []
        for event in events:
            event_objs.append(pickle.loads(event))
        Event.objects.bulk_create(event_objs)

    redis_conn.ltrim(REDIS_EVENTS_QUEUE, len(events), -1)


def track_event(event_obj, use_async=True):
    event_obj.date_created = datetime.now()
    if use_async:
        redis_conn = get_redis_connection("default")
        serialized_event = pickle.dumps(event_obj)
        redis_conn.rpush(REDIS_EVENTS_QUEUE, serialized_event)
    else:
        event_obj.save()
