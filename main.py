import time
import threading
from collections import deque
from typing import Optional

from fastapi import FastAPI, Header, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

TOTAL_ORDERS = 59
RATE_LIMIT = 16
RATE_WINDOW_SECS = 10

app = FastAPI(title="Orders API")

app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=".*",
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["Retry-After"],
)
# -----------------------------
# Fixed catalog
# -----------------------------

CATALOG = [
    {
        "id": i,
        "item": f"item-{i}",
        "amount": round(9.99 + i, 2),
        "status": "confirmed",
    }
    for i in range(1, TOTAL_ORDERS + 1)
]

# -----------------------------
# Idempotency storage
# -----------------------------

store_lock = threading.Lock()
IDEMPOTENCY = {}
next_id = TOTAL_ORDERS + 1

# -----------------------------
# Rate limiting
# -----------------------------

rate_lock = threading.Lock()
CLIENTS = {}


def allowed(client_id: str):
    now = time.time()

    with rate_lock:
        bucket = CLIENTS.setdefault(client_id, deque())

        while bucket and now - bucket[0] >= RATE_WINDOW_SECS:
            bucket.popleft()

        if len(bucket) >= RATE_LIMIT:
            retry = max(1, int(RATE_WINDOW_SECS - (now - bucket[0])) + 1)
            return False, retry

        bucket.append(now)
        return True, 0


@app.middleware("http")
async def rate_limit(request: Request, call_next):
    client = request.headers.get("X-Client-Id", "anonymous")

    ok, retry = allowed(client)

    if not ok:
        return JSONResponse(
            status_code=429,
            headers={"Retry-After": str(retry)},
            content={"detail": "Rate limit exceeded"},
        )

    return await call_next(request)


# -----------------------------
# POST /orders
# -----------------------------

@app.post("/orders")
async def create_order(
    request: Request,
    idempotency_key: Optional[str] = Header(None, alias="Idempotency-Key"),
):
    global next_id

    try:
        body = await request.json()
    except Exception:
        body = {}

    if idempotency_key:
        with store_lock:
            if idempotency_key in IDEMPOTENCY:
                return JSONResponse(
                    status_code=201,
                    content=IDEMPOTENCY[idempotency_key],
                )

    with store_lock:
        order = {
            "id": str(next_id),
            "item": body.get("item", f"item-{next_id}"),
            "amount": body.get("amount", 9.99),
            "status": "created",
        }

        next_id += 1

        if idempotency_key:
            IDEMPOTENCY[idempotency_key] = order

    return JSONResponse(status_code=201, content=order)


# -----------------------------
# GET /orders
# -----------------------------

@app.get("/orders")
async def list_orders(limit: int = 10, cursor: Optional[str] = None):
    try:
        start = int(cursor) if cursor else 0
    except ValueError:
        start = 0

    if limit < 1:
        limit = 1

    end = min(start + limit, TOTAL_ORDERS)

    items = CATALOG[start:end]

    next_cursor = str(end) if end < TOTAL_ORDERS else None

    return {
        "items": items,
        "orders": items,
        "next_cursor": next_cursor,
        "next": next_cursor,
    }


@app.get("/")
def root():
    return {
        "status": "ok",
        "total_orders": TOTAL_ORDERS,
        "rate_limit": RATE_LIMIT,
    }
