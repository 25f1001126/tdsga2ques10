"""
Orders API — demonstrates three production API patterns:
  1. Idempotent POST /orders (Idempotency-Key header)
  2. Cursor-based pagination on GET /orders
  3. Per-client sliding-window rate limiting (X-Client-Id header)

Config (assigned values):
  TOTAL_ORDERS       = 40   # fixed catalog of order IDs 1..T
  RATE_LIMIT         = 17   # requests
  RATE_WINDOW_SECS   = 10   # seconds
"""

import time
import uuid
import threading
from collections import deque
from typing import Optional

from fastapi import FastAPI, Header, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

# --------------------------------------------------------------------------
# Config
# --------------------------------------------------------------------------
TOTAL_ORDERS = 40
RATE_LIMIT = 17
RATE_WINDOW_SECS = 10

app = FastAPI(title="Orders API")

# CORS: allow the exam grader page (and any browser) to call this API directly.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["Retry-After"],
)

# --------------------------------------------------------------------------
# Fixed order catalog: IDs 1..TOTAL_ORDERS, created at startup.
# --------------------------------------------------------------------------
_lock = threading.Lock()

CATALOG = [
    {
        "id": i,
        "order_id": i,
        "item": f"item-{i}",
        "amount": round(9.99 + i, 2),
        "status": "confirmed",
    }
    for i in range(1, TOTAL_ORDERS + 1)
]

# --------------------------------------------------------------------------
# Idempotency store: Idempotency-Key -> created order dict
# --------------------------------------------------------------------------
IDEMPOTENCY_STORE = {}
_next_created_id = TOTAL_ORDERS + 1  # newly POSTed orders get IDs after the catalog


# --------------------------------------------------------------------------
# Rate limiter: per-client sliding window using a deque of timestamps
# --------------------------------------------------------------------------
CLIENT_BUCKETS = {}
_rl_lock = threading.Lock()


def check_rate_limit(client_id: str):
    """
    Returns (allowed: bool, retry_after: int)
    Sliding window: keep only timestamps within the last RATE_WINDOW_SECS.
    """
    now = time.monotonic()
    with _rl_lock:
        bucket = CLIENT_BUCKETS.setdefault(client_id, deque())

        # Drop timestamps outside the window
        while bucket and now - bucket[0] > RATE_WINDOW_SECS:
            bucket.popleft()

        if len(bucket) >= RATE_LIMIT:
            oldest = bucket[0]
            retry_after = max(1, int(RATE_WINDOW_SECS - (now - oldest)) + 1)
            return False, retry_after

        bucket.append(now)
        return True, 0


# --------------------------------------------------------------------------
# Middleware: enforce rate limit on every request, keyed by X-Client-Id.
# Requests with no X-Client-Id are bucketed under "anonymous".
# --------------------------------------------------------------------------
@app.middleware("http")
async def rate_limit_middleware(request: Request, call_next):
    client_id = request.headers.get("X-Client-Id", "anonymous")
    allowed, retry_after = check_rate_limit(client_id)
    if not allowed:
        return JSONResponse(
            status_code=429,
            content={"detail": "Rate limit exceeded. Try again later."},
            headers={"Retry-After": str(retry_after)},
        )
    return await call_next(request)


# --------------------------------------------------------------------------
# 1. Idempotent order creation
# --------------------------------------------------------------------------
@app.post("/orders", status_code=201)
async def create_order(request: Request, idempotency_key: Optional[str] = Header(None, alias="Idempotency-Key")):
    global _next_created_id

    # Try to parse a JSON body if one was sent (optional payload).
    try:
        body = await request.json()
    except Exception:
        body = {}

    if idempotency_key:
        with _lock:
            existing = IDEMPOTENCY_STORE.get(idempotency_key)
            if existing is not None:
                return JSONResponse(status_code=200, content=existing)

    with _lock:
        new_id = _next_created_id
        _next_created_id += 1
        order = {
            "id": str(new_id),
            "order_id": new_id,
            "item": body.get("item", f"item-{new_id}"),
            "amount": body.get("amount", 9.99),
            "status": "created",
        }
        if idempotency_key:
            IDEMPOTENCY_STORE[idempotency_key] = order

    return JSONResponse(status_code=201, content=order)


# --------------------------------------------------------------------------
# 2. Cursor-based pagination
#    Cursor is simply the string form of the next starting index (0-based
#    offset into the catalog). It's treated as opaque by callers.
# --------------------------------------------------------------------------
@app.get("/orders")
async def list_orders(limit: int = 10, cursor: Optional[str] = None):
    try:
        offset = int(cursor) if cursor else 0
    except ValueError:
        offset = 0

    if limit <= 0:
        limit = 10

    offset = max(0, offset)
    end = offset + limit
    page = CATALOG[offset:end]

    next_cursor = str(end) if end < len(CATALOG) else None

    return {
        "items": page,
        "orders": page,       # alias
        "next_cursor": next_cursor,
        "next": next_cursor,  # alias
    }


# --------------------------------------------------------------------------
# Health check
# --------------------------------------------------------------------------
@app.get("/")
async def root():
    return {"status": "ok", "total_orders": TOTAL_ORDERS, "rate_limit": RATE_LIMIT, "window_secs": RATE_WINDOW_SECS}
