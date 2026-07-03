"""
Minimal in-memory rate limiter — keeps Railway costs predictable by capping
how many times a single visitor can hit the expensive endpoints (file parsing,
QTI generation), independent of cookies (which can be cleared) or license
status.

This resets whenever the service redeploys/restarts, which is fine for this
use case — the goal is smoothing out abuse spikes, not perfect accounting.
If you outgrow a single Railway instance, swap this for Redis-backed limiting.
"""
import time
from collections import defaultdict, deque
from fastapi import Request, HTTPException

_buckets = defaultdict(deque)


def _client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def rate_limit(name: str, max_requests: int, window_seconds: int):
    """FastAPI dependency factory: Depends(rate_limit("parse", 15, 3600))"""

    async def checker(request: Request):
        ip = _client_ip(request)
        key = f"{name}:{ip}"
        now = time.time()
        bucket = _buckets[key]
        while bucket and bucket[0] < now - window_seconds:
            bucket.popleft()
        if len(bucket) >= max_requests:
            raise HTTPException(
                429,
                f"Too many requests. Please wait a bit before trying again "
                f"(limit: {max_requests} per {window_seconds // 60} minutes).",
            )
        bucket.append(now)

    return checker
