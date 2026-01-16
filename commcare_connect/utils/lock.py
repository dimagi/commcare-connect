from contextlib import contextmanager

import sentry_sdk
from django.core.cache import cache
from redis.exceptions import LockError
from waffle import switch_is_active

from commcare_connect.flags.switch_names import CONCURRENT_SUBMISSIONS_LOCK


@contextmanager
def try_redis_lock(key: str, *, timeout: int = 10, blocking_timeout: float = 0, sleep: float = 0.1):
    if not switch_is_active(CONCURRENT_SUBMISSIONS_LOCK):
        yield True
        return

    lock = cache.lock(key, timeout=timeout, sleep=sleep)
    acquired = False
    try:
        acquired = lock.acquire(
            blocking=blocking_timeout > 0,
            blocking_timeout=blocking_timeout or None,
        )
        yield acquired
    except LockError:
        sentry_sdk.capture_message(message="Error in acquiring lock", level="error")
        yield False
    finally:
        if acquired:
            try:
                lock.release()
            except LockError:
                sentry_sdk.capture_message(message="Error while releasing lock", level="error")
