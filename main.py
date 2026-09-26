import logging
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse
from slowapi.errors import RateLimitExceeded
from slowapi import _rate_limit_exceeded_handler
from slowapi.middleware import SlowAPIMiddleware

from database import engine, Base
import models

from routers import auth, posts, notifications, users, admin
from ratelimit import limiter

# Create database tables
Base.metadata.create_all(bind=engine)

app = FastAPI(title="Jerry Agu's Dashboard API")

logger = logging.getLogger("office_board")

# --- Rate limiting (slowapi) -------------------------------------------------
# app.state.limiter is what every @limiter.limit(...) decorator (in
# routers/auth.py and routers/posts.py) and the RateLimitExceeded handler
# below look up at request time -- without this, the decorators exist but
# have nothing to check against. SlowAPIMiddleware adds the rate-limit
# response headers (X-RateLimit-*, Retry-After); it does not itself enforce
# limits, so it's safe alongside per-route decorators.
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
app.add_middleware(SlowAPIMiddleware)


# Any bug that slips through as an unhandled exception previously fell all
# the way to Starlette's default handler, which returns a PLAIN TEXT body
# ("Internal Server Error"). The frontend always tries to parse JSON and
# silently falls back to a blank "Something went wrong" when that fails --
# so a real backend bug looked identical to a network hiccup. This at least
# guarantees a real JSON body with a "detail" field, and logs the actual
# exception server-side so it's diagnosable instead of invisible.
#
# NOTE: this is a catch-all for Exception, but Starlette dispatches to the
# MOST SPECIFIC registered handler first, so the dedicated RateLimitExceeded
# handler registered above always wins for 429s -- this one only ever fires
# for genuinely unexpected errors.
@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    logger.exception("Unhandled error on %s %s", request.method, request.url.path)
    return JSONResponse(
        status_code=500,
        content={"detail": "Something went wrong on our end. Please try again."},
    )

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"

# --- Security headers middleware --------------------------------------------
# CSP: blocks XSS/malicious-script injection by only allowing same-origin
# scripts/styles. X-Frame-Options + frame-ancestors: blocks clickjacking
# (embedding the app in a hostile iframe). X-Content-Type-Options: stops
# MIME-sniffing attacks that reinterpret an uploaded file as executable
# script. Referrer-Policy: prevents leaking internal URLs/handles to
# third-party sites via the Referer header. Cache-Control: no-store on
# /api/* stops sensitive JSON (tokens, posts, notifications) from being
# cached by the browser or an intermediate proxy. HSTS forces HTTPS on
# subsequent visits, but only makes sense once the app is actually served
# over TLS, so it's only sent when the request itself arrived over https.
@app.middleware("http")
async def security_headers_middleware(request: Request, call_next):
    response = await call_next(request)
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; img-src 'self' data: blob:; media-src 'self'; "
        "style-src 'self' 'unsafe-inline'; script-src 'self'; "
        "connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; "
        "form-action 'self'"
    )
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    if request.url.scheme == "https":
        response.headers["Strict-Transport-Security"] = (
            "max-age=31536000; includeSubDomains"
        )
    if request.url.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store"
    return response

# API routers
app.include_router(auth.router, prefix="/api")
app.include_router(posts.router, prefix="/api")
app.include_router(notifications.router, prefix="/api")
app.include_router(users.router, prefix="/api")
app.include_router(admin.router, prefix="/api")

# Serve frontend static files
app.mount(
    "/static",
    StaticFiles(directory=str(STATIC_DIR)),
    name="static",
)

# Serve frontend
@app.get("/", include_in_schema=False)
def serve_frontend():
    return FileResponse(STATIC_DIR / "index.html")