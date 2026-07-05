import os

from fastapi import FastAPI, Request
from starlette.middleware.cors import CORSMiddleware

from app.middleware import RequestContextMiddleware, RateLimitMiddleware

# --------------------------------------------------------------------------
# Config
# --------------------------------------------------------------------------

ASSIGNED_ORIGIN = "https://app-wg66hi.example.com"

# The exam/grader page origin is NOT known at build time -> set it via env var
# when you deploy, e.g.  EXAM_ORIGIN=https://grader.example.org
EXAM_ORIGIN = os.getenv("EXAM_ORIGIN", "").strip()

# Comma-separated list for any additional origins you need to allow.
EXTRA_ALLOWED_ORIGINS = [
    o.strip() for o in os.getenv("EXTRA_ALLOWED_ORIGINS", "").split(",") if o.strip()
]

ALLOWED_ORIGINS = [ASSIGNED_ORIGIN]
if EXAM_ORIGIN:
    ALLOWED_ORIGINS.append(EXAM_ORIGIN)
ALLOWED_ORIGINS.extend(EXTRA_ALLOWED_ORIGINS)
# De-dupe while preserving order.
ALLOWED_ORIGINS = list(dict.fromkeys(ALLOWED_ORIGINS))

RATE_LIMIT_MAX = int(os.getenv("RATE_LIMIT_MAX", "12"))       # B = 12 requests
RATE_LIMIT_WINDOW = float(os.getenv("RATE_LIMIT_WINDOW", "10"))  # per 10 seconds

SERVICE_EMAIL = os.getenv("SERVICE_EMAIL", "you@example.com")  # <-- set this to your real address

# --------------------------------------------------------------------------
# App + middleware stack
# --------------------------------------------------------------------------

app = FastAPI(title="ping-service")

# IMPORTANT: Starlette wraps middleware so that the LAST one added via
# add_middleware() ends up OUTERMOST (it runs first on the way in, last on
# the way out). We want:
#
#   request  -> CORS -> RequestContext -> RateLimit -> route handler
#   response <- CORS <- RequestContext <- RateLimit <- route handler
#
# so CORS can short-circuit preflight OPTIONS requests before anything else
# runs, and every response (including 429s) still carries X-Request-ID and,
# if applicable, the CORS headers. To get that order we add innermost first:

app.add_middleware(
    RateLimitMiddleware,
    max_requests=RATE_LIMIT_MAX,
    window_seconds=RATE_LIMIT_WINDOW,
)
app.add_middleware(RequestContextMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,   # explicit list -> never "*"
    allow_credentials=False,
    allow_methods=["GET", "OPTIONS"],
    allow_headers=["*", "X-Client-Id", "X-Request-ID"],
    expose_headers=["X-Request-ID"],  # so browser JS can read it cross-origin
)


# --------------------------------------------------------------------------
# Route
# --------------------------------------------------------------------------

@app.get("/ping")
async def ping(request: Request):
    return {
        "email": SERVICE_EMAIL,
        "request_id": request.state.request_id,
    }
