"""Bounded retries for read-only local inference; never log request/response bodies."""
import random
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

import httpx

TRANSIENT_STATUS = {429, 502, 503, 504}
MAX_ATTEMPTS = 3
MAX_DELAY_SECONDS = 30


def retry_after_seconds(value):
    if value is None:
        return None
    try:
        return max(0, float(value))
    except (ValueError, TypeError):
        try:
            date = parsedate_to_datetime(value)
            if date.tzinfo is None:
                date = date.replace(tzinfo=timezone.utc)
            return max(0, (date - datetime.now(timezone.utc)).total_seconds())
        except (ValueError, TypeError, OverflowError):
            return None


def inference_call(call):
    """Three attempts, at most two bounded sleeps; invalid output is not retried.

    The caller validates/uses the response only after this function returns.
    Retry-After above our wait budget fails immediately rather than retrying early.
    """
    for attempt in range(MAX_ATTEMPTS):
        try:
            return call()
        except (httpx.TimeoutException, httpx.ConnectError, httpx.HTTPStatusError) as error:
            retry_after = None
            if isinstance(error, httpx.HTTPStatusError):
                if error.response.status_code not in TRANSIENT_STATUS:
                    raise
                retry_after = retry_after_seconds(error.response.headers.get('Retry-After'))
                if retry_after is not None and (not retry_after < float('inf') or retry_after > MAX_DELAY_SECONDS):
                    raise
            if attempt + 1 == MAX_ATTEMPTS:
                raise
            delay = max(2 ** attempt, retry_after or 0) + random.uniform(0, .5)
            time.sleep(min(MAX_DELAY_SECONDS, delay))
