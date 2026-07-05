from fastapi import FastAPI, Request, Response
from starlette.middleware.base import BaseHTTPMiddleware
from collections import defaultdict, deque
import time
import uuid
import os

app = FastAPI()

# =========================
# Config
# =========================

ALLOWED_ORIGIN = "https://app-wg66hi.example.com"

# Must allow exam page origin (set explicitly or via env in real deployments)
EXAM_ORIGIN = os.getenv("EXAM_ORIGIN", "https://exam.example.com")

RATE_LIMIT_B = 12          # 12 requests
RATE_LIMIT_WINDOW = 10     # per 10 seconds

LOGIN_EMAIL = os.getenv("LOGIN_EMAIL", "user@example.com")


# =========================
# Middleware 1: Request Context
# =========================

class RequestContextMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        request_id = request.headers.get("X-Request-ID")

        if not request_id:
            request_id = str(uuid.uuid4())

        # store in request state
        request.state.request_id = request_id

        response = await call_next(request)

        # propagate
        response.headers["X-Request-ID"] = request_id
        return response


# =========================
# Middleware 2: Rate Limiter
# =========================

class RateLimitMiddleware(BaseHTTPMiddleware):
    def __init__(self, app):
        super().__init__(app)
        self.clients = defaultdict(deque)  # client_id -> timestamps

    async def dispatch(self, request: Request, call_next):
        client_id = request.headers.get("X-Client-Id", "anonymous")

        now = time.time()
        window_start = now - RATE_LIMIT_WINDOW

        q = self.clients[client_id]

        # remove old timestamps
        while q and q[0] < window_start:
            q.popleft()

        if len(q) >= RATE_LIMIT_B:
            return Response(
                content="Too Many Requests",
                status_code=429
            )

        q.append(now)

        return await call_next(request)


# =========================
# Middleware 3: Scoped CORS
# =========================

class ScopedCORSMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        origin = request.headers.get("origin")

        # Handle preflight
        if request.method == "OPTIONS":
            resp = Response(status_code=204)
        else:
            resp = await call_next(request)

        allowed = {ALLOWED_ORIGIN, EXAM_ORIGIN}

        if origin in allowed:
            resp.headers["Access-Control-Allow-Origin"] = origin
            resp.headers["Vary"] = "Origin"
            resp.headers["Access-Control-Allow-Methods"] = "GET, OPTIONS"
            resp.headers["Access-Control-Allow-Headers"] = "X-Client-Id, X-Request-ID, Content-Type"

        return resp


# =========================
# Register middleware (order matters)
# =========================

app.add_middleware(ScopedCORSMiddleware)
app.add_middleware(RateLimitMiddleware)
app.add_middleware(RequestContextMiddleware)


# =========================
# Endpoint
# =========================

@app.get("/ping")
async def ping(request: Request):
    return {
        "email": LOGIN_EMAIL,
        "request_id": request.state.request_id
    }
