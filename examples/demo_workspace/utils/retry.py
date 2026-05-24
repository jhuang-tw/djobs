"""Retry-with-backoff utility and decorator for transient failure handling."""

import time
import random
from typing import Callable, TypeVar

T = TypeVar("T")

class RetryExhausted(Exception):
    pass

def retry_with_backoff(
    fn: Callable[[], T],
    max_attempts: int = 3,
    base_delay: float = 1.0,
    max_delay: float = 30.0,
    jitter: bool = True,
) -> T:
    last_error = None
    for attempt in range(1, max_attempts + 1):
        try:
            return fn()
        except Exception as e:
            last_error = e
            if attempt == max_attempts:
                break
            delay = min(base_delay * (2 ** (attempt - 1)), max_delay)
            if jitter:
                delay *= random.uniform(0.5, 1.5)
            time.sleep(delay)
    raise RetryExhausted(f"Failed after {max_attempts} attempts") from last_error

def retry_decorator(max_attempts: int = 3, base_delay: float = 1.0):
    def decorator(fn):
        def wrapper(*args, **kwargs):
            return retry_with_backoff(
                lambda: fn(*args, **kwargs),
                max_attempts=max_attempts,
                base_delay=base_delay,
            )
        return wrapper
    return decorator
