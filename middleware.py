"""
Custom Starlette/FastAPI middlewares:

1. RequestContextMiddleware
   - Reuses inbound `X-Request-ID` header if present, otherwise mints a UUID4.
   - Stores it on `request.state.request_id` for handlers to read.
   - Always echoes it back as the `X-Request-ID` response header.

2. RateLimitMiddleware
   - Buckets requests by the `X-Client-Id` header (falls back to the
     connecting IP if the header is absent, so the service still degrades
     gracefully rather than crashing).
   - Sliding-window counter: allows `max_requests` per `window_seconds`
     per client id. The (B+1)th request inside the window gets HTTP 429.
   - In-memory only -> fine for a single-process deployment / this exercise.
     For multi-worker/production use, swap the dict for Redis (INCR + TTL,
     or a sorted set for a real sliding log) so all workers share state.
"""

import time
import threading
import uuid
from collections import deque

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse


class RequestContextMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        incoming_id = request.headers.get("x-request-id")
        request_id = incoming_id.strip() if incoming_id and incoming_id.strip() else str(uuid.uuid4())

        request.state.request_id = request_id

        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        return response


class RateLimitMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, max_requests: int, window_seconds: float):
        super().__init__(app)
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self._buckets: dict[str, deque] = {}
        self._lock = threading.Lock()

    async def dispatch(self, request: Request, call_next):
        client_id = request.headers.get("x-client-id")
        if not client_id:
            client_id = request.client.host if request.client else "anonymous"

        now = time.monotonic()

        with self._lock:
            bucket = self._buckets.setdefault(client_id, deque())

            # Evict timestamps that have aged out of the window.
            while bucket and (now - bucket[0]) > self.window_seconds:
                bucket.popleft()

            if len(bucket) >= self.max_requests:
                retry_after = max(self.window_seconds - (now - bucket[0]), 0)
                request_id = getattr(request.state, "request_id", None) or str(uuid.uuid4())
                response = JSONResponse(
                    {
                        "detail": "rate limit exceeded",
                        "client_id": client_id,
                        "limit": self.max_requests,
                        "window_seconds": self.window_seconds,
                    },
                    status_code=429,
                    headers={
                        "Retry-After": str(int(retry_after) + 1),
                        "X-Request-ID": request_id,
                    },
                )
                return response

            bucket.append(now)

        return await call_next(request)
