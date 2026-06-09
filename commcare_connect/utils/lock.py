from contextlib import contextmanager

import sentry_sdk
from django.core.cache import cache
from redis.exceptions import LockError


@contextmanager
def try_redis_lock(key: str, *, timeout: int = 10, blocking_timeout: float = 0, sleep: float = 0.1):
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
