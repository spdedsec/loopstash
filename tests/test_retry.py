"""Tests for retry logic, exponential backoff, jitter, and error classification."""

from __future__ import annotations

import errno

import pytest

from loopstash.backends.base import (
    AuthenticationRequiredError,
    BackendError,
    BackendNotFoundError,
    DownloadError,
    NetworkError,
    ProfileNotFoundError,
    RateLimitError,
)
from loopstash.retry import RetryPolicy, calculate_backoff_delay, is_retriable_error


def test_calculate_backoff_delay():
    """Test delay computation with exponential backoff and jitter limits."""
    # Without jitter
    d1 = calculate_backoff_delay(1, base_delay=1.0, max_delay=60.0, jitter=False)
    assert d1 == 1.0

    d2 = calculate_backoff_delay(2, base_delay=1.0, max_delay=60.0, jitter=False)
    assert d2 == 2.0

    d3 = calculate_backoff_delay(3, base_delay=1.0, max_delay=60.0, jitter=False)
    assert d3 == 4.0

    # Max delay clamp
    d_max = calculate_backoff_delay(10, base_delay=1.0, max_delay=30.0, jitter=False)
    assert d_max == 30.0

    # With jitter
    for attempt in range(1, 5):
        dj = calculate_backoff_delay(attempt, base_delay=1.0, max_delay=60.0, jitter=True)
        nominal = 1.0 * (2 ** (attempt - 1))
        # dj should be between nominal and nominal + max(5.0, nominal * 0.5)
        assert dj >= nominal


def test_is_retriable_error_classification():
    """Test classification of transient vs permanent errors."""
    # Retriable
    assert is_retriable_error(NetworkError("Timeout")) is True
    assert is_retriable_error(DownloadError("Stream dropped")) is True
    assert is_retriable_error(TimeoutError("Socket timeout")) is True
    assert is_retriable_error(ConnectionError("Connection refused")) is True
    assert is_retriable_error(RateLimitError("429")) is True
    assert is_retriable_error(BackendError("Transient server glitch", retriable=True)) is True

    # Non-retriable permanent errors
    assert is_retriable_error(ProfileNotFoundError("No profile")) is False
    assert is_retriable_error(AuthenticationRequiredError("Login needed")) is False
    assert is_retriable_error(BackendNotFoundError("gallery-dl missing")) is False
    assert is_retriable_error(PermissionError("Access denied")) is False
    assert is_retriable_error(FileNotFoundError("Path missing")) is False
    assert is_retriable_error(BackendError("Fatal backend error", retriable=False)) is False

    # Disk full error (ENOSPC)
    disk_full_err = OSError("Disk full")
    disk_full_err.errno = errno.ENOSPC
    assert is_retriable_error(disk_full_err) is False


def test_retry_policy_success():
    """Test that retry policy returns result immediately on first try."""
    policy = RetryPolicy(max_retries=3)
    calls = 0

    def work():
        nonlocal calls
        calls += 1
        return "success"

    res = policy.execute(work)
    assert res == "success"
    assert calls == 1


def test_retry_policy_retries_and_succeeds():
    """Test that retry policy retries transient errors and succeeds when error clears."""
    delays = []

    def mock_sleep(d: float):
        delays.append(d)

    policy = RetryPolicy(max_retries=4, base_delay=0.5, sleep_func=mock_sleep)
    calls = 0

    def flaky_work():
        nonlocal calls
        calls += 1
        if calls < 3:
            raise NetworkError(f"Transient error on call {calls}")
        return "recovered"

    retries_recorded = []

    def on_retry(att, err, delay):
        retries_recorded.append((att, str(err), delay))

    res = policy.execute(flaky_work, on_retry=on_retry)
    assert res == "recovered"
    assert calls == 3
    assert len(delays) == 2
    assert len(retries_recorded) == 2
    assert retries_recorded[0][0] == 1
    assert retries_recorded[1][0] == 2


def test_retry_policy_exhaustion():
    """Test that exhausting max_retries raises the final exception."""
    delays = []
    policy = RetryPolicy(max_retries=2, base_delay=0.1, sleep_func=lambda d: delays.append(d))

    calls = 0

    def persistent_failure():
        nonlocal calls
        calls += 1
        raise NetworkError("Downloader disconnected")

    with pytest.raises(NetworkError, match="Downloader disconnected"):
        policy.execute(persistent_failure)

    assert calls == 3  # Initial try + 2 retries
    assert len(delays) == 2


def test_retry_policy_non_retriable_fails_fast():
    """Test that non-retriable exceptions fail immediately without retrying."""
    delays = []
    policy = RetryPolicy(max_retries=5, sleep_func=lambda d: delays.append(d))

    calls = 0

    def auth_failure():
        nonlocal calls
        calls += 1
        raise AuthenticationRequiredError("Private account")

    with pytest.raises(AuthenticationRequiredError):
        policy.execute(auth_failure)

    assert calls == 1
    assert len(delays) == 0


def test_retry_policy_rate_limit_retry_after():
    """Test that RateLimitError with retry_after header respects the specified delay."""
    delays = []
    policy = RetryPolicy(max_retries=2, max_delay=60.0, sleep_func=lambda d: delays.append(d))

    calls = 0

    def throttled_call():
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RateLimitError("Throttled by Instagram", retry_after=12)
        return "allowed"

    res = policy.execute(throttled_call)
    assert res == "allowed"
    assert calls == 2
    assert len(delays) == 1
    assert delays[0] == 12.0
