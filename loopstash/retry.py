"""Retry and recovery engine with exponential backoff and jitter."""

from __future__ import annotations

import logging
import random
import time
from typing import Any, Callable, Optional, TypeVar

from loopstash.backends.base import (
    AuthenticationRequiredError,
    BackendError,
    BackendNotFoundError,
    DownloadError,
    NetworkError,
    ProfileNotFoundError,
    RateLimitError,
)

logger = logging.getLogger(__name__)

T = TypeVar("T")


def calculate_backoff_delay(
    attempt: int,
    base_delay: float = 1.0,
    max_delay: float = 60.0,
    jitter: bool = True,
) -> float:
    """Calculate exponential backoff delay with random jitter.

    Formula: min(max_delay, base_delay * 2^(attempt - 1)) + jitter
    """
    exponent = max(0, attempt - 1)
    delay = min(max_delay, base_delay * (2**exponent))
    if jitter:
        # Full jitter between 0 and 0.5 * delay
        jitter_val = random.uniform(0, min(5.0, delay * 0.5))
        delay += jitter_val
    return round(delay, 2)


def is_retriable_error(error: Exception) -> bool:
    """Classify whether an exception is safe to retry."""
    # Permanent errors - NEVER retry
    if isinstance(
        error,
        (
            ProfileNotFoundError,
            AuthenticationRequiredError,
            BackendNotFoundError,
            PermissionError,
            FileNotFoundError,
        ),
    ):
        return False

    # Disk full is non-retriable without user action
    if isinstance(error, OSError) and getattr(error, "errno", None) in (28,):  # ENOSPC
        return False

    # Explicit backend non-retriable flag
    if isinstance(error, BackendError) and not error.retriable:
        return False

    # Retriable errors
    if isinstance(error, (NetworkError, DownloadError, TimeoutError, ConnectionError)):
        return True

    # Rate limits are retriable only with careful spacing
    if isinstance(error, RateLimitError):
        return True

    return True


class RetryPolicy:
    """Executes actions with configurable bounded retries and backoff."""

    def __init__(
        self,
        max_retries: int = 5,
        base_delay: float = 1.0,
        max_delay: float = 60.0,
        sleep_func: Callable[[float], None] = time.sleep,
    ):
        self.max_retries = max(0, max_retries)
        self.base_delay = base_delay
        self.max_delay = max_delay
        self.sleep_func = sleep_func

    def execute(
        self,
        func: Callable[..., T],
        *args: Any,
        on_retry: Optional[Callable[[int, Exception, float], None]] = None,
        **kwargs: Any,
    ) -> T:
        """Execute callable with bounded retries."""
        attempt = 1
        while True:
            try:
                return func(*args, **kwargs)
            except Exception as e:
                if not is_retriable_error(e):
                    logger.debug("Non-retriable error encountered: %s", e)
                    raise

                if attempt > self.max_retries:
                    logger.warning("Max retries (%d) exhausted for: %s", self.max_retries, e)
                    raise

                # Determine backoff delay
                if isinstance(e, RateLimitError) and e.retry_after:
                    delay = float(min(e.retry_after, self.max_delay))
                else:
                    delay = calculate_backoff_delay(
                        attempt=attempt,
                        base_delay=self.base_delay,
                        max_delay=self.max_delay,
                    )

                if on_retry:
                    try:
                        on_retry(attempt, e, delay)
                    except Exception as cb_err:
                        logger.debug("on_retry callback error: %s", cb_err)

                logger.info(
                    "Attempt %d/%d failed with %s: %s. Retrying in %.2fs...",
                    attempt,
                    self.max_retries,
                    type(e).__name__,
                    e,
                    delay,
                )
                self.sleep_func(delay)
                attempt += 1
