"""HTTP retry / backoff helpers and failure classification for live validation."""

from __future__ import annotations

import time
from typing import Any

import httpx


def is_retryable_status(status_code: int) -> bool:
    return status_code in (429, 500, 502, 503, 504)


def classify_http_failure(
    *,
    status_code: int | None = None,
    exc: BaseException | None = None,
) -> str:
    """
    Classify a failed HTTP interaction.

    Returns one of: ``retryable``, ``blocked``, ``hard_fail``.
    """
    if exc is not None:
        if isinstance(
            exc,
            (
                httpx.TimeoutException,
                httpx.ConnectError,
                httpx.RemoteProtocolError,
            ),
        ):
            return "retryable"

    if status_code is not None:
        if status_code in (403, 451):
            return "blocked"
        if is_retryable_status(status_code):
            return "retryable"
        if status_code in (404, 400, 422):
            return "hard_fail"
        if status_code >= 500:
            return "retryable"

    return "hard_fail"


def classify_retry_exhaustion(
    failure_class: str,
    *,
    attempts_used: int,
    max_attempts: int,
) -> str:
    """After retries, upgrade exhausted retryable dependency failures to blocked."""
    if failure_class == "retryable" and attempts_used >= max_attempts:
        return "blocked"
    return failure_class


def request_json_with_retry(
    client: httpx.Client,
    method: str,
    url: str,
    *,
    max_attempts: int = 3,
    backoff_base_s: float = 0.5,
    **kwargs: Any,
) -> httpx.Response:
    """
    Perform an HTTP request; retry on retryable failures with exponential backoff.

    Raises ``httpx.HTTPStatusError`` on non-retryable HTTP errors or when retries are exhausted.
    """
    last_response: httpx.Response | None = None
    for attempt in range(1, max_attempts + 1):
        try:
            resp = client.request(method, url, **kwargs)
        except (
            httpx.TimeoutException,
            httpx.ConnectError,
            httpx.RemoteProtocolError,
            httpx.ReadError,
        ) as exc:
            if classify_http_failure(exc=exc) == "retryable" and attempt < max_attempts:
                time.sleep(backoff_base_s * (2 ** (attempt - 1)))
                continue
            raise

        if resp.status_code < 400:
            return resp

        fc = classify_http_failure(status_code=resp.status_code)
        if fc == "retryable" and attempt < max_attempts:
            time.sleep(backoff_base_s * (2 ** (attempt - 1)))
            last_response = resp
            continue

        resp.raise_for_status()

    if last_response is not None:
        last_response.raise_for_status()
    raise RuntimeError("request_json_with_retry: exhausted without response")
